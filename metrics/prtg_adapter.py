from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from enum import StrEnum
from math import isfinite
from typing import Any

from .definitions import CHANNEL_DEFINITIONS, SENSOR_DEFINITIONS
from .models import MetricSample, MetricSemantic, MetricValueType, SensorIdentity
from .prtg_mappings import (
    SOURCE_CHANNEL_ALIASES,
    normalize_source_channel,
    resolve_channel,
    resolve_unit,
)


_NUMERIC_VALUE = re.compile(
    r"^\s*([-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][-+]?\d+)?)\s*(.*?)\s*$"
)


class PRTGAdapterError(ValueError):
    """Raised when a complete PRTG sensor record is structurally unusable."""


class AdapterIssueCode(StrEnum):
    MISSING_METADATA = "missing_metadata"
    UNKNOWN_UNIT = "unknown_unit"
    INVALID_VALUE = "invalid_value"
    INVALID_TIMESTAMP = "invalid_timestamp"
    CHANNEL_NOT_SUPPORTED = "channel_not_supported"
    UNIT_MISMATCH = "unit_mismatch"
    COUNTER_UNSUPPORTED = "counter_unsupported"


@dataclass(frozen=True, slots=True)
class AdapterIssue:
    code: AdapterIssueCode
    message: str
    channel_index: int | None = None
    source_channel: str | None = None


@dataclass(frozen=True, slots=True)
class UnknownChannel:
    source_channel: str
    source_unit: str | None
    channel_index: int


@dataclass(frozen=True, slots=True)
class AdapterResult:
    identity: SensorIdentity
    sensor_type: str
    samples: tuple[MetricSample, ...]
    unknown_channels: tuple[UnknownChannel, ...]
    issues: tuple[AdapterIssue, ...]


