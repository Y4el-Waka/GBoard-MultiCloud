from __future__ import annotations

import csv
import io
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Callable

from metrics.definitions import CHANNEL_DEFINITIONS
from metrics.models import SensorIdentity
from metrics.prtg_mappings import (
    normalize_source_channel,
    resolve_channel,
    resolve_unit,
)
from prtg_client import (
    PRTGAuthenticationError,
    PRTGClient,
    PRTGClientError,
    PRTGConnectionError,
)


class PRTGDiscoveryError(Exception):
    """Base error for sanitized, read-only discovery failures."""


class PRTGDiscoveryValidationError(PRTGDiscoveryError):
    """The selected server or sensor identity is invalid."""


class PRTGDiscoveryNotFoundError(PRTGDiscoveryError):
    """The selected sensor was not present in the filtered response."""


class PRTGDiscoveryUpstreamError(PRTGDiscoveryError):
    """PRTG could not provide structurally usable discovery data."""


class PRTGDiscoveryAuthenticationError(PRTGDiscoveryUpstreamError):
    """PRTG rejected the configured read-only credentials."""


class PRTGDiscoveryConnectionError(PRTGDiscoveryUpstreamError):
    """The configured PRTG endpoint could not be reached."""


class PRTGSensorFamily(StrEnum):
    PING = "ping"
    CPU = "cpu"
    MEMORY = "memory"
    TRAFFIC = "traffic"
    SFP = "sfp"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class PRTGSourceValueKind(StrEnum):
    GAUGE = "gauge"
    RATE = "rate"
    VOLUME = "volume"
    COUNTER = "counter"
    CALCULATED = "calculated"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PRTGChannelValue:
    """One source value as observed; no numeric normalization is performed."""

    label: str
    display_value: str
    raw_value: str | None
    source_unit: str | None
    kind: PRTGSourceValueKind
    canonical_unit: str | None
    compatible_with_canonical: bool | None


