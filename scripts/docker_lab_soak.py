#!/usr/bin/env python3
"""Measure the running synthetic lab without changing application behavior."""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


SERVICES = ("simulator", "collector", "app")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def command(*args: str) -> str:
    result = subprocess.run(
        args,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def containers() -> dict[str, str]:
    found: dict[str, str] = {}
    for service in SERVICES:
        container_id = command("docker", "compose", "ps", "-q", service)
        if not container_id:
            raise RuntimeError(f"Docker service is not running: {service}")
        found[service] = container_id
    return found


def parse_bytes(value: str) -> float:
    text = value.strip().replace("iB", "B")
    multipliers = {
        "B": 1,
        "kB": 1000,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }
    for suffix in sorted(multipliers, key=len, reverse=True):
        if text.endswith(suffix):
            return float(text[: -len(suffix)].strip()) * multipliers[suffix]
    raise ValueError(f"Unsupported memory value: {value}")


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def slope_per_hour(hours: list[float], values: list[float]) -> float:
    if len(hours) < 2:
        return 0.0
    mean_x = statistics.fmean(hours)
    mean_y = statistics.fmean(values)
    denominator = sum((value - mean_x) ** 2 for value in hours)
    if denominator == 0:
        return 0.0
    numerator = sum(
        (x_value - mean_x) * (y_value - mean_y)
        for x_value, y_value in zip(hours, values, strict=True)
    )
    return numerator / denominator


class AppProbe:
    def __init__(self, password: str) -> None:
        jar = http.cookiejar.CookieJar()
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))
        data = urlencode({"username": "lab-admin", "password": password}).encode()
        with self.opener.open(Request("http://127.0.0.1:5067/", data=data), timeout=10):
            pass

    def json(self, path: str) -> Any:
        request = Request(
            f"http://127.0.0.1:5067{path}",
            headers={"Accept": "application/json"},
        )
        with self.opener.open(request, timeout=10) as response:
            return json.load(response)

    def sample(self) -> dict[str, Any]:
        health = self.json("/healthz")
        snapshot = self.json("/api/sensors")
        collector = snapshot.get("collector") or {}
        mtime_text = str(health.get("data_file_mtime") or "")
        mtime = datetime.fromisoformat(mtime_text)
        if mtime.tzinfo is None:
            mtime = mtime.replace(tzinfo=timezone.utc)
        snapshot_age = max(
            0.0,
            (datetime.now(timezone.utc) - mtime.astimezone(timezone.utc)).total_seconds(),
        )
        return {
            "app_ok": health.get("ok"),
            "app_servers": health.get("servers"),
            "data_file_exists": health.get("data_file_exists"),
            "snapshot_age_seconds": round(snapshot_age, 3),
            "sensors": len(snapshot.get("sensores") or []),
            "servers_configured": collector.get("servers_configured"),
            "collector_errors": collector.get("servers_with_error"),
            "snapshot_timestamp": snapshot.get("ultima_actualizacion"),
        }


