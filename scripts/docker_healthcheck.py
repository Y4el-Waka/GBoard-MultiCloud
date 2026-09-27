#!/usr/bin/env python3
"""Service-aware healthchecks for the contained synthetic laboratory."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener


EXPECTED_SERVERS = 13
EXPECTED_SENSORS = 1000


class HealthcheckError(RuntimeError):
    pass


def _request_json(url: str) -> dict[str, Any]:
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url, timeout=2) as response:
            if response.status != 200:
                raise HealthcheckError(f"HTTP {response.status}")
            payload = json.load(response)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise HealthcheckError("service endpoint is unavailable") from exc
    if not isinstance(payload, dict):
        raise HealthcheckError("service endpoint returned an invalid payload")
    return payload


def validate_simulator(payload: dict[str, Any]) -> None:
    if (
        payload.get("ok") is not True
        or payload.get("synthetic") is not True
        or payload.get("servers") != EXPECTED_SERVERS
        or payload.get("sensors") != EXPECTED_SENSORS
    ):
        raise HealthcheckError("synthetic topology is not ready")


def validate_collector_snapshot(
    path: Path,
    *,
    now: float | None = None,
    max_age_seconds: int = 30,
) -> None:
    if not path.is_file() or path.stat().st_size < 5:
        raise HealthcheckError("collector snapshot is missing")
    reference_time = time.time() if now is None else now
    if reference_time - path.stat().st_mtime > max_age_seconds:
        raise HealthcheckError("collector snapshot is stale")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HealthcheckError("collector snapshot is invalid") from exc
    collector = payload.get("collector")
    sensors = payload.get("sensores")
    if not isinstance(collector, dict) or not isinstance(sensors, list):
        raise HealthcheckError("collector snapshot contract is invalid")
    if (
        collector.get("servers_configured") != EXPECTED_SERVERS
        or collector.get("servers_with_error") != 0
        or len(sensors) != EXPECTED_SENSORS
    ):
        raise HealthcheckError("collector has not reached the stable topology")


def validate_app(payload: dict[str, Any]) -> None:
    if (
        payload.get("ok") is not True
        or payload.get("data_file_exists") is not True
        or payload.get("servers") != EXPECTED_SERVERS
    ):
        raise HealthcheckError("CoreView HTTP service is not ready")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=("simulator", "collector", "app"))
    args = parser.parse_args()

    try:
        if args.service == "simulator":
            validate_simulator(_request_json("http://127.0.0.1:9090/healthz"))
        elif args.service == "collector":
            interval = max(1, int(os.getenv("UPDATE_INTERVAL", "5")))
            validate_collector_snapshot(
                Path(os.getenv("OUTPUT_PATH", "/runtime/estado_sensores_prtg.json")),
                max_age_seconds=max(30, interval * 3),
            )
        else:
            validate_app(_request_json("http://127.0.0.1:5067/healthz"))
    except (HealthcheckError, TypeError, ValueError) as exc:
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