@dataclass(frozen=True, slots=True)
class PRTGDiscoveredChannel:
    source_channel_id: str | None
    name: str
    canonical_key: str | None
    mapped_sensor_types: tuple[str, ...]
    values: tuple[PRTGChannelValue, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PRTGSensorMetadata:
    identity: SensorIdentity
    name: str
    prtg_sensor_type: str
    prtg_version: str | None
    device_id: str | None
    device_name: str | None
    probe_name: str | None
    group_name: str | None
    tags: tuple[str, ...]
    interval: str | None
    status: str | None
    last_value: str | None
    last_check: str | None
    declared_channel_count: int | None


@dataclass(frozen=True, slots=True)
class PRTGClassification:
    family: PRTGSensorFamily
    evidence: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class PRTGHistoricRequestMetadata:
    sensor_id: str
    requires_explicit_range: bool = True
    source_timezone_required_for_naive_timestamps: bool = True


@dataclass(frozen=True, slots=True)
class PRTGSensorDiscoveryResult:
    sensor: PRTGSensorMetadata
    channels: tuple[PRTGDiscoveredChannel, ...]
    classification: PRTGClassification
    historic: PRTGHistoricRequestMetadata


@dataclass(frozen=True, slots=True)
class PRTGHistoricCharacterization:
    delimiter: str
    headers: tuple[str, ...]
    timestamp_header: str | None
    channel_headers: tuple[str, ...]
    raw_headers: tuple[str, ...]
    channel_units: tuple[tuple[str, tuple[str, ...]], ...]
    row_count: int
    empty_value_count: int
    invalid_timestamp_count: int
    timestamp_mode: str
    average_seconds: int


ClientFactory = Callable[[], PRTGClient]

_SENSOR_TYPE_FAMILIES = {
    "ping": PRTGSensorFamily.PING,
    "snmpcpu": PRTGSensorFamily.CPU,
    "snmpmemory": PRTGSensorFamily.MEMORY,
    "snmptraffic": PRTGSensorFamily.TRAFFIC,
}

_KNOWN_UNSUPPORTED_TYPES = frozenset({"http"})

_TIMESTAMP_HEADERS = frozenset(
    {"date time", "datetime", "date/time", "fecha hora", "fecha/hora"}
)
_VALUE_WITH_UNIT = re.compile(
    r"^\s*[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][-+]?\d+)?\s*(.*?)\s*$"
)


def _type_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _clean(value: object, *, limit: int = 512) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _safe_positive_sensor_id(value: object) -> str:
    if isinstance(value, bool):
        raise PRTGDiscoveryValidationError("sensor_id must be a positive integer")
    text = str(value or "").strip()
    try:
        numeric = int(text)
    except (TypeError, ValueError):
        raise PRTGDiscoveryValidationError(
            "sensor_id must be a positive integer"
        ) from None
    if numeric <= 0 or str(numeric) != text:
        raise PRTGDiscoveryValidationError(
            "sensor_id must be a positive integer"
        )
    return text


def _extract_unit(display_value: str) -> str | None:
    match = _VALUE_WITH_UNIT.match(display_value)
    if match is None:
        return None
    return match.group(1).strip() or None


def _normalized_header(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().replace("\ufeff", "").split())


def _base_header(value: object) -> str:
    return re.sub(r"\s*\(raw\)\s*$", "", _normalized_header(value)).strip()


def _value_kind(
    header: str,
    source_unit: str | None,
    canonical_key: str | None,
) -> PRTGSourceValueKind:
    normalized = _normalized_header(header)
    if "speed" in normalized or "velocidad" in normalized:
        return PRTGSourceValueKind.RATE
    if "volume" in normalized or "volumen" in normalized:
        return PRTGSourceValueKind.VOLUME
    if "counter" in normalized or "contador" in normalized:
        return PRTGSourceValueKind.COUNTER
    if canonical_key in {"availability_pct", "utilization_pct"}:
        return PRTGSourceValueKind.CALCULATED

    conversion = resolve_unit(source_unit) if source_unit else None
    if conversion is not None and conversion.canonical_unit == "bps":
        return PRTGSourceValueKind.RATE
    if (
        conversion is not None
        and conversion.canonical_unit == "bytes"
        and canonical_key in {"traffic_in_bps", "traffic_out_bps"}
    ):
        return PRTGSourceValueKind.VOLUME
    return PRTGSourceValueKind.GAUGE


def _compatible_unit(
    canonical_key: str | None,
    source_unit: str | None,
    kind: PRTGSourceValueKind,
) -> tuple[str | None, bool | None]:
    if canonical_key is None or source_unit is None:
        return None, None
    conversion = resolve_unit(source_unit)
    if conversion is None:
        return None, False
    canonical_unit = conversion.canonical_unit
    expected = CHANNEL_DEFINITIONS[canonical_key].unit
    if kind in {PRTGSourceValueKind.VOLUME, PRTGSourceValueKind.COUNTER}:
        return canonical_unit, False
    return canonical_unit, canonical_unit == expected


def parse_channel_csv(text: str) -> tuple[PRTGDiscoveredChannel, ...]:
    """Parse channel inventory while preserving rate/volume distinctions."""
    if not isinstance(text, str) or not text.strip():
        raise PRTGDiscoveryUpstreamError("PRTG channel response is empty")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.DictReader(io.StringIO(text.replace("\r", "")), dialect=dialect)
    headers = tuple(str(header or "").strip().strip('"') for header in reader.fieldnames or ())
    if not headers:
        raise PRTGDiscoveryUpstreamError("PRTG channel response has no header")

    channel_header = next(
        (
            header
            for header in headers
            if _base_header(header) in {"channel", "name", "canal"}
            and "(raw)" not in _normalized_header(header)
        ),
        None,
    )
    if channel_header is None:
        raise PRTGDiscoveryUpstreamError(
            "PRTG channel response has no channel-name column"
        )
    channel_id_header = next(
        (
            header
            for header in headers
            if _base_header(header) in {"channel id", "channelid", "id de canal"}
            and "(raw)" not in _normalized_header(header)
        ),
        None,
    )
    raw_by_base = {
        _base_header(header): header
        for header in headers
        if "(raw)" in _normalized_header(header)
    }

    discovered: list[PRTGDiscoveredChannel] = []
    for row in reader:
        name = _clean(row.get(channel_header))
        if not name:
            continue
        mapping = resolve_channel(name)
        canonical_key = mapping.canonical_key if mapping else None
        mapped_types = tuple(sorted(mapping.sensor_types)) if mapping else ()
        values: list[PRTGChannelValue] = []
        issues: list[str] = []

        for header in headers:
            normalized = _normalized_header(header)
            base = _base_header(header)
            if "(raw)" in normalized:
                continue
            if header in {channel_header, channel_id_header}:
                continue
            if not base.startswith(("last value", "ultimo valor", "último valor")):
                continue
            display = _clean(row.get(header))
            if display is None:
                continue
            source_unit = _extract_unit(display)
            kind = _value_kind(header, source_unit, canonical_key)
            canonical_unit, compatible = _compatible_unit(
                canonical_key,
                source_unit,
                kind,
            )
            raw_header = raw_by_base.get(base)
            raw_value = _clean(row.get(raw_header)) if raw_header else None
            values.append(
                PRTGChannelValue(
                    label=header,
                    display_value=display,
                    raw_value=raw_value,
                    source_unit=source_unit,
                    kind=kind,
                    canonical_unit=canonical_unit,
                    compatible_with_canonical=compatible,
                )
            )
            if source_unit and resolve_unit(source_unit) is None:
                issues.append("unknown_unit")
            elif compatible is False:
                issues.append(
                    "non_rate_volume"
                    if kind is PRTGSourceValueKind.VOLUME
                    else "incompatible_unit"
                )

        if canonical_key is None:
            issues.append("unknown_channel")
        discovered.append(
            PRTGDiscoveredChannel(
                source_channel_id=(
                    _clean(row.get(channel_id_header))
                    if channel_id_header is not None
                    else None
                ),
                name=name,
                canonical_key=canonical_key,
                mapped_sensor_types=mapped_types,
                values=tuple(values),
                issues=tuple(dict.fromkeys(issues)),
            )
        )

    if not discovered:
        raise PRTGDiscoveryUpstreamError("PRTG channel response has no rows")
    return tuple(discovered)


def parse_sensor_metadata(
    payload: Mapping[str, Any],
    *,
    server_id: str,
    sensor_id: str,
) -> PRTGSensorMetadata:
    rows = payload.get("sensors") if isinstance(payload, Mapping) else None
    if (
        not isinstance(rows, Sequence)
        or isinstance(rows, (str, bytes, bytearray))
    ):
        raise PRTGDiscoveryUpstreamError(
            "PRTG sensor response has no sensor table"
        )
    matches = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("objid") or "").strip() == sensor_id
    ]
    if not matches:
        raise PRTGDiscoveryNotFoundError(
            f"PRTG sensor '{sensor_id}' was not found"
        )
    if len(matches) != 1:
        raise PRTGDiscoveryUpstreamError(
            "PRTG sensor response contains duplicate identities"
        )
    row = matches[0]
    name = _clean(row.get("sensor", row.get("name")))
    sensor_type = _clean(row.get("type"))
    if not name or not sensor_type:
        raise PRTGDiscoveryUpstreamError(
            "PRTG sensor response lacks name or type metadata"
        )
    device_id = _clean(row.get("parentid"))
    tags = tuple(
        tag
        for tag in str(row.get("tags") or "").split()
        if tag
    )
    count_value = row.get("channel")
    try:
        declared_channel_count = (
            int(count_value)
            if count_value is not None and str(count_value).strip()
            else None
        )
    except (TypeError, ValueError):
        declared_channel_count = None

    return PRTGSensorMetadata(
        identity=SensorIdentity(
            server_id=server_id,
            sensor_id=sensor_id,
            device_id=device_id,
        ),
        name=name,
        prtg_sensor_type=sensor_type,
        prtg_version=_clean(
            payload.get("prtg-version", payload.get("prtg_version")),
            limit=80,
        ),
        device_id=device_id,
        device_name=_clean(row.get("device")),
        probe_name=_clean(row.get("probe")),
        group_name=_clean(row.get("group")),
        tags=tags,
        interval=_clean(row.get("interval")),
        status=_clean(row.get("status")),
        last_value=_clean(row.get("lastvalue")),
        last_check=_clean(row.get("lastcheck")),
        declared_channel_count=declared_channel_count,
    )