def runtime_state(container_ids: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for service, container_id in container_ids.items():
        inspected = json.loads(command("docker", "inspect", container_id))[0]
        state = inspected["State"]
        result[service] = {
            "running": state.get("Running"),
            "health": (state.get("Health") or {}).get("Status"),
            "restart_count": inspected.get("RestartCount", 0),
            "oom_killed": state.get("OOMKilled", False),
        }
    return result


def resource_sample(container_ids: dict[str, str]) -> dict[str, Any]:
    reverse = {container_id: service for service, container_id in container_ids.items()}
    output = command(
        "docker",
        "stats",
        "--no-stream",
        "--format",
        "{{json .}}",
        *container_ids.values(),
    )
    observed: dict[str, Any] = {}
    for line in output.splitlines():
        item = json.loads(line)
        container_id = str(item.get("ID") or "")
        service = next(
            (name for identifier, name in reverse.items() if identifier.startswith(container_id)),
            None,
        )
        if service is None:
            continue
        memory_used = str(item["MemUsage"]).split("/", 1)[0].strip()
        observed[service] = {
            "cpu_pct": float(str(item["CPUPerc"]).rstrip("%")),
            "memory_mib": parse_bytes(memory_used) / (1024**2),
            "pids": int(item.get("PIDs") or 0),
        }
    if set(observed) != set(SERVICES):
        raise RuntimeError("docker stats did not report every laboratory service")
    return observed


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    started = datetime.fromisoformat(samples[0]["timestamp"].replace("Z", "+00:00"))
    hours = [
        (
            datetime.fromisoformat(sample["timestamp"].replace("Z", "+00:00"))
            - started
        ).total_seconds()
        / 3600
        for sample in samples
    ]
    for service in SERVICES:
        cpu = [float(sample["resources"][service]["cpu_pct"]) for sample in samples]
        memory = [float(sample["resources"][service]["memory_mib"]) for sample in samples]
        result[service] = {
            "cpu_pct": {
                "mean": round(statistics.fmean(cpu), 3),
                "p95": round(percentile(cpu, 0.95), 3),
                "max": round(max(cpu), 3),
            },
            "memory_mib": {
                "first": round(memory[0], 3),
                "last": round(memory[-1], 3),
                "delta": round(memory[-1] - memory[0], 3),
                "mean": round(statistics.fmean(memory), 3),
                "p95": round(percentile(memory, 0.95), 3),
                "max": round(max(memory), 3),
                "linear_slope_per_hour": round(slope_per_hour(hours, memory), 3),
            },
        }
    totals = [
        sum(float(sample["resources"][service]["memory_mib"]) for service in SERVICES)
        for sample in samples
    ]
    result["total_memory_mib"] = {
        "mean": round(statistics.fmean(totals), 3),
        "p95": round(percentile(totals, 0.95), 3),
        "max": round(max(totals), 3),
    }
    snapshot_ages = [
        float(sample["application"]["snapshot_age_seconds"])
        for sample in samples
    ]
    result["snapshot_age_seconds"] = {
        "p95": round(percentile(snapshot_ages, 0.95), 3),
        "max": round(max(snapshot_ages), 3),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=3600, help="seconds")
    parser.add_argument("--interval", type=int, default=30, help="seconds")
    parser.add_argument("--output", default="artifacts/soak")
    args = parser.parse_args()
    if args.duration < 1 or args.interval < 1:
        parser.error("duration and interval must be positive")

    password = os.getenv("COREVIEW_LAB_ADMIN_PASSWORD", "")
    if not password:
        parser.error("COREVIEW_LAB_ADMIN_PASSWORD is required")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    samples_path = output_dir / "samples.jsonl"
    container_ids = containers()
    probe = AppProbe(password)
    started_at = utc_now()
    started = time.monotonic()
    samples: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    with samples_path.open("w", encoding="utf-8") as stream:
        while True:
            sampled_at = utc_now()
            try:
                sample = {
                    "timestamp": sampled_at,
                    "resources": resource_sample(container_ids),
                    "runtime": runtime_state(container_ids),
                    "application": probe.sample(),
                }
                samples.append(sample)
                stream.write(json.dumps(sample, sort_keys=True) + "\n")
                stream.flush()
                print(
                    json.dumps(
                        {
                            "sample": len(samples),
                            "timestamp": sampled_at,
                            "sensors": sample["application"]["sensors"],
                            "collector_errors": sample["application"]["collector_errors"],
                            "snapshot_age_seconds": sample["application"]["snapshot_age_seconds"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
                failures.append({"timestamp": sampled_at, "error": str(exc)})
            elapsed = time.monotonic() - started
            if elapsed >= args.duration:
                break
            time.sleep(min(args.interval, max(0.0, args.duration - elapsed)))

    if not samples:
        raise RuntimeError("No soak samples were collected")
    final_state = runtime_state(container_ids)
    app_consistent = all(
        sample["application"].get("app_ok") is True
        and sample["application"].get("app_servers") == 13
        and sample["application"].get("data_file_exists") is True
        and sample["application"].get("snapshot_age_seconds", 999) <= 30
        and sample["application"].get("sensors") == 1000
        and sample["application"].get("servers_configured") == 13
        and sample["application"].get("collector_errors") == 0
        for sample in samples
    )
    stable_runtime = all(
        item["running"]
        and item["health"] == "healthy"
        and item["restart_count"] == 0
        and not item["oom_killed"]
        for item in final_state.values()
    )
    report = {
        "schema_version": 1,
        "started_at": started_at,
        "finished_at": utc_now(),
        "requested_duration_seconds": args.duration,
        "actual_duration_seconds": round(time.monotonic() - started, 3),
        "sample_interval_seconds": args.interval,
        "samples": len(samples),
        "host": {
            "platform": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "summary": summarize(samples),
        "final_runtime": final_state,
        "application_consistent": app_consistent,
        "failures": failures,
        "ok": app_consistent and stable_runtime and not failures,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Docker lab soak failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
