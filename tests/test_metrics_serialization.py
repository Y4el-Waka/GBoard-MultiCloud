from __future__ import annotations

import ast
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from history_service import NormalizedHistoryResult
from metrics.definitions import get_sensor_definition
from metrics.models import MetricSample, SensorIdentity
from metrics.prtg_adapter import AdapterIssue, AdapterIssueCode, UnknownChannel
from metrics.serialization import (
    METRICS_HISTORY_SCHEMA_VERSION,
    serialize_normalized_history,
    utc_isoformat,
)


class MetricsSerializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = SensorIdentity(
            server_id="srv-synthetic-01",
            sensor_id="100001",
            device_id="200001",
        )
        self.start = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        self.end = self.start + timedelta(minutes=5)

    def result(
        self,
        sensor_type: str,
        channel_values: dict[str, tuple[float | int, float | int]],
        *,
        issues: tuple[AdapterIssue, ...] = (),
        unknown_channels: tuple[UnknownChannel, ...] = (),
        secret_metadata: bool = False,
    ) -> NormalizedHistoryResult:
        definition = get_sensor_definition(sensor_type)
        channels = tuple(
            channel
            for channel in definition.channels
            if channel.key in channel_values
        )
        samples = []
        for timestamp_index, timestamp in enumerate((self.start, self.end)):
            for channel in channels:
                metadata = (
                    {
                        "password": "must-not-serialize",
                        "passhash": "must-not-serialize",
                        "url": "https://synthetic.invalid/?password=secret",
                    }
                    if secret_metadata
                    else {}
                )
                samples.append(
                    MetricSample(
                        identity=self.identity,
                        channel_key=channel.key,
                        value=channel_values[channel.key][timestamp_index],
                        timestamp=timestamp,
                        unit=channel.unit,
                        source_channel=channel.label,
                        source_metadata=metadata,
                    )
                )
        return NormalizedHistoryResult(
            identity=self.identity,
            sensor_type=sensor_type,
            samples=tuple(samples),
            channels=channels,
            issues=issues,
            unknown_channels=unknown_channels,
            start=self.start,
            end=self.end,
        )

    def test_ping_cpu_and_memory_contracts(self) -> None:
        cases = {
            "ping": {
                "latency_ms": (12.5, 14.0),
                "packet_loss_pct": (0.0, 1.0),
                "availability_pct": (100.0, 99.8),
            },
            "cpu": {"cpu_usage_pct": (35.0, 48.0)},
            "memory": {
                "memory_usage_pct": (55.0, 56.0),
                "memory_used_bytes": (8_800_000_000, 8_960_000_000),
            },
        }

        for sensor_type, values in cases.items():
            with self.subTest(sensor_type=sensor_type):
                payload = serialize_normalized_history(
                    self.result(sensor_type, values)
                )
                self.assertEqual(
                    payload["schema_version"],
                    METRICS_HISTORY_SCHEMA_VERSION,
                )
                self.assertEqual(payload["sensor"]["sensor_type"], sensor_type)
                self.assertEqual(set(payload["channels"]), set(values))

    def test_traffic_channels_remain_separate_numeric_bps_series(self) -> None:
        payload = serialize_normalized_history(
            self.result(
                "traffic",
                {
                    "traffic_in_bps": (85_000_000, 91_000_000),
                    "traffic_out_bps": (22_000_000, 26_000_000),
                    "utilization_pct": (8.5, 9.1),
                },
            )
        )
        inbound = payload["channels"]["traffic_in_bps"]
        outbound = payload["channels"]["traffic_out_bps"]

        self.assertEqual(inbound["unit"], "bps")
        self.assertEqual(outbound["unit"], "bps")
        self.assertNotEqual(inbound["samples"], outbound["samples"])
        self.assertTrue(
            all(
                isinstance(sample["value"], (int, float))
                and not isinstance(sample["value"], bool)
                for channel in payload["channels"].values()
                for sample in channel["samples"]
            )
        )

    def test_full_and_partial_sfp_contracts(self) -> None:
        full_values = {
            "rx_power_dbm": (-3.8, -3.9),
            "tx_power_dbm": (-2.0, -2.1),
            "temperature_c": (34.0, 34.2),
            "voltage_v": (3.3, 3.31),
            "bias_current_ma": (6.1, 6.2),
        }
        full = serialize_normalized_history(self.result("sfp", full_values))
        partial = serialize_normalized_history(
            self.result(
                "sfp",
                {
                    "rx_power_dbm": full_values["rx_power_dbm"],
                    "tx_power_dbm": full_values["tx_power_dbm"],
                },
            )
        )

        self.assertEqual(set(full["channels"]), set(full_values))
        self.assertEqual(
            set(partial["channels"]),
            {"rx_power_dbm", "tx_power_dbm"},
        )

    def test_channel_metadata_comes_from_domain_definitions(self) -> None:
        payload = serialize_normalized_history(
            self.result("cpu", {"cpu_usage_pct": (32.0, 44.0)})
        )
        channel = payload["channels"]["cpu_usage_pct"]

        self.assertEqual(channel["label"], "CPU usage")
        self.assertEqual(channel["unit"], "%")
        self.assertEqual(channel["semantic"], "gauge")
        self.assertEqual(channel["value_type"], "number")

    def test_timestamps_are_utc_z_and_chronological(self) -> None:
        payload = serialize_normalized_history(
            self.result("ping", {"latency_ms": (10.0, 12.0)})
        )
        timestamps = [
            sample["timestamp"]
            for sample in payload["channels"]["latency_ms"]["samples"]
        ]

        self.assertEqual(
            timestamps,
            ["2026-09-23T12:00:00Z", "2026-09-23T12:05:00Z"],
        )
        self.assertEqual(payload["range"]["start"], timestamps[0])
        self.assertEqual(payload["range"]["end"], timestamps[-1])

    def test_utc_serializer_converts_offsets_and_rejects_naive_values(self) -> None:
        offset_value = datetime(
            2026,
            9,
            23,
            6,
            0,
            tzinfo=timezone(timedelta(hours=-6)),
        )
        self.assertEqual(
            utc_isoformat(offset_value),
            "2026-09-23T12:00:00Z",
        )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            utc_isoformat(datetime(2026, 9, 23, 12, 0))

    def test_issues_and_unknown_channels_have_safe_explicit_contracts(self) -> None:
        result = self.result(
            "sfp",
            {"rx_power_dbm": (-3.8, -3.9)},
            issues=(
                AdapterIssue(
                    code=AdapterIssueCode.INVALID_VALUE,
                    message="channel value is not a finite numeric observation",
                    channel_index=1,
                    source_channel="Temperature",
                ),
            ),
            unknown_channels=(
                UnknownChannel(
                    source_channel="Laser Health Index",
                    source_unit="%",
                    channel_index=2,
                ),
            ),
        )
        payload = serialize_normalized_history(result)

        self.assertEqual(
            payload["issues"],
            [
                {
                    "code": "invalid_value",
                    "channel": "Temperature",
                    "channel_index": 1,
                    "message": (
                        "channel value is not a finite numeric observation"
                    ),
                }
            ],
        )
        self.assertEqual(
            payload["unknown_channels"],
            [
                {
                    "source_channel": "Laser Health Index",
                    "source_unit": "%",
                    "channel_index": 2,
                    "reason": "unmapped_channel",
                }
            ],
        )

    def test_serializer_ignores_source_secrets_and_presentation_details(self) -> None:
        payload = serialize_normalized_history(
            self.result(
                "traffic",
                {"traffic_in_bps": (1_000_000, 2_000_000)},
                secret_metadata=True,
            )
        )
        serialized = repr(payload).casefold()

        self.assertNotIn("must-not-serialize", serialized)
        self.assertNotIn("password", serialized)
        self.assertNotIn("passhash", serialized)
        self.assertNotIn("synthetic.invalid", serialized)
        self.assertNotIn("axis", serialized)
        self.assertNotIn("color", serialized)

    def test_diagnostic_fields_redact_secret_values_and_urls(self) -> None:
        result = self.result(
            "ping",
            {"latency_ms": (10.0, 11.0)},
            issues=(
                AdapterIssue(
                    code=AdapterIssueCode.INVALID_VALUE,
                    message="password=must-not-serialize",
                    channel_index=1,
                    source_channel="token=must-not-serialize",
                ),
            ),
            unknown_channels=(
                UnknownChannel(
                    source_channel=(
                        "https://synthetic.invalid/channel?passhash=must-not-serialize"
                    ),
                    source_unit="secret=must-not-serialize",
                    channel_index=2,
                ),
            ),
        )

        serialized = repr(serialize_normalized_history(result)).casefold()
        self.assertNotIn("must-not-serialize", serialized)
        self.assertNotIn("synthetic.invalid", serialized)
        self.assertIn("[redacted]", serialized)
        self.assertIn("[redacted-url]", serialized)
        self.assertNotIn("chart", serialized)

    def test_serializer_has_no_flask_dependency(self) -> None:
        path = Path(__file__).resolve().parents[1] / "metrics" / "serialization.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertNotIn("flask", imports)


if __name__ == "__main__":
    unittest.main()
