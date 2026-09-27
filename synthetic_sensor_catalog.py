from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


SYNTHETIC_SENSOR_FAMILIES = frozenset(
    {"ping", "cpu", "memory", "traffic", "sfp"}
)


@dataclass(frozen=True, slots=True)
class SyntheticSensorKind:
    """One deterministic slot in the synthetic sensor distribution."""

    family: str
    legacy_name: str
    primary_source_channel: str

    def __post_init__(self) -> None:
        if self.family not in SYNTHETIC_SENSOR_FAMILIES:
            raise ValueError(f"unsupported synthetic family: {self.family!r}")
        if not self.legacy_name.strip() or not self.primary_source_channel.strip():
            raise ValueError("synthetic sensor labels cannot be empty")


# One ten-sensor period preserves the Phase 0.3C distribution exactly:
# 40% Ping, 20% CPU, 10% memory, 20% traffic, and 10% SFP.
SYNTHETIC_SENSOR_PATTERN = (
    SyntheticSensorKind("ping", "Ping", "Ping Time"),
    SyntheticSensorKind("ping", "Ping", "Ping Time"),
    SyntheticSensorKind("ping", "Ping", "Ping Time"),
    SyntheticSensorKind("ping", "Ping", "Ping Time"),
    SyntheticSensorKind("cpu", "CPU Load", "CPU Load"),
    SyntheticSensorKind("cpu", "CPU Load", "CPU Load"),
    SyntheticSensorKind("memory", "Memory", "Memory Usage"),
    SyntheticSensorKind("traffic", "Traffic In", "Traffic In"),
    SyntheticSensorKind("traffic", "Traffic Out", "Traffic Out"),
    SyntheticSensorKind("sfp", "SFP Optics", "RX Power"),
)


def sensor_kind_for_ordinal(ordinal: int) -> SyntheticSensorKind:
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("synthetic sensor ordinal must be a non-negative integer")
    return SYNTHETIC_SENSOR_PATTERN[ordinal % len(SYNTHETIC_SENSOR_PATTERN)]


@dataclass(frozen=True, slots=True)
class SyntheticSensorCatalogEntry:
    """Canonical synthetic identity and its single metric family."""

    server_id: str
    server_slug: str
    sensor_id: int
    family: str
    legacy_name: str
    primary_source_channel: str

    def __post_init__(self) -> None:
        if not self.server_id.strip() or not self.server_slug.strip():
            raise ValueError("synthetic server identity cannot be empty")
        if isinstance(self.sensor_id, bool) or int(self.sensor_id) <= 0:
            raise ValueError("synthetic sensor_id must be positive")
        if self.family not in SYNTHETIC_SENSOR_FAMILIES:
            raise ValueError(f"unsupported synthetic family: {self.family!r}")
        if not self.legacy_name.strip() or not self.primary_source_channel.strip():
            raise ValueError("synthetic sensor labels cannot be empty")

    @property
    def identity(self) -> tuple[str, int]:
        return (self.server_id, self.sensor_id)


class SyntheticSensorCatalog:
    """Composite-key catalog shared by legacy and multichannel simulators."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, int], SyntheticSensorCatalogEntry] = {}

    def add(self, entry: SyntheticSensorCatalogEntry) -> None:
        if entry.identity in self._entries:
            raise ValueError(
                "duplicate synthetic sensor identity: "
                f"{entry.server_id}/{entry.sensor_id}"
            )
        self._entries[entry.identity] = entry

    def get(self, server_id: str, sensor_id: int) -> SyntheticSensorCatalogEntry:
        key = (str(server_id).strip(), int(sensor_id))
        try:
            return self._entries[key]
        except KeyError as exc:
            raise KeyError(key) from exc

    def entries(self) -> tuple[SyntheticSensorCatalogEntry, ...]:
        return tuple(self._entries.values())

    def family_counts(self) -> dict[str, int]:
        counts = Counter(entry.family for entry in self._entries.values())
        return {
            family: counts.get(family, 0)
            for family in ("ping", "cpu", "memory", "traffic", "sfp")
        }

    def __len__(self) -> int:
        return len(self._entries)


__all__ = [
    "SYNTHETIC_SENSOR_FAMILIES",
    "SYNTHETIC_SENSOR_PATTERN",
    "SyntheticSensorCatalog",
    "SyntheticSensorCatalogEntry",
    "SyntheticSensorKind",
    "sensor_kind_for_ordinal",
]