def classify_sensor(
    sensor: PRTGSensorMetadata,
    channels: Sequence[PRTGDiscoveredChannel],
) -> PRTGClassification:
    """Classify from explicit PRTG type plus channel evidence, never display name."""
    type_token = _type_token(sensor.prtg_sensor_type)
    type_family = _SENSOR_TYPE_FAMILIES.get(type_token)
    if type_token in _KNOWN_UNSUPPORTED_TYPES:
        return PRTGClassification(
            family=PRTGSensorFamily.UNSUPPORTED,
            evidence=(f"source_type:{type_token}",),
            reason="PRTG sensor type is explicitly outside the supported families",
        )

    usable = {
        channel.canonical_key: channel
        for channel in channels
        if channel.canonical_key is not None
        and any(
            value.compatible_with_canonical is True
            for value in channel.values
        )
    }
    keys = frozenset(usable)
    candidate_families = {
        PRTGSensorFamily(sensor_type)
        for channel in usable.values()
        for sensor_type in channel.mapped_sensor_types
    }
    signatures = {
        PRTGSensorFamily.PING: "latency_ms" in keys,
        PRTGSensorFamily.CPU: "cpu_usage_pct" in keys,
        PRTGSensorFamily.MEMORY: bool(
            keys & {"memory_usage_pct", "memory_used_bytes"}
        ),
        PRTGSensorFamily.TRAFFIC: bool(
            keys & {"traffic_in_bps", "traffic_out_bps"}
        ),
        PRTGSensorFamily.SFP: {
            "rx_power_dbm",
            "tx_power_dbm",
        }.issubset(keys),
    }
    channel_families = {
        family
        for family in candidate_families
        if signatures.get(family, False)
    }
    evidence = [f"source_type:{type_token or 'missing'}"]
    if keys:
        evidence.append(f"canonical_channels:{','.join(sorted(keys))}")

    if type_family is not None:
        conflicts = candidate_families - {type_family}
        if conflicts:
            return PRTGClassification(
                family=PRTGSensorFamily.UNKNOWN,
                evidence=tuple(evidence),
                reason="PRTG type and discovered channel families conflict",
            )
        if signatures[type_family]:
            return PRTGClassification(
                family=type_family,
                evidence=tuple(evidence),
                reason="PRTG type and compatible channel signature agree",
            )
        return PRTGClassification(
            family=PRTGSensorFamily.UNKNOWN,
            evidence=tuple(evidence),
            reason="PRTG type lacks a compatible channel signature",
        )

    if len(channel_families) == 1:
        family = next(iter(channel_families))
        return PRTGClassification(
            family=family,
            evidence=tuple(evidence),
            reason="compatible channel signature identifies one family",
        )
    return PRTGClassification(
        family=PRTGSensorFamily.UNKNOWN,
        evidence=tuple(evidence),
        reason="metadata and channels do not identify one supported family",
    )


