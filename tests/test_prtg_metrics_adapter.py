from __future__ import annotations

import ast
import json
import math
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from metrics.prtg_adapter import (
    AdapterIssueCode,
    PRTGAdapterError,
    PRTGMetricAdapter,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "prtg_multichannel"
PRODUCTION_FILES = (
    "app.py",
    "monitor.py",
    "history_service.py",
    "sensor_control_service.py",
    "prtg_client.py",
    "database.py",
    "config.py",
    "datos_simulados.py",
)


def fixed_timezone(value: str):
    sign = 1 if value.startswith("+") else -1
    hours, minutes = (int(part) for part in value[1:].split(":"))
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


class PRTGMetricsAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(
            (FIXTURE_DIR / "sensors.json").read_text(encoding="utf-8")
        )
        cls.partial_fixture = json.loads(
            (FIXTURE_DIR / "partial_sfp.json").read_text(encoding="utf-8")
        )

    def setUp(self) -> None:
        self.adapter = PRTGMetricAdapter()

    def adapt_fixture_sensor(self, sensor_type: str):
        source = next(
            sensor
            for sensor in self.fixture["sensors"]
            if sensor["sensor_type"] == sensor_type
        )
        return self.adapter.adapt_sensor(
            source,
            server_id=self.fixture["server_id"],
            sensor_type=sensor_type,
            source_timezone=fixed_timezone(self.fixture["source_timezone"]),
        )

    @staticmethod
    def source(
        *channels,
        objid=100001,
        parentid=10001,
        group_name="PRTG LAB 01",
    ) -> dict:
        return {
            "objid": objid,
            "parentid": parentid,
            "device": "SITE-01-001",
            "sensor": "Synthetic metric sensor",
            "group_name": group_name,
            "status": "Up",
            "status_raw": 3,
            "channels": list(channels),
        }

    def test_multichannel_fixtures_cover_all_sensor_types(self) -> None:
        results = {
            sensor_type: self.adapt_fixture_sensor(sensor_type)
            for sensor_type in ("ping", "cpu", "memory", "traffic", "sfp")
        }

        self.assertEqual(sum(len(result.samples) for result in results.values()), 32)
        self.assertTrue(all(not result.issues for result in results.values()))
        self.assertTrue(
            all(not result.unknown_channels for result in results.values())
        )
        self.assertEqual(
            {result.sensor_type for result in results.values()},
            {"ping", "cpu", "memory", "traffic", "sfp"},
        )

    def test_ping_latency_packet_loss_and_availability(self) -> None:
        result = self.adapt_fixture_sensor("ping")
        by_key: dict[str, list] = {}
        for sample in result.samples:
            by_key.setdefault(sample.channel_key, []).append(sample)

        self.assertEqual(set(by_key), {
            "latency_ms",
            "packet_loss_pct",
            "availability_pct",
        })
        self.assertEqual([sample.value for sample in by_key["latency_ms"]], [14.5, 17.2])
        self.assertEqual(by_key["latency_ms"][0].unit, "ms")
        self.assertEqual(by_key["packet_loss_pct"][0].unit, "%")
        self.assertEqual(by_key["availability_pct"][0].value, 99.9)

    def test_current_prtg_historic_shape_uses_same_channel_boundary(self) -> None:
        source = (FIXTURE_DIR / "ping_history.csv").read_text(encoding="utf-8")

        result = self.adapter.adapt_historic_csv(
            source,
            server_id="srv_synthetic_01",
            sensor_id=100001,
            device_id=10001,
            sensor_type="ping",
            source_timezone=timezone(timedelta(hours=-6)),
            source_metadata={
                "group_name": "PRTG LAB 01",
                "device": "SITE-01-001",
                "sensor": "Ping",
                "status": "Up",
            },
        )

        self.assertEqual(
            [sample.channel_key for sample in result.samples],
            [
                "latency_ms",
                "packet_loss_pct",
                "latency_ms",
                "packet_loss_pct",
            ],
        )
        self.assertEqual(
            {channel.source_channel for channel in result.unknown_channels},
            {"Minimum", "Maximum", "Downtime", "Coverage"},
        )
        self.assertEqual(result.samples[0].timestamp.tzinfo, UTC)
        self.assertEqual(
            result.samples[0].source_metadata["group_name"],
            "PRTG LAB 01",
        )

    def test_invalid_historic_structure_is_rejected(self) -> None:
        invalid_sources = (
            "",
            "Channel;Value\nPing Time;10 ms\n",
            "Date Time;Ping Time\n",
        )

        for source in invalid_sources:
            with self.subTest(source=source):
                with self.assertRaises(PRTGAdapterError):
                    self.adapter.adapt_historic_csv(
                        source,
                        server_id="srv_synthetic_01",
                        sensor_id=100001,
                        sensor_type="ping",
                        source_timezone=UTC,
                    )

    def test_cpu_usage(self) -> None:
        result = self.adapt_fixture_sensor("cpu")

        self.assertEqual(
            [sample.channel_key for sample in result.samples],
            ["cpu_usage_pct"] * 3,
        )
        self.assertEqual([sample.value for sample in result.samples], [35.0, 48.0, 67.0])

    def test_memory_percent_and_bytes(self) -> None:
        result = self.adapt_fixture_sensor("memory")
        usage = [
            sample.value
            for sample in result.samples
            if sample.channel_key == "memory_usage_pct"
        ]
        used = [
            sample.value
            for sample in result.samples
            if sample.channel_key == "memory_used_bytes"
        ]

        self.assertEqual(usage, [58.0, 62.0])
        self.assertEqual(used, [6_144_000_000, 6_500_000_000])
        self.assertTrue(all(isinstance(value, int) for value in used))

    def test_traffic_rates_and_kilo_mega_giga_conversions(self) -> None:
        result = self.adapt_fixture_sensor("traffic")
        inbound = [
            sample.value
            for sample in result.samples
            if sample.channel_key == "traffic_in_bps"
        ]
        outbound = [
            sample.value
            for sample in result.samples
            if sample.channel_key == "traffic_out_bps"
        ]
        utilization = [
            sample.value
            for sample in result.samples
            if sample.channel_key == "utilization_pct"
        ]

        self.assertEqual(inbound, [850_000.0, 85_000_000.0, 120_000_000.0])
        self.assertEqual(outbound, [450_000.0, 42_000_000.0, 65_000_000.0])
        self.assertEqual(utilization, [12.0, 31.0, 45.0])
        self.assertTrue(all(sample.unit == "bps" for sample in result.samples if sample.channel_key.endswith("_bps")))

    def test_byte_kbyte_and_mbyte_conversions_are_explicit(self) -> None:
        source = self.source(
            {"channel": "Used Memory", "value": 512, "unit": "Byte", "timestamp": "2026-09-23T12:00:00Z"},
            {"channel": "Used Memory", "value": 2, "unit": "KByte", "timestamp": "2026-09-23T12:05:00Z"},
            {"channel": "Used Memory", "value": 3, "unit": "MByte", "timestamp": "2026-09-23T12:10:00Z"},
        )

        result = self.adapter.adapt_sensor(
            source,
            server_id="srv_synthetic_01",
            sensor_type="memory",
        )

        self.assertEqual([sample.value for sample in result.samples], [512, 2_000, 3_000_000])
        self.assertEqual({sample.unit for sample in result.samples}, {"bytes"})

    def test_sfp_channels_are_independent_and_use_canonical_units(self) -> None:
        result = self.adapt_fixture_sensor("sfp")
        units = {sample.channel_key: sample.unit for sample in result.samples}

        self.assertEqual(
            set(units),
            {
                "rx_power_dbm",
                "tx_power_dbm",
                "temperature_c",
                "voltage_v",
                "bias_current_ma",
            },
        )
        self.assertEqual(units["rx_power_dbm"], "dBm")
        self.assertEqual(units["temperature_c"], "°C")
        self.assertEqual(units["voltage_v"], "V")
        self.assertEqual(units["bias_current_ma"], "mA")

    def test_identity_is_stable_and_group_name_is_only_metadata(self) -> None:
        channel = {
            "channel": "Ping Time",
            "value": "10 ms",
            "timestamp": "2026-09-23T12:00:00Z",
        }
        before = self.adapter.adapt_sensor(
            self.source(channel, group_name="PRTG LAB 01"),
            server_id="srv_synthetic_01",
            sensor_type="ping",
        )
        after = self.adapter.adapt_sensor(
            self.source(channel, group_name="RENAMED SYNTHETIC LAB"),
            server_id="srv_synthetic_01",
            sensor_type="ping",
        )

        self.assertEqual(before.identity, after.identity)
        self.assertEqual(hash(before.identity), hash(after.identity))
        self.assertNotEqual(
            before.samples[0].source_metadata["group_name"],
            after.samples[0].source_metadata["group_name"],
        )

    def test_naive_source_timestamp_requires_explicit_timezone(self) -> None:
        source = self.source(
            {
                "channel": "Ping Time",
                "value": "10 ms",
                "timestamp": "2026-09-23 12:00:00",
            }
        )

        result = self.adapter.adapt_sensor(
            source,
            server_id="srv_synthetic_01",
            sensor_type="ping",
        )

        self.assertEqual(result.samples, ())
        self.assertEqual(result.issues[0].code, AdapterIssueCode.INVALID_TIMESTAMP)

    def test_timestamps_are_normalized_to_utc(self) -> None:
        result = self.adapt_fixture_sensor("ping")

        self.assertEqual(result.samples[0].timestamp.tzinfo, UTC)
        self.assertEqual(
            result.samples[0].timestamp,
            datetime(2026, 9, 23, 18, 0, tzinfo=UTC),
        )

        aware_source = self.source(
            {
                "channel": "Ping Time",
                "value": "10 ms",
                "timestamp": "2026-09-23T12:00:00+02:00",
            }
        )
        aware = self.adapter.adapt_sensor(
            aware_source,
            server_id="srv_synthetic_01",
            sensor_type="ping",
        )
        self.assertEqual(
            aware.samples[0].timestamp,
            datetime(2026, 9, 23, 10, 0, tzinfo=UTC),
        )

    def test_built_in_and_provider_aliases_do_not_require_core_changes(self) -> None:
        adapter = PRTGMetricAdapter(
            additional_aliases={"Round Trip Delay": "latency_ms"}
        )
        source = self.source(
            {"channel": "Ping", "value": "9 ms", "timestamp": "2026-09-23T12:00:00Z"},
            {"channel": "Round Trip Delay", "value": "11 ms", "timestamp": "2026-09-23T12:05:00Z"},
        )

        result = adapter.adapt_sensor(
            source,
            server_id="srv_synthetic_01",
            sensor_type="ping",
        )

        self.assertEqual([sample.channel_key for sample in result.samples], ["latency_ms", "latency_ms"])
        self.assertEqual([sample.value for sample in result.samples], [9.0, 11.0])

    def test_partial_sensor_and_unknown_channel_preserve_valid_samples(self) -> None:
        fixture = self.partial_fixture
        result = self.adapter.adapt_sensor(
            fixture["sensor"],
            server_id=fixture["server_id"],
            sensor_type=fixture["sensor"]["sensor_type"],
            source_timezone=fixed_timezone(fixture["source_timezone"]),
        )

        self.assertEqual(
            {sample.channel_key for sample in result.samples},
            {"rx_power_dbm", "tx_power_dbm"},
        )
        self.assertEqual(result.issues, ())
        self.assertEqual(len(result.unknown_channels), 1)
        self.assertEqual(
            result.unknown_channels[0].source_channel,
            "Vendor Diagnostic",
        )

    def test_unknown_unit_is_reported_without_fabricating_zero(self) -> None:
        result = self.adapter.adapt_sensor(
            self.source(
                {"channel": "Traffic In", "value": "85 furlong/day", "timestamp": "2026-09-23T12:00:00Z"}
            ),
            server_id="srv_synthetic_01",
            sensor_type="traffic",
        )

        self.assertEqual(result.samples, ())
        self.assertEqual(result.issues[0].code, AdapterIssueCode.UNKNOWN_UNIT)

    def test_recognized_but_incoherent_unit_is_reported(self) -> None:
        result = self.adapter.adapt_sensor(
            self.source(
                {"channel": "Traffic In", "value": "-4.2 dBm", "timestamp": "2026-09-23T12:00:00Z"}
            ),
            server_id="srv_synthetic_01",
            sensor_type="traffic",
        )

        self.assertEqual(result.samples, ())
        self.assertEqual(result.issues[0].code, AdapterIssueCode.UNIT_MISMATCH)

    def test_declared_and_embedded_units_must_agree(self) -> None:
        result = self.adapter.adapt_sensor(
            self.source(
                {"channel": "Traffic In", "value": "85 Mbit/s", "unit": "Kbit/s", "timestamp": "2026-09-23T12:00:00Z"}
            ),
            server_id="srv_synthetic_01",
            sensor_type="traffic",
        )

        self.assertEqual(result.samples, ())
        self.assertEqual(result.issues[0].code, AdapterIssueCode.UNIT_MISMATCH)

    def test_invalid_nan_and_infinite_values_are_reported_not_zeroed(self) -> None:
        invalid_values = ("not available", "NaN %", math.nan, math.inf, -math.inf)

        for value in invalid_values:
            with self.subTest(value=value):
                result = self.adapter.adapt_sensor(
                    self.source(
                        {"channel": "CPU Load", "value": value, "unit": "%", "timestamp": "2026-09-23T12:00:00Z"}
                    ),
                    server_id="srv_synthetic_01",
                    sensor_type="cpu",
                )
                self.assertEqual(result.samples, ())
                self.assertEqual(result.issues[0].code, AdapterIssueCode.INVALID_VALUE)

    def test_invalid_timestamp_does_not_destroy_valid_sibling_channel(self) -> None:
        result = self.adapter.adapt_sensor(
            self.source(
                {"channel": "Ping Time", "value": "10 ms", "timestamp": "not-a-date"},
                {"channel": "Packet Loss", "value": "1 %", "timestamp": "2026-09-23T12:00:00Z"},
            ),
            server_id="srv_synthetic_01",
            sensor_type="ping",
        )

        self.assertEqual([sample.channel_key for sample in result.samples], ["packet_loss_pct"])
        self.assertEqual(result.issues[0].code, AdapterIssueCode.INVALID_TIMESTAMP)

    def test_missing_channel_metadata_is_distinct_from_unknown_channel(self) -> None:
        result = self.adapter.adapt_sensor(
            self.source(
                {"value": "10 ms", "timestamp": "2026-09-23T12:00:00Z"},
                {"channel": "Ping Time", "value": 10, "timestamp": "2026-09-23T12:00:00Z"},
            ),
            server_id="srv_synthetic_01",
            sensor_type="ping",
        )

        self.assertEqual(result.samples, ())
        self.assertEqual(
            [issue.code for issue in result.issues],
            [AdapterIssueCode.MISSING_METADATA, AdapterIssueCode.MISSING_METADATA],
        )
        self.assertEqual(result.unknown_channels, ())

    def test_sensor_type_rejects_mapped_channel_from_another_definition(self) -> None:
        result = self.adapter.adapt_sensor(
            self.source(
                {"channel": "CPU Load", "value": "40 %", "timestamp": "2026-09-23T12:00:00Z"}
            ),
            server_id="srv_synthetic_01",
            sensor_type="ping",
        )

        self.assertEqual(result.samples, ())
        self.assertEqual(result.issues[0].code, AdapterIssueCode.CHANNEL_NOT_SUPPORTED)

    def test_rate_channels_reject_counters_without_calculating_deltas(self) -> None:
        result = self.adapter.adapt_sensor(
            self.source(
                {"channel": "Traffic In", "value": "9000 bit/s", "kind": "counter", "timestamp": "2026-09-23T12:00:00Z"}
            ),
            server_id="srv_synthetic_01",
            sensor_type="traffic",
        )

        self.assertEqual(result.samples, ())
        self.assertEqual(result.issues[0].code, AdapterIssueCode.COUNTER_UNSUPPORTED)

    def test_legacy_single_lastvalue_record_is_supported(self) -> None:
        source = {
            "objid": 100001,
            "parentid": 10001,
            "device": "SITE-01-001",
            "sensor": "Ping",
            "status": "Up",
            "status_raw": 3,
            "lastvalue": "12 ms",
            "timestamp": "2026-09-23T12:00:00Z",
        }

        result = self.adapter.adapt_sensor(
            source,
            server_id="srv_synthetic_01",
            sensor_type="ping",
        )

        self.assertEqual(len(result.samples), 1)
        self.assertEqual(result.samples[0].channel_key, "latency_ms")
        self.assertEqual(result.samples[0].value, 12.0)

    def test_samples_are_sorted_chronologically(self) -> None:
        result = self.adapter.adapt_sensor(
            self.source(
                {"channel": "CPU Load", "value": "67 %", "timestamp": "2026-09-23T12:10:00Z"},
                {"channel": "CPU Load", "value": "30 %", "timestamp": "2026-09-23T12:00:00Z"},
                {"channel": "CPU Load", "value": "48 %", "timestamp": "2026-09-23T12:05:00Z"},
            ),
            server_id="srv_synthetic_01",
            sensor_type="cpu",
        )

        self.assertEqual([sample.value for sample in result.samples], [30.0, 48.0, 67.0])
        self.assertEqual(
            [sample.timestamp for sample in result.samples],
            sorted(sample.timestamp for sample in result.samples),
        )

    def test_source_metadata_is_traceable_but_does_not_copy_payload_or_secrets(self) -> None:
        source = self.source(
            {"channel": "Ping Time", "value": "10 ms", "timestamp": "2026-09-23T12:00:00Z"}
        )
        source["password"] = "do-not-copy-synthetic-secret"
        source["large_payload"] = {"not": "copied"}

        result = self.adapter.adapt_sensor(
            source,
            server_id="srv_synthetic_01",
            sensor_type="ping",
        )
        sample = result.samples[0]

        self.assertEqual(sample.source_channel, "Ping Time")
        self.assertEqual(sample.source_metadata["source_unit"], "ms")
        self.assertEqual(sample.source_metadata["source_sensor_type"], "ping")
        self.assertEqual(sample.source_metadata["device_name"], "SITE-01-001")
        serialized = repr(dict(sample.source_metadata))
        self.assertNotIn("do-not-copy-synthetic-secret", serialized)
        self.assertNotIn("large_payload", serialized)

    def test_structurally_invalid_sensors_are_rejected(self) -> None:
        cases = (
            ({"channels": []}, "ping"),
            ({"objid": 1, "channels": "not-a-list"}, "ping"),
            ({"objid": 1, "unrelated": True}, "ping"),
            ({"objid": 1, "channels": []}, "unknown"),
        )

        for source, sensor_type in cases:
            with self.subTest(source=source, sensor_type=sensor_type):
                with self.assertRaises(PRTGAdapterError):
                    self.adapter.adapt_sensor(
                        source,
                        server_id="srv_synthetic_01",
                        sensor_type=sensor_type,
                    )

        with self.assertRaises(PRTGAdapterError):
            self.adapter.adapt_sensor(
                {"objid": 1, "channels": []},
                server_id="",
                sensor_type="ping",
            )

    def test_only_history_service_imports_adapter_in_production(self) -> None:
        consumers = []
        for relative_name in PRODUCTION_FILES:
            tree = ast.parse((ROOT / relative_name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = {node.module}
                else:
                    continue
                if any(name.startswith("metrics.prtg_adapter") for name in names):
                    consumers.append(relative_name)

        self.assertEqual(consumers, ["history_service.py"])


if __name__ == "__main__":
    unittest.main()
