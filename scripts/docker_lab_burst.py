#!/usr/bin/env python3
"""Run a bounded synthetic API burst against the loopback Docker lab."""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

from docker_lab_smoke import SmokeError, _loopback_base_url


FAMILIES = ("ping", "cpu", "memory", "traffic", "sfp")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def read_json(url: str, cookie_header: str = "") -> Any:
    headers = {"Accept": "application/json"}
    if cookie_header:
        headers["Cookie"] = cookie_header
    opener = build_opener(ProxyHandler({}))
    started = time.monotonic()
    with opener.open(Request(url, headers=headers), timeout=10) as response:
        payload = json.load(response)
        if response.status != 200:
            raise SmokeError(f"unexpected HTTP status for {url.split('?', 1)[0]}")
    return payload, (time.monotonic() - started) * 1000


def authenticate(app_url: str, password: str) -> str:
    jar = http.cookiejar.CookieJar()
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))
    body = urlencode({"username": "lab-admin", "password": password}).encode()
    with opener.open(Request(f"{app_url}/", data=body), timeout=10):
        pass
    cookie_header = "; ".join(f"{cookie.name}={cookie.value}" for cookie in jar)
    if not cookie_header:
        raise SmokeError("laboratory authentication did not establish a session")
    identity, _latency = read_json(f"{app_url}/api/me", cookie_header)
    if identity.get("username") != "lab-admin":
        raise SmokeError("laboratory authentication failed")
    return cookie_header


def discover_sensor_ids(simulator_url: str) -> dict[str, str]:
    payload, _latency = read_json(
        f"{simulator_url}/prtg-01/api/multichannel.json"
    )
    found: dict[str, str] = {}
    for sensor in payload.get("sensors") or ():
        family = str(sensor.get("sensor_type") or "")
        if family in FAMILIES and family not in found:
            found[family] = str(sensor["objid"])
    if set(found) != set(FAMILIES):
        raise SmokeError("synthetic catalog does not contain every metric family")
    return found


def endpoint_plan(app_url: str, sensor_ids: dict[str, str]) -> list[str]:
    endpoints = [f"{app_url}/healthz", f"{app_url}/api/sensors"]
    for family in FAMILIES:
        identity = urlencode(
            {"server": "PRTG LAB 01", "sensor_id": sensor_ids[family]}
        )
        endpoints.append(f"{app_url}/api/v2/metrics/sensor-metadata?{identity}")
        history = urlencode(
            {
                "server": "PRTG LAB 01",
                "sensor_id": sensor_ids[family],
                "sensor_type": family,
                "points": 6,
            }
        )
        endpoints.append(f"{app_url}/api/v2/metrics/history?{history}")
    legacy = urlencode(
        {
            "group": "PRTG LAB 01",
            "id": sensor_ids["ping"],
            "minutes": 15,
            "avg": 300,
        }
    )
    endpoints.append(f"{app_url}/api/history?{legacy}")
    return endpoints


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=180)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--output", default="artifacts/resource-limits/burst.json")
    args = parser.parse_args()
    if not 1 <= args.requests <= 1000:
        parser.error("requests must be between 1 and 1000")
    if not 1 <= args.workers <= 32:
        parser.error("workers must be between 1 and 32")

    password = os.getenv("COREVIEW_LAB_ADMIN_PASSWORD", "")
    if not password:
        parser.error("COREVIEW_LAB_ADMIN_PASSWORD is required")
    app_url = _loopback_base_url(
        os.getenv("COREVIEW_LAB_APP_URL", "http://127.0.0.1:5067"),
        "CoreView URL",
    )
    simulator_url = _loopback_base_url(
        os.getenv("COREVIEW_LAB_SIMULATOR_URL", "http://127.0.0.1:9090"),
        "simulator URL",
    )
    cookie_header = authenticate(app_url, password)
    endpoints = endpoint_plan(app_url, discover_sensor_ids(simulator_url))

    latencies: list[float] = []
    failures: list[dict[str, str]] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        scheduled = {
            executor.submit(read_json, endpoints[index % len(endpoints)], cookie_header):
            endpoints[index % len(endpoints)].split("?", 1)[0].removeprefix(app_url)
            for index in range(args.requests)
        }
        for future in as_completed(scheduled):
            endpoint = scheduled[future]
            try:
                _payload, latency = future.result()
                latencies.append(latency)
            except (OSError, SmokeError, TypeError, ValueError) as exc:
                failures.append(
                    {"endpoint": endpoint, "error_type": type(exc).__name__}
                )

    elapsed = time.monotonic() - started
    report = {
        "schema_version": 1,
        "synthetic_only": True,
        "requests": args.requests,
        "workers": args.workers,
        "succeeded": len(latencies),
        "failed": len(failures),
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(len(latencies) / elapsed, 3),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
            "p50": round(percentile(latencies, 0.50), 3) if latencies else None,
            "p95": round(percentile(latencies, 0.95), 3) if latencies else None,
            "max": round(max(latencies), 3) if latencies else None,
        },
        "failures": failures,
        "ok": not failures and len(latencies) == args.requests,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SmokeError, TypeError, ValueError) as exc:
        print(f"Docker lab burst failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
