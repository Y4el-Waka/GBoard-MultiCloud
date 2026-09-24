from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from history_service import NormalizedHistoryResult


METRICS_HISTORY_SCHEMA_VERSION = "2.0"
_SENSITIVE_DIAGNOSTIC = re.compile(
    r"(?i)\b(password|passhash|token|secret)\s*[:=]\s*[^\s&;,]+"
)


def _safe_diagnostic_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()[:512]
    if "://" in text:
        return "[redacted-url]"
    return _SENSITIVE_DIAGNOSTIC.sub(r"\1=[redacted]", text)


def utc_isoformat(value: datetime) -> str:
    """Serialize an aware timestamp as canonical ISO-8601 UTC with Z."""
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def serialize_normalized_history(
    result: NormalizedHistoryResult,
) -> dict[str, Any]:
    """Convert normalized history into the versioned HTTP-neutral payload."""
    grouped = result.by_channel()
    channels: dict[str, dict[str, Any]] = {}

    for definition in result.channels:
        samples = grouped.get(definition.key, ())
        channels[definition.key] = {
            "label": definition.label,
            "unit": definition.unit,
            "semantic": definition.semantic.value,
            "value_type": definition.value_type.value,
            "samples": [
                {
                    "timestamp": utc_isoformat(sample.timestamp),
                    "value": sample.value,
                }
                for sample in samples
            ],
        }

    return {
        "schema_version": METRICS_HISTORY_SCHEMA_VERSION,
        "sensor": {
            "server_id": result.identity.server_id,
            "sensor_id": result.identity.sensor_id,
            "sensor_type": result.sensor_type,
        },
        "range": {
            "start": (
                utc_isoformat(result.start)
                if result.start is not None
                else None
            ),
            "end": (
                utc_isoformat(result.end)
                if result.end is not None
                else None
            ),
        },
        "channels": channels,
        "issues": [
            {
                "code": issue.code.value,
                "channel": _safe_diagnostic_text(issue.source_channel),
                "channel_index": issue.channel_index,
                "message": _safe_diagnostic_text(issue.message),
            }
            for issue in result.issues
        ],
        "unknown_channels": [
            {
                "source_channel": _safe_diagnostic_text(unknown.source_channel),
                "source_unit": _safe_diagnostic_text(unknown.source_unit),
                "channel_index": unknown.channel_index,
                "reason": "unmapped_channel",
            }
            for unknown in result.unknown_channels
        ],
    }


__all__ = [
    "METRICS_HISTORY_SCHEMA_VERSION",
    "serialize_normalized_history",
    "utc_isoformat",
]