def characterize_historic_csv(
    text: str,
    *,
    average_seconds: int,
) -> PRTGHistoricCharacterization:
    """Describe CSV structure without converting metric values."""
    if not isinstance(text, str) or not text.strip():
        raise PRTGDiscoveryUpstreamError("PRTG historic response is empty")
    if isinstance(average_seconds, bool) or not isinstance(average_seconds, int):
        raise PRTGDiscoveryValidationError(
            "average_seconds must be an integer"
        )
    if average_seconds < 0:
        raise PRTGDiscoveryValidationError(
            "average_seconds cannot be negative"
        )
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.DictReader(io.StringIO(text.replace("\r", "")), dialect=dialect)
    headers = tuple(str(header or "").strip().strip('"') for header in reader.fieldnames or ())
    if not headers:
        raise PRTGDiscoveryUpstreamError("PRTG historic response has no header")
    timestamp_header = next(
        (header for header in headers if _base_header(header) in _TIMESTAMP_HEADERS),
        None,
    )
    if timestamp_header is None:
        raise PRTGDiscoveryUpstreamError(
            "PRTG historic response has no timestamp column"
        )
    rows = [
        row
        for row in reader
        if row and any(str(value or "").strip() for value in row.values())
    ]
    aware = 0
    naive = 0
    invalid = 0
    for row in rows:
        value = _clean(row.get(timestamp_header))
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            invalid += 1
            continue
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            aware += 1
        else:
            naive += 1
    observed_timestamp_modes = sum(bool(value) for value in (aware, naive, invalid))
    timestamp_mode = (
        "mixed"
        if observed_timestamp_modes > 1
        else "aware"
        if aware
        else "naive"
        if naive
        else "invalid"
        if invalid
        else "unknown"
    )
    raw_headers = tuple(
        header for header in headers if "(raw)" in _normalized_header(header)
    )
    channel_headers = tuple(
        header
        for header in headers
        if header != timestamp_header
        and "(raw)" not in _normalized_header(header)
    )
    channel_units = tuple(
        (
            header,
            tuple(
                dict.fromkeys(
                    unit
                    for row in rows
                    if (value := _clean(row.get(header))) is not None
                    if (unit := _extract_unit(value)) is not None
                )
            ),
        )
        for header in channel_headers
    )
    empty_value_count = sum(
        1
        for row in rows
        for header in channel_headers
        if not str(row.get(header) or "").strip()
    )
    return PRTGHistoricCharacterization(
        delimiter=dialect.delimiter,
        headers=headers,
        timestamp_header=timestamp_header,
        channel_headers=channel_headers,
        raw_headers=raw_headers,
        channel_units=channel_units,
        row_count=len(rows),
        empty_value_count=empty_value_count,
        invalid_timestamp_count=invalid,
        timestamp_mode=timestamp_mode,
        average_seconds=average_seconds,
    )


