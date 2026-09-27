#!/usr/bin/env python3
"""Authenticated end-to-end smoke for the Docker synthetic laboratory."""
from __future__ import annotations

import http.cookiejar
import ipaddress
import json
import os
import sys
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import (
    HTTPCookieProcessor,
    ProxyHandler,
    Request,
    build_opener,
)


EXPECTED_CHANNELS = {
    "ping": {"latency_ms", "packet_loss_pct", "availability_pct"},
    "cpu": {"cpu_usage_pct"},
    "memory": {"memory_usage_pct", "memory_used_bytes"},
    "traffic": {"traffic_in_bps", "traffic_out_bps", "utilization_pct"},
    "sfp": {
        "rx_power_dbm",
        "tx_power_dbm",
        "temperature_c",
        "voltage_v",
        "bias_current_ma",
    },
}


class SmokeError(RuntimeError):
    pass


def _loopback_base_url(value: str, label: str) -> str:
    try:
        parsed = urlsplit(value.strip().rstrip("/"))
        host = parsed.hostname or ""
        if host.casefold() != "localhost":
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
    except ValueError:
        raise SmokeError(f"{label} must use a loopback destination") from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise SmokeError(f"{label} is invalid")
    netloc = parsed.hostname or ""
    if ":" in netloc:
        netloc = f"[{netloc}]"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


class LabClient:
    def __init__(self, app_url: str, simulator_url: str) -> None:
        self.app_url = _loopback_base_url(app_url, "CoreView URL")
        self.simulator_url = _loopback_base_url(
            simulator_url,
            "simulator URL",
        )
        jar = http.cookiejar.CookieJar()
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))

    def request(
        self,
        base: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        form: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ):
        if sum(value is not None for value in (form, payload)) > 1:
            raise SmokeError("request body modes cannot be combined")
        url = f"{base}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = {"Accept": "application/json"}
        data = None
        if form is not None:
            data = urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers)
        try:
            return self.opener.open(request, timeout=10)
        except HTTPError as exc:
            raise SmokeError(f"HTTP {exc.code} for {path}") from None
        except (URLError, OSError):
            raise SmokeError(f"service unavailable for {path}") from None

    def json(self, base: str, path: str, **kwargs: Any) -> Any:
        with self.request(base, path, **kwargs) as response:
            try:
                return json.load(response)
            except ValueError:
                raise SmokeError(f"invalid JSON for {path}") from None

    def text(self, base: str, path: str, **kwargs: Any) -> str:
        with self.request(base, path, **kwargs) as response:
            return response.read().decode("utf-8", errors="replace")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def _discover_family_sensors(payload: Mapping[str, Any]) -> tuple[dict[str, Any], Any]:
    sensors = payload.get("sensors")
    if not isinstance(sensors, list):
        raise SmokeError("simulator catalog response is invalid")
    by_family: dict[str, list[Mapping[str, Any]]] = {}
    for sensor in sensors:
        if not isinstance(sensor, Mapping):
            continue
        family = str(sensor.get("sensor_type") or "")
        if family in EXPECTED_CHANNELS:
            by_family.setdefault(family, []).append(sensor)
    selected: dict[str, Any] = {}
    for family in EXPECTED_CHANNELS:
        candidates = by_family.get(family, [])
        _require(bool(candidates), f"no synthetic {family} sensor was discovered")
        selected[family] = max(
            candidates,
            key=lambda item: len(item.get("channels") or ()),
        )
    partial_sfp = min(
        by_family["sfp"],
        key=lambda item: len(item.get("channels") or ()),
    )
    return selected, partial_sfp


def _validate_history(family: str, payload: Mapping[str, Any], required: set[str]) -> set[str]:
    _require(payload.get("schema_version") == "2.0", f"{family} schema mismatch")
    channels = payload.get("channels")
    _require(isinstance(channels, Mapping), f"{family} channels are invalid")
    keys = set(channels)
    _require(required <= keys, f"{family} canonical channels are incomplete")
    for key in keys:
        channel = channels[key]
        _require(isinstance(channel, Mapping), f"{family}/{key} is invalid")
        samples = channel.get("samples")
        _require(isinstance(samples, list) and samples, f"{family}/{key} is empty")
        timestamps = []
        for sample in samples:
            _require(isinstance(sample.get("value"), (int, float)), f"{family}/{key} is not numeric")
            timestamp = sample.get("timestamp")
            _require(isinstance(timestamp, str) and timestamp.endswith("Z"), f"{family}/{key} timestamp is not UTC")
            timestamps.append(timestamp)
        _require(timestamps == sorted(timestamps), f"{family}/{key} is unordered")
    return keys


