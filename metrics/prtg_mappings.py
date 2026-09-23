from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType

from .definitions import (
    BITS_PER_SECOND,
    BYTES,
    CELSIUS,
    CHANNEL_DEFINITIONS,
    DECIBEL_MILLIWATTS,
    MILLIAMPERES,
    MILLISECONDS,
    PERCENT,
    VOLTS,
)


@dataclass(frozen=True, slots=True)
class PRTGChannelMapping:
    """Declarative mapping from PRTG labels to one canonical channel."""

    canonical_key: str
    aliases: tuple[str, ...]
    sensor_types: frozenset[str]


@dataclass(frozen=True, slots=True)
class UnitConversion:
    """Conversion from a source unit to a canonical storage unit."""

    canonical_unit: str
    factor: float = 1.0


def normalize_source_channel(value: object) -> str:
    """Normalize presentation differences without guessing channel meaning."""

    normalized = str(value or "").strip().casefold().replace("\ufeff", "")
    normalized = re.sub(r"\s*\(raw\)\s*$", "", normalized)
    return " ".join(normalized.split())


def normalize_source_unit(value: object) -> str:
    normalized = str(value or "").strip().casefold().replace("℃", "°c")
    return " ".join(normalized.split())


CHANNEL_MAPPINGS = (
    PRTGChannelMapping(
        "latency_ms",
        ("Ping Time", "Ping", "Latency"),
        frozenset({"ping"}),
    ),
    PRTGChannelMapping(
        "packet_loss_pct",
        ("Packet Loss", "PacketLoss"),
        frozenset({"ping"}),
    ),
    PRTGChannelMapping(
        "availability_pct",
        ("Availability",),
        frozenset({"ping"}),
    ),
    PRTGChannelMapping(
        "cpu_usage_pct",
        ("CPU Load", "CPU Usage"),
        frozenset({"cpu"}),
    ),
    PRTGChannelMapping(
        "memory_usage_pct",
        ("Memory", "Memory Usage"),
        frozenset({"memory"}),
    ),
    PRTGChannelMapping(
        "memory_used_bytes",
        ("Used Memory", "Memory Used"),
        frozenset({"memory"}),
    ),
    PRTGChannelMapping(
        "traffic_in_bps",
        ("Traffic In", "Bandwidth In"),
        frozenset({"traffic"}),
    ),
    PRTGChannelMapping(
        "traffic_out_bps",
        ("Traffic Out", "Bandwidth Out"),
        frozenset({"traffic"}),
    ),
    PRTGChannelMapping(
        "utilization_pct",
        ("Utilization", "Link Utilization"),
        frozenset({"traffic"}),
    ),
    PRTGChannelMapping(
        "rx_power_dbm",
        ("RX Power", "Receive Power"),
        frozenset({"sfp"}),
    ),
    PRTGChannelMapping(
        "tx_power_dbm",
        ("TX Power", "Transmit Power"),
        frozenset({"sfp"}),
    ),
    PRTGChannelMapping(
        "temperature_c",
        ("Temperature", "SFP Temperature"),
        frozenset({"sfp"}),
    ),
    PRTGChannelMapping(
        "voltage_v",
        ("Voltage", "Supply Voltage"),
        frozenset({"sfp"}),
    ),
    PRTGChannelMapping(
        "bias_current_ma",
        ("Bias Current", "TX Bias Current"),
        frozenset({"sfp"}),
    ),
)


def _channel_alias_registry() -> MappingProxyType:
    registry: dict[str, PRTGChannelMapping] = {}
    for mapping in CHANNEL_MAPPINGS:
        if mapping.canonical_key not in CHANNEL_DEFINITIONS:
            raise RuntimeError(
                f"Unknown canonical channel in PRTG mapping: {mapping.canonical_key}"
            )
        for alias in mapping.aliases:
            normalized = normalize_source_channel(alias)
            if normalized in registry:
                raise RuntimeError(f"Duplicate PRTG channel alias: {alias}")
            registry[normalized] = mapping
    return MappingProxyType(registry)


SOURCE_CHANNEL_ALIASES = _channel_alias_registry()


def _unit_registry() -> MappingProxyType:
    groups = (
        (UnitConversion(MILLISECONDS), ("ms", "msec", "millisecond", "milliseconds")),
        (UnitConversion(PERCENT), ("%", "percent")),
        (UnitConversion(BYTES), ("byte", "bytes")),
        (UnitConversion(BYTES, 1_000.0), ("kbyte", "kbytes")),
        (UnitConversion(BYTES, 1_000_000.0), ("mbyte", "mbytes")),
        (UnitConversion(BYTES, 1_000_000_000.0), ("gbyte", "gbytes")),
        (UnitConversion(BITS_PER_SECOND), ("bit/s", "bits/s", "bps")),
        (
            UnitConversion(BITS_PER_SECOND, 1_000.0),
            ("kbit/s", "kbits/s", "kbps"),
        ),
        (
            UnitConversion(BITS_PER_SECOND, 1_000_000.0),
            ("mbit/s", "mbits/s", "mbps"),
        ),
        (
            UnitConversion(BITS_PER_SECOND, 1_000_000_000.0),
            ("gbit/s", "gbits/s", "gbps"),
        ),
        (UnitConversion(DECIBEL_MILLIWATTS), ("dbm",)),
        (UnitConversion(CELSIUS), ("°c", "deg c", "celsius")),
        (UnitConversion(VOLTS), ("v", "volt", "volts")),
        (UnitConversion(MILLIAMPERES), ("ma", "milliamp", "milliamps")),
    )

    registry: dict[str, UnitConversion] = {}
    for conversion, aliases in groups:
        for alias in aliases:
            normalized = normalize_source_unit(alias)
            if normalized in registry:
                raise RuntimeError(f"Duplicate PRTG unit alias: {alias}")
            registry[normalized] = conversion
    return MappingProxyType(registry)


SOURCE_UNIT_CONVERSIONS = _unit_registry()


def resolve_channel(source_channel: object) -> PRTGChannelMapping | None:
    return SOURCE_CHANNEL_ALIASES.get(normalize_source_channel(source_channel))


def resolve_unit(source_unit: object) -> UnitConversion | None:
    return SOURCE_UNIT_CONVERSIONS.get(normalize_source_unit(source_unit))


__all__ = [
    "CHANNEL_MAPPINGS",
    "PRTGChannelMapping",
    "SOURCE_CHANNEL_ALIASES",
    "SOURCE_UNIT_CONVERSIONS",
    "UnitConversion",
    "normalize_source_channel",
    "normalize_source_unit",
    "resolve_channel",
    "resolve_unit",
]