class PRTGSensorDiscovery:
    """Read-only orchestration for one explicitly selected PRTG sensor."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._logger = logger or logging.getLogger(__name__)

    def discover_sensor(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
    ) -> PRTGSensorDiscoveryResult:
        server_id = str(server.get("id") or "").strip()
        if not server_id:
            raise PRTGDiscoveryValidationError(
                "PRTG discovery requires a stable server id"
            )
        sensor_text = _safe_positive_sensor_id(sensor_id)
        try:
            with self._client_factory() as client:
                sensor_payload = client.get_sensor_metadata(server, sensor_text)
                channel_csv = client.get_channels_csv(server, sensor_text)
        except PRTGAuthenticationError:
            self._logger.warning(
                "PRTG discovery authentication failed server_id=%s sensor_id=%s",
                server_id,
                sensor_text,
            )
            raise PRTGDiscoveryAuthenticationError(
                "PRTG discovery authentication failed"
            ) from None
        except PRTGConnectionError:
            self._logger.warning(
                "PRTG discovery connection failed server_id=%s sensor_id=%s",
                server_id,
                sensor_text,
            )
            raise PRTGDiscoveryConnectionError(
                "PRTG discovery connection failed"
            ) from None
        except PRTGClientError as exc:
            self._logger.warning(
                "PRTG discovery failed server_id=%s sensor_id=%s error_type=%s",
                server_id,
                sensor_text,
                type(exc).__name__,
            )
            raise PRTGDiscoveryUpstreamError(
                "PRTG discovery request failed"
            ) from None

        sensor = parse_sensor_metadata(
            sensor_payload,
            server_id=server_id,
            sensor_id=sensor_text,
        )
        channels = parse_channel_csv(channel_csv)
        return PRTGSensorDiscoveryResult(
            sensor=sensor,
            channels=channels,
            classification=classify_sensor(sensor, channels),
            historic=PRTGHistoricRequestMetadata(sensor_id=sensor_text),
        )

    def fetch_historic_csv(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
        start: datetime | str,
        end: datetime | str,
        *,
        average_seconds: int,
    ) -> str:
        """Explicit, on-demand sample acquisition; never used by discovery."""
        sensor_text = _safe_positive_sensor_id(sensor_id)
        try:
            with self._client_factory() as client:
                return client.get_historic_csv(
                    server,
                    sensor_text,
                    start,
                    end,
                    average_seconds=average_seconds,
                    allow_redirects=False,
                )
        except PRTGAuthenticationError:
            self._logger.warning(
                "PRTG historic authentication failed "
                "server_id=%s sensor_id=%s",
                str(server.get("id") or "missing"),
                sensor_text,
            )
            raise PRTGDiscoveryAuthenticationError(
                "PRTG historic authentication failed"
            ) from None
        except PRTGConnectionError:
            self._logger.warning(
                "PRTG historic connection failed server_id=%s sensor_id=%s",
                str(server.get("id") or "missing"),
                sensor_text,
            )
            raise PRTGDiscoveryConnectionError(
                "PRTG historic connection failed"
            ) from None
        except PRTGClientError as exc:
            self._logger.warning(
                "PRTG historic characterization failed "
                "server_id=%s sensor_id=%s error_type=%s",
                str(server.get("id") or "missing"),
                sensor_text,
                type(exc).__name__,
            )
            raise PRTGDiscoveryUpstreamError(
                "PRTG historic characterization request failed"
            ) from None


__all__ = [
    "PRTGChannelValue",
    "PRTGClassification",
    "PRTGDiscoveredChannel",
    "PRTGDiscoveryError",
    "PRTGDiscoveryAuthenticationError",
    "PRTGDiscoveryConnectionError",
    "PRTGDiscoveryNotFoundError",
    "PRTGDiscoveryUpstreamError",
    "PRTGDiscoveryValidationError",
    "PRTGHistoricCharacterization",
    "PRTGHistoricRequestMetadata",
    "PRTGSensorDiscovery",
    "PRTGSensorDiscoveryResult",
    "PRTGSensorFamily",
    "PRTGSensorMetadata",
    "PRTGSourceValueKind",
    "characterize_historic_csv",
    "classify_sensor",
    "parse_channel_csv",
    "parse_sensor_metadata",
]