def run() -> dict[str, Any]:
    password = os.getenv("COREVIEW_LAB_ADMIN_PASSWORD", "")
    if not password:
        raise SmokeError("COREVIEW_LAB_ADMIN_PASSWORD is required")
    username = os.getenv("COREVIEW_LAB_ADMIN_USER", "lab-admin")
    client = LabClient(
        os.getenv("COREVIEW_LAB_APP_URL", "http://127.0.0.1:5067"),
        os.getenv("COREVIEW_LAB_SIMULATOR_URL", "http://127.0.0.1:9090"),
    )

    simulator_health = client.json(client.simulator_url, "/healthz")
    _require(simulator_health.get("servers") == 13, "simulator server count mismatch")
    _require(simulator_health.get("sensors") == 1000, "simulator sensor count mismatch")

    client.text(
        client.app_url,
        "/",
        form={"username": username, "password": password},
    )
    identity = client.json(client.app_url, "/api/me")
    _require(identity.get("username") == username, "authentication failed")

    dashboard = client.text(client.app_url, "/dashboard")
    _require('data-metrics-ui-enabled="true"' in dashboard, "multichannel UI flag is disabled")

    snapshot = client.json(client.app_url, "/api/sensors")
    collector = snapshot.get("collector") or {}
    sensors = snapshot.get("sensores") or []
    _require(collector.get("servers_configured") == 13, "collector server count mismatch")
    _require(collector.get("servers_with_error") == 0, "collector reports errors")
    _require(len(sensors) == 1000, "collector sensor count mismatch")

    source = client.json(
        client.simulator_url,
        "/prtg-01/api/multichannel.json",
    )
    selected, partial_sfp = _discover_family_sensors(source)
    observed: dict[str, list[str]] = {}
    for family, sensor in selected.items():
        sensor_id = str(sensor["objid"])
        metadata = client.json(
            client.app_url,
            "/api/v2/metrics/sensor-metadata",
            query={"server": "PRTG LAB 01", "sensor_id": sensor_id},
        )
        _require(metadata["sensor"]["sensor_type"] == family, f"{family} metadata mismatch")
        history = client.json(
            client.app_url,
            "/api/v2/metrics/history",
            query={
                "server": "PRTG LAB 01",
                "sensor_id": sensor_id,
                "sensor_type": family,
                "points": 6,
            },
        )
        observed[family] = sorted(
            _validate_history(family, history, EXPECTED_CHANNELS[family])
        )

    partial_id = str(partial_sfp["objid"])
    partial_history = client.json(
        client.app_url,
        "/api/v2/metrics/history",
        query={
            "server": "PRTG LAB 01",
            "sensor_id": partial_id,
            "sensor_type": "sfp",
            "points": 3,
        },
    )
    partial_channels = _validate_history(
        "partial_sfp",
        partial_history,
        {"rx_power_dbm"},
    )
    _require(partial_channels < EXPECTED_CHANNELS["sfp"], "partial SFP unexpectedly contains every channel")

    ping_id = str(selected["ping"]["objid"])
    marker = {"group": "PRTG LAB 01", "id": ping_id}
    pins_before = client.json(client.app_url, "/api/me/pins").get("pins", [])
    expect_persisted = os.getenv("COREVIEW_LAB_EXPECT_PERSISTED", "").casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if expect_persisted:
        _require(marker in pins_before, "SQLite persistence marker is missing")
    pin_result = client.json(
        client.app_url,
        "/api/me/pins",
        payload=marker,
    )
    _require(pin_result.get("pinned") is True, "SQLite persistence marker failed")
    pins_after = client.json(client.app_url, "/api/me/pins").get("pins", [])
    _require(marker in pins_after, "SQLite persistence marker was not stored")

    legacy = client.json(
        client.app_url,
        "/api/history",
        query={"group": "PRTG LAB 01", "id": ping_id, "minutes": 15, "avg": 300},
    )
    _require(isinstance(legacy.get("datasets"), list), "legacy history is unavailable")

    pause_result = client.json(
        client.app_url,
        "/api/sensors/pause",
        payload={
            "group": "PRTG LAB 01",
            "id": ping_id,
            "duration_minutes": 60,
            "message": "Synthetic Docker smoke",
        },
    )
    _require(pause_result.get("ok") is True, "synthetic pause failed")
    source_sensors = client.json(
        client.simulator_url,
        "/prtg-01/api/table.json",
        query={"content": "sensors"},
    )["sensors"]
    paused = next(item for item in source_sensors if str(item["objid"]) == ping_id)
    _require("Paused" in paused["status"], "simulator did not enter paused state")

    resume_result = client.json(
        client.app_url,
        "/api/sensors/resume",
        payload={"group": "PRTG LAB 01", "id": ping_id},
    )
    _require(resume_result.get("ok") is True, "synthetic resume failed")

    return {
        "ok": True,
        "synthetic_only": True,
        "provider": "synthetic",
        "metrics_ui_enabled": True,
        "servers": 13,
        "sensors": 1000,
        "collector_errors": 0,
        "families": observed,
        "partial_sfp_channels": sorted(partial_channels),
        "legacy_history": True,
        "pause_resume": True,
        "sqlite_persistence_marker": True,
        "sqlite_persistence_verified_after_restart": expect_persisted,
    }


def main() -> int:
    try:
        print(json.dumps(run(), indent=2, sort_keys=True))
        return 0
    except (SmokeError, KeyError, StopIteration, TypeError, ValueError) as exc:
        print(f"Docker lab smoke failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
