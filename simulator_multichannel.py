from __future__ import annotations

import hashlib
import math
import random
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


SENSOR_FAMILY_CYCLE = (
    "ping",
    "ping",
    "ping",
    "ping",
    "cpu",
    "cpu",
    "memory",
    "traffic",
    "traffic",
    "sfp",
)

SUPPORTED_SCENARIOS = frozenset(
    {
        "normal",
        "cpu_spike",
        "traffic_saturation",
        "sfp_degradation",
        "packet_loss",
        "partial_channels",
        "unknown_channel",
        "invalid_value",
    }
)

SUPPORTED_FAULTS = frozenset(
    {
        "n_a",
        "dashes",
        "unknown_unit",
        "invalid_timestamp",
    }
)

FAMILY_DISPLAY_NAMES = {
    "ping": "Ping",
    "cpu": "CPU Load",
    "memory": "Memory",
    "traffic": "Traffic",
    "sfp": "SFP Optics",
}

DEFAULT_START_TIME = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class MetricSensorProfile:
    server_slug: str
    server_id: str
    group_name: str
    sensor_id: int
    parent_id: int
    device_name: str
    family: str
    phase: float
    parameters: Mapping[str, float] = field(repr=False)
    sfp_channel_count: int = 5
    legacy_sensor: Any = field(default=None, compare=False, repr=False)


