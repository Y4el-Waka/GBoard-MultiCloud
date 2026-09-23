from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any


NumericValue = int | float


class MetricValueType(StrEnum):
    """Storage type expected for a numeric metric channel."""

    NUMBER = "number"
    INTEGER = "integer"


class MetricSemantic(StrEnum):
    """Small semantic vocabulary for interpreting metric values over time."""

    GAUGE = "gauge"
    COUNTER = "counter"
    RATE = "rate"
    STATE = "state"


def _required_text(value: object, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field_name} must not be empty")
    return clean


def _optional_text(value: object | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _frozen_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a mapping")
    return MappingProxyType(dict(metadata))


def _finite_number(value: object, field_name: str) -> NumericValue:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a numeric value")
    if not isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class SensorIdentity:
    """Stable identity based on configured server ID and provider sensor ID."""

    server_id: str
    sensor_id: str
    device_id: str | None = field(default=None, compare=False, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "server_id",
            _required_text(self.server_id, "server_id"),
        )
        object.__setattr__(
            self,
            "sensor_id",
            _required_text(self.sensor_id, "sensor_id"),
        )
        object.__setattr__(
            self,
            "device_id",
            _optional_text(self.device_id, "device_id"),
        )


@dataclass(frozen=True, slots=True)
class MetricChannelDefinition:
    """Provider-neutral definition of one normalized metric channel."""

    key: str
    label: str
    unit: str
    value_type: MetricValueType = MetricValueType.NUMBER
    semantic: MetricSemantic = MetricSemantic.GAUGE
    description: str | None = None
    expected_min: NumericValue | None = None
    expected_max: NumericValue | None = None
    metadata: Mapping[str, Any] = field(
        default_factory=dict,
        compare=False,
        hash=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _required_text(self.key, "key"))
        object.__setattr__(self, "label", _required_text(self.label, "label"))
        object.__setattr__(self, "unit", _required_text(self.unit, "unit"))
        object.__setattr__(
            self,
            "description",
            _optional_text(self.description, "description"),
        )

        if not isinstance(self.value_type, MetricValueType):
            raise TypeError("value_type must be a MetricValueType")
        if not isinstance(self.semantic, MetricSemantic):
            raise TypeError("semantic must be a MetricSemantic")

        minimum = self.expected_min
        maximum = self.expected_max
        if minimum is not None:
            minimum = _finite_number(minimum, "expected_min")
            object.__setattr__(self, "expected_min", minimum)
        if maximum is not None:
            maximum = _finite_number(maximum, "expected_max")
            object.__setattr__(self, "expected_max", maximum)
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("expected_min must not exceed expected_max")

        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class MetricSample:
    """One normalized numeric observation for a sensor channel."""

    identity: SensorIdentity
    channel_key: str
    value: NumericValue
    timestamp: datetime
    unit: str
    quality: str | None = None
    source_channel: str | None = None
    source_metadata: Mapping[str, Any] = field(
        default_factory=dict,
        compare=False,
        hash=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.identity, SensorIdentity):
            raise TypeError("identity must be a SensorIdentity")

        object.__setattr__(
            self,
            "channel_key",
            _required_text(self.channel_key, "channel_key"),
        )
        object.__setattr__(self, "unit", _required_text(self.unit, "unit"))
        object.__setattr__(self, "value", _finite_number(self.value, "value"))

        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))

        object.__setattr__(
            self,
            "quality",
            _optional_text(self.quality, "quality"),
        )
        object.__setattr__(
            self,
            "source_channel",
            _optional_text(self.source_channel, "source_channel"),
        )
        object.__setattr__(
            self,
            "source_metadata",
            _frozen_metadata(self.source_metadata),
        )


@dataclass(frozen=True, slots=True)
class SensorMetricDefinition:
    """Describes the normalized channel subset exposed by a sensor type."""

    key: str
    label: str
    channels: tuple[MetricChannelDefinition, ...]
    metadata: Mapping[str, Any] = field(
        default_factory=dict,
        compare=False,
        hash=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _required_text(self.key, "key"))
        object.__setattr__(self, "label", _required_text(self.label, "label"))

        channels = tuple(self.channels)
        if not channels:
            raise ValueError("channels must contain at least one definition")
        if not all(isinstance(item, MetricChannelDefinition) for item in channels):
            raise TypeError("channels must contain MetricChannelDefinition values")

        keys = [item.key for item in channels]
        if len(keys) != len(set(keys)):
            raise ValueError("channel keys must be unique within a sensor definition")

        object.__setattr__(self, "channels", channels)
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))

    def supports(self, channel_key: str) -> bool:
        return any(channel.key == channel_key for channel in self.channels)

    def channel(self, channel_key: str) -> MetricChannelDefinition:
        for definition in self.channels:
            if definition.key == channel_key:
                return definition
        raise KeyError(channel_key)
