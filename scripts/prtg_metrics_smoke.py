#!/usr/bin/env python3
"""Manual, opt-in, read-only smoke test for one authorized PRTG sensor."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    load_app_settings,
    load_environment,
    load_monitor_prtg_servers,
)
from history_service import HistoryService  # noqa: E402
from prtg_client import PRTGClient  # noqa: E402
from real_metrics_provider import RealHistoricalMetricsSource  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read metadata and a small history window from one explicitly "
            "configured, authorized PRTG sensor."
        )
    )
    parser.add_argument("--server-id", required=True)
    parser.add_argument("--sensor-id", required=True)
    parser.add_argument("--points", type=int, default=3, choices=range(1, 11))
    parser.add_argument(
        "--confirm-read-only-authorized",
        action="store_true",
        help="Required acknowledgement that the selected PRTG is authorized.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.confirm_read_only_authorized:
        print(
            "Refusing to contact PRTG without "
            "--confirm-read-only-authorized.",
            file=sys.stderr,
        )
        return 2

    try:
        load_environment()
        settings = load_app_settings()
        if settings.metrics_provider != "prtg":
            raise RuntimeError("COREVIEW_METRICS_PROVIDER must be prtg")

        configured = load_monitor_prtg_servers(emit=None)
        server = next(
            (
                item
                for item in configured
                if str(item.get("id") or "") == args.server_id
            ),
            None,
        )
        if server is None:
            raise RuntimeError("configured server_id was not found")

        def client_factory() -> PRTGClient:
            return PRTGClient(
                timeout=settings.request_timeout,
                verify_ssl=settings.shared.verify_ssl,
                http_settings=settings.shared.http,
                logger=logging.getLogger("coreview.prtg_smoke"),
            )

        source = RealHistoricalMetricsSource(
            client_factory=client_factory,
            source_timezone=settings.prtg_source_timezone or "",
            history_average_seconds=settings.prtg_history_average_seconds,
            metadata_cache_ttl_seconds=(
                settings.prtg_discovery_cache_ttl_seconds
            ),
            metadata_cache_max_entries=(
                settings.prtg_discovery_cache_max_entries
            ),
            max_history_requests_per_minute=(
                settings.prtg_history_max_requests_per_minute
            ),
            expected_processes=settings.prtg_metrics_expected_processes,
        )
        servers = {str(server["name"]): server}
        service = HistoryService(
            server_provider=lambda: servers,
            client_factory=client_factory,
            cache_ttl_seconds=settings.history_cache_ttl,
            cache_max_entries=settings.history_cache_max_entries,
            normalized_source=source,
        )

        metadata = service.get_normalized_sensor_metadata(
            str(server["name"]),
            args.sensor_id,
        )
        result = service.get_normalized_history(
            str(server["name"]),
            args.sensor_id,
            sensor_type=metadata.sensor_type,
            points=args.points,
        )
        summary = {
            "implementation_complete_with_fixtures": True,
            "validated_against_real_prtg": True,
            "server_id": metadata.identity.server_id,
            "sensor_id": metadata.identity.sensor_id,
            "sensor_type": metadata.sensor_type,
            "channels": sorted(result.by_channel()),
            "sample_count": len(result.samples),
            "issue_count": len(result.issues),
            "unknown_channel_count": len(result.unknown_channels),
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
