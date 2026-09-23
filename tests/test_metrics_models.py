from __future__ import annotations

import ast
import json
import math
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from metrics import (
    BITS_PER_SECOND,
    BYTES,
    CANONICAL_UNITS,
    CELSIUS,
    CHANNEL_DEFINITIONS,
    CPU_DEFINITION,
    DECIBEL_MILLIWATTS,
    MEMORY_DEFINITION,
    MILLIAMPERES,
    MILLISECONDS,
    MetricChannelDefinition,
    MetricSample,
    MetricSemantic,
    MetricValueType,
    PERCENT,
    PING_DEFINITION,
    SENSOR_DEFINITIONS,
    SFP_DEFINITION,
    SensorIdentity,
    SensorMetricDefinition,
    TRAFFIC_DEFINITION,
    VOLTS,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "multichannel_metrics.json"


class MetricsDomainModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_sensor_identity_equality_and_hash_ignore_mutable_labels(self) -> None:
        first = SensorIdentity(
            server_id="srv_synthetic_01",
            sensor_id="sensor_ping_001",
            device_id="device_before",
        )
        after_device_rename = SensorIdentity(
            server_id="srv_synthetic_01",
            sensor_id="sensor_ping_001",
            device_id="device_after",
        )
        group_metadata_before = {"group_name": "PRTG LAB 01"}
        group_metadata_after = {"group_name": "RENAMED SYNTHETIC LAB"}

        self.assertEqual(first, after_device_rename)
        self.assertEqual(hash(first), hash(after_device_rename))
        self.assertNotEqual(group_metadata_before, group_metadata_after)
        self.assertEqual(len({first, after_device_rename}), 1)

    def test_sensor_identity_rejects_missing_canonical_parts(self) -> None:
        for server_id, sensor_id in (("", "sensor"), ("server", "")):
            with self.subTest(server_id=server_id, sensor_id=sensor_id):
                with self.assertRaises(ValueError):
                    SensorIdentity(server_id=server_id, sensor_id=sensor_id)

        with self.assertRaises(ValueError):
            SensorIdentity("server", "sensor", device_id="  ")

    def test_metric_channel_definition_is_typed_and_descriptive(self) -> None:
        channel = MetricChannelDefinition(
            key="fan_speed_rpm",
            label="Fan speed",
            unit="rpm",
            value_type=MetricValueType.INTEGER,
            semantic=MetricSemantic.GAUGE,
            expected_min=0,
            metadata={"synthetic": True},
        )

        self.assertEqual(channel.key, "fan_speed_rpm")
        self.assertEqual(channel.unit, "rpm")
        self.assertEqual(channel.value_type, MetricValueType.INTEGER)
        self.assertEqual(channel.semantic, MetricSemantic.GAUGE)
        self.assertTrue(channel.metadata["synthetic"])
        with self.assertRaises(TypeError):
            channel.metadata["changed"] = True

    def test_metric_sample_is_numeric_and_normalized_to_utc(self) -> None:
        source_timezone = timezone(timedelta(hours=-6))
        sample = MetricSample(
            identity=SensorIdentity("srv_synthetic_01", "sensor_cpu_001"),
            channel_key="cpu_usage_pct",
            value=48.5,
            timestamp=datetime(2026, 9, 23, 6, 0, tzinfo=source_timezone),
            unit=PERCENT,
            quality="good",
            source_channel="synthetic_cpu_usage",
        )

        self.assertIsInstance(sample.value, float)
        self.assertEqual(sample.timestamp, datetime(2026, 9, 23, 12, 0, tzinfo=UTC))
        self.assertEqual(sample.timestamp.tzinfo, UTC)
        self.assertEqual(sample.unit, "%")

    def test_metric_sample_rejects_naive_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            MetricSample(
                identity=SensorIdentity("server", "sensor"),
                channel_key="latency_ms",
                value=10.0,
                timestamp=datetime(2026, 9, 23, 12, 0),
                unit=MILLISECONDS,
            )

    def test_metric_sample_rejects_non_numeric_or_non_finite_values(self) -> None:
        invalid_values = ("42 %", True, math.nan, math.inf, -math.inf)

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    MetricSample(
                        identity=SensorIdentity("server", "sensor"),
                        channel_key="custom_metric",
                        value=value,
                        timestamp=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
                        unit="custom-unit",
                    )

    def test_expected_channel_limits_are_metadata_not_sample_enforcement(self) -> None:
        sample = MetricSample(
            identity=SensorIdentity("server", "sensor"),
            channel_key="cpu_usage_pct",
            value=125.0,
            timestamp=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
            unit=PERCENT,
        )

        self.assertEqual(CHANNEL_DEFINITIONS["cpu_usage_pct"].expected_max, 100)
        self.assertEqual(sample.value, 125.0)

    def test_canonical_units_are_storage_units_not_display_formatting(self) -> None:
        self.assertEqual(
            CANONICAL_UNITS,
            {
                MILLISECONDS,
                PERCENT,
                BYTES,
                BITS_PER_SECOND,
                DECIBEL_MILLIWATTS,
                CELSIUS,
                VOLTS,
                MILLIAMPERES,
            },
        )
        self.assertNotIn("Mbps", CANONICAL_UNITS)

    def test_ping_definition(self) -> None:
        self.assertEqual(
            {channel.key for channel in PING_DEFINITION.channels},
            {"latency_ms", "packet_loss_pct", "availability_pct"},
        )

    def test_cpu_definition(self) -> None:
        self.assertEqual(
            [channel.key for channel in CPU_DEFINITION.channels],
            ["cpu_usage_pct"],
        )

    def test_memory_definition(self) -> None:
        self.assertEqual(
            {channel.key for channel in MEMORY_DEFINITION.channels},
            {"memory_usage_pct", "memory_used_bytes"},
        )
        self.assertEqual(
            MEMORY_DEFINITION.channel("memory_used_bytes").value_type,
            MetricValueType.INTEGER,
        )

    def test_traffic_definition_distinguishes_rates(self) -> None:
        self.assertEqual(
            {channel.key for channel in TRAFFIC_DEFINITION.channels},
            {"traffic_in_bps", "traffic_out_bps", "utilization_pct"},
        )
        self.assertEqual(
            TRAFFIC_DEFINITION.channel("traffic_in_bps").semantic,
            MetricSemantic.RATE,
        )
        self.assertEqual(
            TRAFFIC_DEFINITION.channel("traffic_out_bps").semantic,
            MetricSemantic.RATE,
        )

    def test_sfp_definition(self) -> None:
        self.assertEqual(
            {channel.key for channel in SFP_DEFINITION.channels},
            {
                "rx_power_dbm",
                "tx_power_dbm",
                "temperature_c",
                "voltage_v",
                "bias_current_ma",
            },
        )

    def test_sensor_definitions_support_optional_channel_subsets(self) -> None:
        receive_only_sfp = SensorMetricDefinition(
            key="sfp_receive_only",
            label="Receive-only optics",
            channels=(CHANNEL_DEFINITIONS["rx_power_dbm"],),
        )

        self.assertTrue(receive_only_sfp.supports("rx_power_dbm"))
        self.assertFalse(receive_only_sfp.supports("tx_power_dbm"))
        with self.assertRaises(KeyError):
            receive_only_sfp.channel("tx_power_dbm")

    def test_custom_channel_extends_model_without_core_changes(self) -> None:
        custom_channel = MetricChannelDefinition(
            key="jitter_ms",
            label="Jitter",
            unit=MILLISECONDS,
            semantic=MetricSemantic.GAUGE,
        )
        custom_sensor = SensorMetricDefinition(
            key="custom_network_quality",
            label="Custom network quality",
            channels=(custom_channel,),
        )

        self.assertTrue(custom_sensor.supports("jitter_ms"))
        self.assertNotIn("jitter_ms", CHANNEL_DEFINITIONS)

    def test_invalid_definitions_are_rejected(self) -> None:
        channel = MetricChannelDefinition("metric", "Metric", "unit")

        with self.assertRaises(ValueError):
            MetricChannelDefinition("", "Metric", "unit")
        with self.assertRaises(ValueError):
            MetricChannelDefinition(
                "metric",
                "Metric",
                "unit",
                expected_min=10,
                expected_max=1,
            )
        with self.assertRaises(ValueError):
            SensorMetricDefinition("empty", "Empty", ())
        with self.assertRaises(ValueError):
            SensorMetricDefinition("duplicate", "Duplicate", (channel, channel))

    def test_multichannel_fixture_covers_definitions_and_valid_time_series(self) -> None:
        timestamps = [
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            for value in self.fixture["timestamps"]
        ]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(len(timestamps), 7)
        self.assertTrue(all(value.tzinfo is not None for value in timestamps))

        fixture_types = {sensor["sensor_type"] for sensor in self.fixture["sensors"]}
        self.assertEqual(fixture_types, {"ping", "cpu", "memory", "traffic", "sfp"})

        sample_count = 0
        for sensor in self.fixture["sensors"]:
            definition = SENSOR_DEFINITIONS[sensor["sensor_type"]]
            identity = SensorIdentity(**sensor["identity"])
            self.assertTrue(identity.server_id.startswith("srv_synthetic_"))
            self.assertRegex(sensor["metadata"]["group_name"], r"^PRTG LAB \d{2}$")

            for channel_key, series in sensor["channels"].items():
                channel = definition.channel(channel_key)
                self.assertEqual(series["unit"], channel.unit)
                self.assertEqual(len(series["values"]), len(timestamps))
                self.assertGreater(len(set(series["values"])), 1)

                for timestamp, value in zip(timestamps, series["values"], strict=True):
                    sample = MetricSample(
                        identity=identity,
                        channel_key=channel_key,
                        value=value,
                        timestamp=timestamp,
                        unit=series["unit"],
                        quality="good",
                        source_channel=series["source_channel"],
                        source_metadata=sensor["metadata"],
                    )
                    self.assertEqual(sample.timestamp.tzinfo, UTC)
                    self.assertTrue(math.isfinite(sample.value))
                    sample_count += 1

        self.assertEqual(sample_count, 98)

    def test_fixture_traffic_curves_are_distinct_numeric_rates(self) -> None:
        traffic = next(
            sensor
            for sensor in self.fixture["sensors"]
            if sensor["sensor_type"] == "traffic"
        )
        traffic_in = traffic["channels"]["traffic_in_bps"]["values"]
        traffic_out = traffic["channels"]["traffic_out_bps"]["values"]

        self.assertNotEqual(traffic_in, traffic_out)
        self.assertTrue(all(isinstance(value, int) for value in traffic_in))
        self.assertTrue(all(isinstance(value, int) for value in traffic_out))
        self.assertIn(85000000, traffic_in)

    def test_metrics_package_has_no_framework_or_infrastructure_imports(self) -> None:
        forbidden = {
            "app",
            "flask",
            "monitor",
            "prtg_client",
            "requests",
            "sqlite3",
        }
        imported_roots = set()

        for path in (ROOT / "metrics").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(
                        alias.name.split(".", 1)[0] for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_roots.add(node.module.split(".", 1)[0])

        self.assertTrue(forbidden.isdisjoint(imported_roots))


if __name__ == "__main__":
    unittest.main()