class PRTGMetricAdapter:
    """Convert PRTG-shaped sensor/channel observations into domain samples.

    A structurally invalid sensor raises ``PRTGAdapterError``. Individual bad
    channels are reported in ``AdapterResult`` so valid sibling channels remain
    available.
    """

    def __init__(
        self,
        *,
        additional_aliases: Mapping[str, str] | None = None,
    ) -> None:
        aliases: dict[str, str] = {}
        for source_alias, canonical_key in (additional_aliases or {}).items():
            normalized = normalize_source_channel(source_alias)
            if not normalized:
                raise ValueError("additional channel aliases must not be empty")
            if canonical_key not in CHANNEL_DEFINITIONS:
                raise ValueError(f"unknown canonical channel: {canonical_key}")

            existing = SOURCE_CHANNEL_ALIASES.get(normalized)
            if existing is not None and existing.canonical_key != canonical_key:
                raise ValueError(
                    f"channel alias conflicts with built-in mapping: {source_alias}"
                )
            aliases[normalized] = canonical_key
        self._additional_aliases = aliases

    def adapt_sensor(
        self,
        source: Mapping[str, Any],
        *,
        server_id: str,
        sensor_type: str,
        source_timezone: tzinfo | None = None,
    ) -> AdapterResult:
        if not isinstance(source, Mapping):
            raise PRTGAdapterError("source must be a mapping")

        normalized_sensor_type = str(sensor_type or "").strip().casefold()
        if normalized_sensor_type not in SENSOR_DEFINITIONS:
            raise PRTGAdapterError(f"unsupported sensor type: {sensor_type!r}")
        if source_timezone is not None and not isinstance(source_timezone, tzinfo):
            raise PRTGAdapterError("source_timezone must be a tzinfo instance")

        sensor_id = source.get("objid", source.get("sensor_id"))
        if sensor_id is None or not str(sensor_id).strip():
            raise PRTGAdapterError("source sensor identity is missing objid")

        device_id = source.get("parentid", source.get("device_id"))
        try:
            identity = SensorIdentity(
                server_id=server_id,
                sensor_id=str(sensor_id),
                device_id=None if device_id is None else str(device_id),
            )
        except (TypeError, ValueError) as exc:
            raise PRTGAdapterError("source sensor identity is invalid") from exc

        observations = self._channel_observations(source)
        definition = SENSOR_DEFINITIONS[normalized_sensor_type]
        source_metadata = self._source_metadata(source, normalized_sensor_type)
        samples: list[MetricSample] = []
        unknown_channels: list[UnknownChannel] = []
        issues: list[AdapterIssue] = []

        for index, observation in enumerate(observations):
            if not isinstance(observation, Mapping):
                issues.append(
                    AdapterIssue(
                        AdapterIssueCode.MISSING_METADATA,
                        "channel observation must be a mapping",
                        channel_index=index,
                    )
                )
                continue

            source_channel = self._source_channel(observation)
            if source_channel is None:
                issues.append(
                    AdapterIssue(
                        AdapterIssueCode.MISSING_METADATA,
                        "channel observation is missing its name",
                        channel_index=index,
                    )
                )
                continue

            canonical_key, allowed_sensor_types = self._canonical_channel(
                source_channel
            )
            if canonical_key is None:
                source_unit = observation.get("unit")
                unknown_channels.append(
                    UnknownChannel(
                        source_channel=source_channel,
                        source_unit=(
                            None
                            if source_unit is None
                            else str(source_unit).strip() or None
                        ),
                        channel_index=index,
                    )
                )
                continue

            if (
                normalized_sensor_type not in allowed_sensor_types
                or not definition.supports(canonical_key)
            ):
                issues.append(
                    AdapterIssue(
                        AdapterIssueCode.CHANNEL_NOT_SUPPORTED,
                        (
                            f"canonical channel {canonical_key} is not supported "
                            f"by sensor type {normalized_sensor_type}"
                        ),
                        channel_index=index,
                        source_channel=source_channel,
                    )
                )
                continue

            timestamp_source = observation.get("timestamp", source.get("timestamp"))
            try:
                timestamp = self._parse_timestamp(
                    timestamp_source,
                    source_timezone=source_timezone,
                )
            except (TypeError, ValueError):
                issues.append(
                    AdapterIssue(
                        AdapterIssueCode.INVALID_TIMESTAMP,
                        "channel timestamp is invalid or lacks an explicit timezone",
                        channel_index=index,
                        source_channel=source_channel,
                    )
                )
                continue

            raw_value = observation.get("value", observation.get("lastvalue"))
            try:
                numeric_value, embedded_unit = self._parse_value(raw_value)
            except (TypeError, ValueError):
                issues.append(
                    AdapterIssue(
                        AdapterIssueCode.INVALID_VALUE,
                        "channel value is not a finite numeric observation",
                        channel_index=index,
                        source_channel=source_channel,
                    )
                )
                continue

            declared_unit = observation.get("unit")
            source_unit = self._select_source_unit(declared_unit, embedded_unit)
            if source_unit is None:
                issues.append(
                    AdapterIssue(
                        AdapterIssueCode.MISSING_METADATA,
                        "channel observation is missing its source unit",
                        channel_index=index,
                        source_channel=source_channel,
                    )
                )
                continue

            declared_conversion = (
                resolve_unit(declared_unit) if declared_unit is not None else None
            )
            embedded_conversion = (
                resolve_unit(embedded_unit) if embedded_unit is not None else None
            )
            conversion = resolve_unit(source_unit)
            if conversion is None:
                issues.append(
                    AdapterIssue(
                        AdapterIssueCode.UNKNOWN_UNIT,
                        f"source unit is not mapped: {source_unit}",
                        channel_index=index,
                        source_channel=source_channel,
                    )
                )
                continue
            if (
                (declared_unit is not None and declared_conversion is None)
                or (embedded_unit is not None and embedded_conversion is None)
            ):
                unknown_unit = (
                    declared_unit
                    if declared_conversion is None and declared_unit is not None
                    else embedded_unit
                )
                issues.append(
                    AdapterIssue(
                        AdapterIssueCode.UNKNOWN_UNIT,
                        f"source unit is not mapped: {unknown_unit}",
                        channel_index=index,
                        source_channel=source_channel,
                    )
                )
                continue
            if (
                declared_conversion is not None
                and embedded_conversion is not None
                and declared_conversion != embedded_conversion
            ):
                issues.append(
                    AdapterIssue(
                        AdapterIssueCode.UNIT_MISMATCH,
                        "declared and embedded source units disagree",
                        channel_index=index,
                        source_channel=source_channel,
                    )
                )
                continue

            channel_definition = definition.channel(canonical_key)
            if conversion.canonical_unit != channel_definition.unit:
                issues.append(
                    AdapterIssue(
                        AdapterIssueCode.UNIT_MISMATCH,
                        (
                            f"canonical unit {conversion.canonical_unit} does not "
                            f"match {canonical_key} ({channel_definition.unit})"
                        ),
                        channel_index=index,
                        source_channel=source_channel,
                    )
                )
                continue

            source_kind = str(observation.get("kind", "")).strip().casefold()
            if (
                source_kind == "counter"
                and channel_definition.semantic is MetricSemantic.RATE
            ):
                issues.append(
                    AdapterIssue(
                        AdapterIssueCode.COUNTER_UNSUPPORTED,
                        "counter-to-rate calculation is not implemented",
                        channel_index=index,
                        source_channel=source_channel,
                    )
                )
                continue

            converted_value = numeric_value * conversion.factor
            if channel_definition.value_type is MetricValueType.INTEGER:
                if not converted_value.is_integer():
                    issues.append(
                        AdapterIssue(
                            AdapterIssueCode.INVALID_VALUE,
                            "integer canonical channel received a fractional value",
                            channel_index=index,
                            source_channel=source_channel,
                        )
                    )
                    continue
                canonical_value: int | float = int(converted_value)
            else:
                canonical_value = converted_value

            metadata = dict(source_metadata)
            metadata["source_unit"] = source_unit
            if source_kind:
                metadata["source_kind"] = source_kind

            samples.append(
                MetricSample(
                    identity=identity,
                    channel_key=canonical_key,
                    value=canonical_value,
                    timestamp=timestamp,
                    unit=channel_definition.unit,
                    quality=self._quality(observation, source),
                    source_channel=source_channel,
                    source_metadata=metadata,
                )
            )

        samples.sort(
            key=lambda sample: (
                sample.timestamp,
                sample.channel_key,
                sample.source_channel or "",
            )
        )
        return AdapterResult(
            identity=identity,
            sensor_type=normalized_sensor_type,
            samples=tuple(samples),
            unknown_channels=tuple(unknown_channels),
            issues=tuple(issues),
        )

    def adapt_historic_csv(
        self,
        text: str,
        *,
        server_id: str,
        sensor_id: str | int,
        sensor_type: str,
        source_timezone: tzinfo | None,
        device_id: str | int | None = None,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> AdapterResult:
        """Adapt PRTG historic CSV without using the legacy UI parser.

        CSV decoding is structural only. Every non-time column is passed through
        the same explicit channel mapping and unit normalization as live source
        observations. Unknown historic columns therefore remain observable.
        """

        if not isinstance(text, str) or not text.strip():
            raise PRTGAdapterError("historic CSV source is empty")
        if source_metadata is not None and not isinstance(source_metadata, Mapping):
            raise PRTGAdapterError("source_metadata must be a mapping")

        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ";"

        reader = csv.DictReader(
            io.StringIO(text.replace("\r", "")),
            dialect=dialect,
        )
        headers = [
            str(header or "").strip().strip('"')
            for header in reader.fieldnames or ()
        ]
        if not headers:
            raise PRTGAdapterError("historic CSV source has no header")

        time_aliases = {
            "date time",
            "datetime",
            "date/time",
            "fecha hora",
            "fecha/hora",
        }
        time_header = next(
            (
                header
                for header in headers
                if normalize_source_channel(header) in time_aliases
            ),
            None,
        )
        if time_header is None:
            raise PRTGAdapterError("historic CSV source has no time column")

        observations: list[dict[str, Any]] = []
        row_count = 0
        for row in reader:
            if row is None or not any(
                str(value or "").strip() for value in row.values()
            ):
                continue
            row_count += 1
            timestamp = row.get(time_header)
            for header in headers:
                if header == time_header:
                    continue
                value = row.get(header)
                if value is None or not str(value).strip():
                    continue
                observations.append(
                    {
                        "channel": header,
                        "value": value,
                        "timestamp": timestamp,
                    }
                )

        if row_count == 0:
            raise PRTGAdapterError("historic CSV source has no data rows")

        metadata = dict(source_metadata or {})
        source: dict[str, Any] = {
            "objid": sensor_id,
            "parentid": device_id,
            "sensor": metadata.get("sensor", sensor_type),
            "device": metadata.get("device"),
            "group_name": metadata.get("group_name"),
            "server_name": metadata.get("server_name"),
            "status": metadata.get("status"),
            "channels": observations,
        }
        return self.adapt_sensor(
            source,
            server_id=server_id,
            sensor_type=sensor_type,
            source_timezone=source_timezone,
        )

    def _channel_observations(
        self,
        source: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any] | object, ...]:
        if "channels" in source:
            channels = source["channels"]
            if (
                not isinstance(channels, Sequence)
                or isinstance(channels, (str, bytes, bytearray))
            ):
                raise PRTGAdapterError("source channels must be a sequence")
            return tuple(channels)

        if "sensor" not in source or "lastvalue" not in source:
            raise PRTGAdapterError(
                "source must contain channels or a legacy sensor/lastvalue pair"
            )
        return (
            {
                "channel": source.get("sensor"),
                "value": source.get("lastvalue"),
                "unit": source.get("unit"),
                "timestamp": source.get("timestamp"),
                "quality": source.get("status"),
            },
        )

    def _canonical_channel(
        self,
        source_channel: str,
    ) -> tuple[str | None, frozenset[str]]:
        normalized = normalize_source_channel(source_channel)
        custom = self._additional_aliases.get(normalized)
        if custom is not None:
            supported = frozenset(
                sensor_type
                for sensor_type, definition in SENSOR_DEFINITIONS.items()
                if definition.supports(custom)
            )
            return custom, supported

        mapping = resolve_channel(source_channel)
        if mapping is None:
            return None, frozenset()
        return mapping.canonical_key, mapping.sensor_types

    @staticmethod
    def _source_channel(observation: Mapping[str, Any]) -> str | None:
        value = observation.get("channel", observation.get("name"))
        if value is None:
            return None
        clean = str(value).strip()
        return clean or None

    @staticmethod
    def _parse_timestamp(
        value: object,
        *,
        source_timezone: tzinfo | None,
    ) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            text = value.strip()
            if text.endswith(("Z", "z")):
                text = f"{text[:-1]}+00:00"
            parsed = datetime.fromisoformat(text)
        else:
            raise ValueError("timestamp is missing")

        if parsed.tzinfo is None or parsed.utcoffset() is None:
            if source_timezone is None:
                raise ValueError("naive timestamp requires source_timezone")
            parsed = parsed.replace(tzinfo=source_timezone)
            if parsed.utcoffset() is None:
                raise ValueError("source_timezone cannot resolve the timestamp")
        return parsed.astimezone(UTC)

    @staticmethod
    def _parse_value(value: object) -> tuple[float, str | None]:
        if isinstance(value, bool):
            raise TypeError("boolean is not a metric value")
        if isinstance(value, (int, float)):
            numeric = float(value)
            if not isfinite(numeric):
                raise ValueError("metric value must be finite")
            return numeric, None
        if not isinstance(value, str):
            raise TypeError("metric value must be numeric or numeric text")

        match = _NUMERIC_VALUE.match(value)
        if match is None:
            raise ValueError("metric value is not numeric")
        numeric_text = match.group(1)
        if "," in numeric_text and "." in numeric_text:
            numeric_text = numeric_text.replace(",", "")
        elif "," in numeric_text:
            numeric_text = numeric_text.replace(",", ".")
        numeric = float(numeric_text)
        if not isfinite(numeric):
            raise ValueError("metric value must be finite")
        embedded_unit = match.group(2).strip() or None
        return numeric, embedded_unit

    @staticmethod
    def _select_source_unit(
        declared_unit: object,
        embedded_unit: str | None,
    ) -> str | None:
        if declared_unit is not None and str(declared_unit).strip():
            return str(declared_unit).strip()
        return embedded_unit

    @staticmethod
    def _quality(
        observation: Mapping[str, Any],
        source: Mapping[str, Any],
    ) -> str | None:
        value = observation.get("quality", source.get("status"))
        if value is None:
            return None
        clean = str(value).strip()
        return clean or None

    @staticmethod
    def _source_metadata(
        source: Mapping[str, Any],
        sensor_type: str,
    ) -> dict[str, Any]:
        candidates = {
            "source_sensor_type": sensor_type,
            "group_name": source.get(
                "group_name",
                source.get("group", source.get("grupo")),
            ),
            "server_name": source.get("server_name"),
            "device_name": source.get("device"),
            "sensor_name": source.get("sensor"),
            "source_status": source.get("status"),
            "source_status_code": source.get("status_raw"),
        }
        return {
            key: value
            for key, value in candidates.items()
            if value is not None and str(value).strip()
        }


__all__ = [
    "AdapterIssue",
    "AdapterIssueCode",
    "AdapterResult",
    "PRTGAdapterError",
    "PRTGMetricAdapter",
    "UnknownChannel",
]