class SyntheticMultichannelSimulator:
    """Deterministic PRTG-like multichannel source with a logical clock."""

    def __init__(
        self,
        servers: Mapping[str, Any],
        *,
        seed: int,
        scenario: str = "normal",
        tick_seconds: int = 300,
        start_time: datetime | None = None,
    ) -> None:
        if isinstance(tick_seconds, bool) or int(tick_seconds) <= 0:
            raise ValueError("tick_seconds must be a positive integer")

        selected_start = start_time or DEFAULT_START_TIME
        if selected_start.tzinfo is None or selected_start.utcoffset() is None:
            raise ValueError("start_time must be timezone-aware")

        self.seed = int(seed)
        self.tick_seconds = int(tick_seconds)
        self.start_time = selected_start.astimezone(UTC)
        self._scenario = self._validate_scenario(scenario)
        self._tick = 0
        self._lock = threading.RLock()
        self._profiles = self._build_profiles(servers)

    @property
    def scenario(self) -> str:
        with self._lock:
            return self._scenario

    @property
    def current_tick(self) -> int:
        with self._lock:
            return self._tick

    @property
    def current_time(self) -> datetime:
        with self._lock:
            return self._timestamp_at(self._tick)

    def set_scenario(self, scenario: str) -> None:
        selected = self._validate_scenario(scenario)
        with self._lock:
            self._scenario = selected

    def tick(self, steps: int = 1) -> datetime:
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
            raise ValueError("steps must be a positive integer")
        with self._lock:
            self._tick += steps
            return self._timestamp_at(self._tick)

    def sensor_ids(self, server_slug: str, family: str | None = None) -> tuple[int, ...]:
        profiles = self._server_profiles(server_slug)
        selected_family = None if family is None else self._validate_family(family)
        return tuple(
            profile.sensor_id
            for profile in profiles.values()
            if selected_family is None or profile.family == selected_family
        )

    def family_counts(self, server_slug: str | None = None) -> dict[str, int]:
        counts = {family: 0 for family in FAMILY_DISPLAY_NAMES}
        profile_groups = (
            (self._server_profiles(server_slug),)
            if server_slug is not None
            else tuple(self._profiles.values())
        )
        for profiles in profile_groups:
            for profile in profiles.values():
                counts[profile.family] += 1
        return counts

    def sensor_source(
        self,
        server_slug: str,
        sensor_id: int,
        *,
        scenario: str | None = None,
        fault: str | None = None,
        tick: int | None = None,
    ) -> dict[str, Any]:
        selected_scenario = self._validate_scenario(scenario or self.scenario)
        selected_fault = self._validate_fault(fault)
        profiles = self._server_profiles(server_slug)
        try:
            profile = profiles[int(sensor_id)]
        except (KeyError, TypeError, ValueError) as exc:
            raise KeyError(sensor_id) from exc

        with self._lock:
            selected_tick = self._tick if tick is None else int(tick)
            return self._source_for_profile(
                profile,
                tick=selected_tick,
                scenario=selected_scenario,
                fault=selected_fault,
            )

    def server_source(
        self,
        server_slug: str,
        *,
        scenario: str | None = None,
        fault: str | None = None,
    ) -> dict[str, Any]:
        selected_scenario = self._validate_scenario(scenario or self.scenario)
        selected_fault = self._validate_fault(fault)
        profiles = self._server_profiles(server_slug)
        with self._lock:
            tick = self._tick
            sensors = [
                self._source_for_profile(
                    profile,
                    tick=tick,
                    scenario=selected_scenario,
                    fault=selected_fault,
                )
                for profile in profiles.values()
            ]
        first = next(iter(profiles.values()), None)
        return {
            "synthetic": True,
            "server_id": None if first is None else first.server_id,
            "server_name": None if first is None else first.group_name,
            "scenario": selected_scenario,
            "tick": tick,
            "tick_seconds": self.tick_seconds,
            "timestamp": self._format_timestamp(self._timestamp_at(tick)),
            "sensors": sensors,
        }

    def historic_source(
        self,
        server_slug: str,
        sensor_id: int,
        *,
        points: int = 12,
        scenario: str | None = None,
        fault: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(points, bool) or not isinstance(points, int) or not 1 <= points <= 1000:
            raise ValueError("points must be between 1 and 1000")
        selected_scenario = self._validate_scenario(scenario or self.scenario)
        selected_fault = self._validate_fault(fault)
        profiles = self._server_profiles(server_slug)
        try:
            profile = profiles[int(sensor_id)]
        except (KeyError, TypeError, ValueError) as exc:
            raise KeyError(sensor_id) from exc

        with self._lock:
            end_tick = self._tick
            ticks = range(end_tick - points + 1, end_tick + 1)
            samples = [
                self._source_for_profile(
                    profile,
                    tick=tick,
                    scenario=selected_scenario,
                    fault=selected_fault,
                )
                for tick in ticks
            ]
        return {
            "synthetic": True,
            "server_id": profile.server_id,
            "server_name": profile.group_name,
            "sensor_id": profile.sensor_id,
            "sensor_type": profile.family,
            "scenario": selected_scenario,
            "tick_seconds": self.tick_seconds,
            "samples": samples,
        }

    def _build_profiles(
        self,
        servers: Mapping[str, Any],
    ) -> dict[str, dict[int, MetricSensorProfile]]:
        profiles: dict[str, dict[int, MetricSensorProfile]] = {}
        ordinal = 0
        sfp_ordinal = 0
        for server_slug, server in servers.items():
            server_profiles: dict[int, MetricSensorProfile] = {}
            for legacy_sensor in server.sensors.values():
                family = SENSOR_FAMILY_CYCLE[ordinal % len(SENSOR_FAMILY_CYCLE)]
                rng = random.Random(
                    self._derived_seed(server_slug, legacy_sensor.objid)
                )
                parameters = {
                    "latency_base": rng.uniform(8.0, 42.0),
                    "packet_loss_base": rng.uniform(0.0, 0.6),
                    "cpu_base": rng.uniform(25.0, 62.0),
                    "memory_base": rng.uniform(36.0, 72.0),
                    "memory_total": rng.choice((8.0, 16.0, 32.0)) * 1_000_000_000,
                    "traffic_capacity": rng.choice((1.0, 2.5, 10.0)) * 1_000_000_000,
                    "traffic_in_base": rng.uniform(0.08, 0.35),
                    "traffic_out_base": rng.uniform(0.03, 0.20),
                    "rx_power_base": rng.uniform(-5.2, -2.8),
                    "tx_power_base": rng.uniform(-3.0, -1.2),
                    "temperature_base": rng.uniform(31.0, 43.0),
                    "voltage_base": rng.uniform(3.27, 3.34),
                    "bias_base": rng.uniform(5.2, 8.4),
                }
                sfp_counts = (5, 3, 2, 1)
                sfp_channel_count = 5
                if family == "sfp":
                    sfp_channel_count = sfp_counts[sfp_ordinal % len(sfp_counts)]
                    sfp_ordinal += 1
                server_profiles[legacy_sensor.objid] = MetricSensorProfile(
                    server_slug=server_slug,
                    server_id=f"srv-{server_slug}",
                    group_name=server.name,
                    sensor_id=legacy_sensor.objid,
                    parent_id=legacy_sensor.parentid,
                    device_name=legacy_sensor.device,
                    family=family,
                    phase=rng.uniform(0.0, math.tau),
                    parameters=parameters,
                    sfp_channel_count=sfp_channel_count,
                    legacy_sensor=legacy_sensor,
                )
                ordinal += 1
            profiles[server_slug] = server_profiles
        return profiles

    def _source_for_profile(
        self,
        profile: MetricSensorProfile,
        *,
        tick: int,
        scenario: str,
        fault: str | None,
    ) -> dict[str, Any]:
        timestamp = self._format_timestamp(self._timestamp_at(tick))
        channels = self._channels(profile, tick=tick, scenario=scenario)

        if scenario == "unknown_channel":
            channels.append(
                {
                    "channel": "Laser Health Index",
                    "value": f"{self._number(96.0 + math.sin(tick * 0.2), 2)} index",
                }
            )

        effective_fault = fault
        if scenario == "invalid_value" and effective_fault is None:
            effective_fault = "n_a"
        if effective_fault is not None and channels:
            channels = [dict(channel) for channel in channels]
            if effective_fault == "n_a":
                channels[0]["value"] = "N/A"
            elif effective_fault == "dashes":
                channels[0]["value"] = "---"
            elif effective_fault == "unknown_unit":
                channels[0]["value"] = "42 widgets"
            elif effective_fault == "invalid_timestamp":
                channels[0]["timestamp"] = "not-a-timestamp"

        legacy = profile.legacy_sensor.as_prtg()
        return {
            "server_id": profile.server_id,
            "server_name": profile.group_name,
            "sensor_type": profile.family,
            "objid": profile.sensor_id,
            "parentid": profile.parent_id,
            "device": profile.device_name,
            "sensor": FAMILY_DISPLAY_NAMES[profile.family],
            "group_name": profile.group_name,
            "status": legacy["status"],
            "status_raw": legacy["status_raw"],
            "message": legacy["message"],
            "timestamp": timestamp,
            "channels": channels,
        }

    def _channels(
        self,
        profile: MetricSensorProfile,
        *,
        tick: int,
        scenario: str,
    ) -> list[dict[str, str]]:
        family = profile.family
        if family == "ping":
            return self._ping_channels(profile, tick, scenario)
        if family == "cpu":
            return self._cpu_channels(profile, tick, scenario)
        if family == "memory":
            return self._memory_channels(profile, tick)
        if family == "traffic":
            return self._traffic_channels(profile, tick, scenario)
        return self._sfp_channels(profile, tick, scenario)

    def _ping_channels(
        self,
        profile: MetricSensorProfile,
        tick: int,
        scenario: str,
    ) -> list[dict[str, str]]:
        params = profile.parameters
        latency = (
            params["latency_base"]
            + 3.2 * math.sin(profile.phase + tick * 0.55)
            + 0.8 * math.sin(profile.phase * 0.5 + tick * 0.17)
        )
        loss = params["packet_loss_base"]
        if scenario == "packet_loss":
            loss = min(45.0, loss + max(0, tick) * 3.5)
            latency += loss * 0.45
        elif (tick + profile.sensor_id) % 29 in {0, 1}:
            loss += 2.5
        availability = max(0.0, 100.0 - loss * 0.2)
        return [
            {"channel": "Ping Time", "value": f"{self._number(max(0.5, latency), 2)} ms"},
            {"channel": "Packet Loss", "value": f"{self._number(loss, 2)} %"},
            {"channel": "Availability", "value": f"{self._number(availability, 3)} %"},
        ]

    def _cpu_channels(
        self,
        profile: MetricSensorProfile,
        tick: int,
        scenario: str,
    ) -> list[dict[str, str]]:
        base = profile.parameters["cpu_base"]
        if scenario == "cpu_spike":
            usage = min(99.0, base + max(0, tick) * 7.0)
        else:
            usage = base + 10.0 * math.sin(profile.phase + tick * 0.34)
            usage += 2.2 * math.sin(tick * 0.11)
        return [
            {"channel": "CPU Load", "value": f"{self._number(self._clamp(usage, 1.0, 99.0), 2)} %"}
        ]

    def _memory_channels(
        self,
        profile: MetricSensorProfile,
        tick: int,
    ) -> list[dict[str, str]]:
        params = profile.parameters
        usage = params["memory_base"] + tick * 0.12
        usage += 1.4 * math.sin(profile.phase + tick * 0.12)
        usage = self._clamp(usage, 5.0, 96.0)
        used_bytes = round(params["memory_total"] * usage / 100.0)
        return [
            {"channel": "Memory Usage", "value": f"{self._number(usage, 2)} %"},
            {
                "channel": "Used Memory",
                "value": self._scaled_value(
                    used_bytes,
                    (("KByte", 1_000.0), ("MByte", 1_000_000.0), ("GByte", 1_000_000_000.0)),
                    profile.sensor_id + tick,
                    precision=3,
                ),
            },
        ]

    def _traffic_channels(
        self,
        profile: MetricSensorProfile,
        tick: int,
        scenario: str,
    ) -> list[dict[str, str]]:
        params = profile.parameters
        capacity = params["traffic_capacity"]
        if scenario == "traffic_saturation":
            inbound_ratio = min(0.97, params["traffic_in_base"] + max(0, tick) * 0.085)
            outbound_ratio = min(0.92, params["traffic_out_base"] + max(0, tick) * 0.065)
        else:
            inbound_ratio = params["traffic_in_base"] + 0.12 * math.sin(
                profile.phase + tick * 0.42
            )
            outbound_ratio = params["traffic_out_base"] + 0.07 * math.sin(
                profile.phase * 0.7 + tick * 0.29
            )
            if (tick + profile.sensor_id) % 19 == 0:
                inbound_ratio *= 1.8
                outbound_ratio *= 1.35

        inbound = capacity * self._clamp(inbound_ratio, 0.002, 0.98)
        outbound = capacity * self._clamp(outbound_ratio, 0.001, 0.95)
        utilization = max(inbound, outbound) / capacity * 100.0
        units = (
            ("Kbit/s", 1_000.0),
            ("Mbit/s", 1_000_000.0),
            ("Gbit/s", 1_000_000_000.0),
        )
        return [
            {
                "channel": "Traffic In",
                "value": self._scaled_value(inbound, units, profile.sensor_id + tick),
                "kind": "rate",
            },
            {
                "channel": "Traffic Out",
                "value": self._scaled_value(outbound, units, profile.sensor_id + tick + 1),
                "kind": "rate",
            },
            {"channel": "Utilization", "value": f"{self._number(utilization, 2)} %"},
        ]

    def _sfp_channels(
        self,
        profile: MetricSensorProfile,
        tick: int,
        scenario: str,
    ) -> list[dict[str, str]]:
        params = profile.parameters
        degradation = max(0, tick) * (0.16 if scenario == "sfp_degradation" else 0.012)
        rx = params["rx_power_base"] - degradation + 0.05 * math.sin(
            profile.phase + tick * 0.18
        )
        tx = params["tx_power_base"] - degradation * 0.65 + 0.04 * math.sin(
            profile.phase * 0.8 + tick * 0.16
        )
        temperature = params["temperature_base"] + 0.35 * math.sin(
            profile.phase + tick * 0.09
        )
        voltage = params["voltage_base"] + 0.008 * math.sin(tick * 0.07)
        bias = params["bias_base"] + 0.12 * math.sin(profile.phase + tick * 0.11)
        channels = [
            {"channel": "RX Power", "value": f"{self._number(rx, 3)} dBm"},
            {"channel": "TX Power", "value": f"{self._number(tx, 3)} dBm"},
            {"channel": "Temperature", "value": f"{self._number(temperature, 2)} °C"},
            {"channel": "Voltage", "value": f"{self._number(voltage, 3)} V"},
            {"channel": "Bias Current", "value": f"{self._number(bias, 3)} mA"},
        ]
        count = profile.sfp_channel_count
        if scenario == "partial_channels":
            count = 1 + profile.sensor_id % 3
        return channels[:count]

    def _derived_seed(self, server_slug: str, sensor_id: int) -> int:
        source = f"{self.seed}:{server_slug}:{sensor_id}:multichannel".encode("utf-8")
        return int.from_bytes(hashlib.sha256(source).digest()[:8], "big")

    def _server_profiles(self, server_slug: str) -> dict[int, MetricSensorProfile]:
        try:
            return self._profiles[server_slug]
        except KeyError as exc:
            raise KeyError(server_slug) from exc

    @staticmethod
    def _validate_scenario(scenario: str) -> str:
        selected = str(scenario or "").strip().casefold()
        if selected not in SUPPORTED_SCENARIOS:
            raise ValueError(f"unsupported scenario: {scenario!r}")
        return selected

    @staticmethod
    def _validate_fault(fault: str | None) -> str | None:
        if fault is None:
            return None
        selected = str(fault).strip().casefold()
        if selected not in SUPPORTED_FAULTS:
            raise ValueError(f"unsupported fault: {fault!r}")
        return selected

    @staticmethod
    def _validate_family(family: str) -> str:
        selected = str(family or "").strip().casefold()
        if selected not in FAMILY_DISPLAY_NAMES:
            raise ValueError(f"unsupported sensor family: {family!r}")
        return selected

    def _timestamp_at(self, tick: int) -> datetime:
        return self.start_time + timedelta(seconds=tick * self.tick_seconds)

    @staticmethod
    def _format_timestamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _number(value: float, precision: int) -> str:
        return f"{value:.{precision}f}".rstrip("0").rstrip(".")

    @classmethod
    def _scaled_value(
        cls,
        value: float,
        units: tuple[tuple[str, float], ...],
        selector: int,
        *,
        precision: int = 6,
    ) -> str:
        unit, factor = units[selector % len(units)]
        return f"{cls._number(value / factor, precision)} {unit}"

    @staticmethod
    def _clamp(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))


__all__ = [
    "DEFAULT_START_TIME",
    "FAMILY_DISPLAY_NAMES",
    "MetricSensorProfile",
    "SENSOR_FAMILY_CYCLE",
    "SUPPORTED_FAULTS",
    "SUPPORTED_SCENARIOS",
    "SyntheticMultichannelSimulator",
]
