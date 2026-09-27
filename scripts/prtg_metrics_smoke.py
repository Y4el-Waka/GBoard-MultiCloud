#!/usr/bin/env python3
"""Manual, opt-in, read-only smoke test for one authorized PRTG sensor."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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
from prtg_discovery import characterize_historic_csv  # noqa: E402
from real_metrics_provider import RealHistoricalMetricsSource  # noqa: E402


SUPPORTED_FAMILIES = ("ping", "cpu", "memory", "traffic", "sfp")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_SAFE_UNIT = re.compile(r"^[A-Za-z0-9%°µμ/ ._-]{1,24}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9_.() -]{1,80}$")


class ValidationCaptureSource:
    """Capture one provider response in memory without persisting raw data."""

    def __init__(self, source: RealHistoricalMetricsSource) -> None:
        self._source = source
        self.metadata: Mapping[str, Any] | None = None
        self.history: Mapping[str, Any] | None = None

    def fetch_sensor_metadata(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
    ) -> Mapping[str, Any]:
        self.metadata = self._source.fetch_sensor_metadata(server, sensor_id)
        return self.metadata

    def fetch_history(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
        *,
        points: int | None,
        start: datetime | None,
        end: datetime | None,
    ) -> Mapping[str, Any]:
        self.history = self._source.fetch_history(
            server,
            sensor_id,
            points=points,
            start=start,
            end=end,
        )
        return self.history


def _safe_token(value: object, *, fallback: str = "redacted") -> str:
    text = str(value or "").strip()
    return text if _SAFE_TOKEN.fullmatch(text) else fallback


def _safe_unit(value: object) -> str:
    text = str(value or "").strip()
    return text if _SAFE_UNIT.fullmatch(text) else "redacted_unit"


def _safe_version(value: object) -> str:
    text = str(value or "").strip()
    return text if _SAFE_VERSION.fullmatch(text) else "not_recorded"


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_expected_family(expected: str, detected: str) -> None:
    if detected != expected:
        raise RuntimeError("detected sensor family does not match expectation")


def build_safe_summary(
    *,
    expected_family: str,
    metadata: Any,
    result: Any,
    source_metadata: Mapping[str, Any],
    history_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build allowlisted evidence; raw CSV, values, names, and secrets stay out."""
    historic_csv = history_payload.get("historic_csv")
    average_seconds = history_payload.get("aggregation_seconds")
    if not isinstance(historic_csv, str) or not isinstance(average_seconds, int):
        raise RuntimeError("historic evidence is unavailable")
    characterization = characterize_historic_csv(
        historic_csv,
        average_seconds=average_seconds,
    )

    source_channels = []
    for index, channel in enumerate(source_metadata.get("channels", ()), start=1):
        if not isinstance(channel, Mapping):
            continue
        source_channels.append(
            {
                "alias": f"channel_{index}",
                "canonical_key": _safe_token(
                    channel.get("canonical_key"),
                    fallback="unmapped",
                ),
                "source_units": sorted(
                    {
                        _safe_unit(unit)
                        for unit in channel.get("source_units", ())
                    }
                ),
                "kinds": sorted(
                    _safe_token(kind) for kind in channel.get("kinds", ())
                ),
                "issues": sorted(
                    _safe_token(issue) for issue in channel.get("issues", ())
                ),
            }
        )

    canonical_channels = []
    for channel_key, samples in sorted(result.by_channel().items()):
        canonical_channels.append(
            {
                "key": _safe_token(channel_key),
                "unit": _safe_unit(samples[0].unit) if samples else None,
                "sample_count": len(samples),
            }
        )

    channel_columns = []
    for index, (_header, units) in enumerate(
        characterization.channel_units,
        start=1,
    ):
        channel_columns.append(
            {
                "alias": f"historic_column_{index}",
                "source_units": sorted(_safe_unit(unit) for unit in units),
            }
        )

    delimiter_names = {";": "semicolon", ",": "comma", "\t": "tab"}
    return {
        "implementation_complete_with_fixtures": True,
        "validated_against_real_prtg": False,
        "compatibility_status": "not_tested",
        "evidence_review_required": True,
        "smoke_execution_succeeded": True,
        "scope": {
            "server_id": metadata.identity.server_id,
            "sensor_id": metadata.identity.sensor_id,
            "expected_family": expected_family,
            "detected_family": metadata.sensor_type,
            "source_sensor_type": _safe_token(
                source_metadata.get("source_sensor_type"),
            ),
            "prtg_version": _safe_version(
                source_metadata.get("source_prtg_version"),
            ),
        },
        "discovery": {
            "classification_status": _safe_token(
                source_metadata.get("classification_status"),
            ),
            "channels": source_channels,
        },
        "history": {
            "delimiter": delimiter_names.get(
                characterization.delimiter,
                "other",
            ),
            "header_count": len(characterization.headers),
            "channel_columns": channel_columns,
            "raw_column_count": len(characterization.raw_headers),
            "timestamp_mode": characterization.timestamp_mode,
            "row_count": characterization.row_count,
            "empty_value_count": characterization.empty_value_count,
            "invalid_timestamp_count": characterization.invalid_timestamp_count,
            "aggregation_seconds": characterization.average_seconds,
            "observed_start": _utc_text(result.start),
            "observed_end": _utc_text(result.end),
        },
        "adapter": {
            "canonical_channels": canonical_channels,
            "sample_count": len(result.samples),
            "issue_codes": sorted(
                {_safe_token(issue.code) for issue in result.issues}
            ),
            "unknown_channel_count": len(result.unknown_channels),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read metadata and a small history window from one explicitly "
            "configured, authorized PRTG sensor."
        )
    )
    parser.add_argument("--server-id", required=True)
    parser.add_argument("--sensor-id", required=True)
    parser.add_argument(
        "--expected-family",
        required=True,
        choices=SUPPORTED_FAMILIES,
    )
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
        capture = ValidationCaptureSource(source)
        servers = {str(server["name"]): server}
        service = HistoryService(
            server_provider=lambda: servers,
            client_factory=client_factory,
            cache_ttl_seconds=settings.history_cache_ttl,
            cache_max_entries=settings.history_cache_max_entries,
            normalized_source=capture,
        )

        metadata = service.get_normalized_sensor_metadata(
            str(server["name"]),
            args.sensor_id,
        )
        _require_expected_family(args.expected_family, metadata.sensor_type)
        result = service.get_normalized_history(
            str(server["name"]),
            args.sensor_id,
            sensor_type=metadata.sensor_type,
            points=args.points,
        )
        if capture.metadata is None or capture.history is None:
            raise RuntimeError("validation evidence was not captured")
        summary = build_safe_summary(
            expected_family=args.expected_family,
            metadata=metadata,
            result=result,
            source_metadata=capture.metadata,
            history_payload=capture.history,
        )
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
