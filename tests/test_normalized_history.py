from __future__ import annotations

import ast
import csv
import io
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import datos_simulados
from history_service import (
    HistoricalMetricsSensorNotFoundError,
    HistoricalMetricsSourceError,
    HistorySensorNotFoundError,
    HistoryService,
    HistoryUpstreamError,
    HistoryValidationError,
    NormalizedHistoryResult,
)
from metrics.prtg_adapter import AdapterIssueCode


class LegacyCSVClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def get_historic_csv(
        self,
        server,
        sensor_id,
        start,
        end,
        *,
        average_seconds,
    ) -> str:
        self.calls += 1
        return self.response


class SimulatorHistorySource:
    def __init__(
        self,
        simulator: datos_simulados.Simulator,
        *,
        slug: str = "prtg-01",
        scenario: str | None = None,
        fault: str | None = None,
    ) -> None:
        self.simulator = simulator
        self.slug = slug
        self.scenario = scenario
        self.fault = fault
        self.calls: list[dict[str, Any]] = []

    def fetch_history(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
        *,
        points: int,
        start: datetime | None,
        end: datetime | None,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "server": server,
                "sensor_id": sensor_id,
                "points": points,
                "start": start,
                "end": end,
            }
        )
        try:
            return self.simulator.multichannel_history(
                self.slug,
                int(sensor_id),
                points=points,
                scenario=self.scenario,
                fault=self.fault,
            )
        except KeyError:
            raise HistoricalMetricsSensorNotFoundError(
                f"synthetic sensor {sensor_id} was not found"
            ) from None


class StaticHistorySource:
    def __init__(self, payload: Mapping[str, Any] | None = None) -> None:
        self.payload = payload
        self.calls = 0
        self.error: Exception | None = None

    def fetch_history(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
        *,
        points: int,
        start: datetime | None,
        end: datetime | None,
    ) -> Mapping[str, Any]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.payload is not None
        return self.payload


class NormalizedHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start_time = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        self.simulator = datos_simulados.Simulator(
            server_count=1,
            sensor_count=100,
            seed=20260923,
            tick_seconds=300,
            start_time=self.start_time,
        )
        self.simulator.tick(8)
        self.server = {
            "id": "srv-prtg-01",
            "type": "prtg",
            "enabled": True,
            "name": "PRTG LAB 01",
            "url": "http://127.0.0.1:19090/prtg-01",
            "username": "synthetic-user",
            "password": "synthetic-password",
        }
        self.servers = {"PRTG LAB 01": self.server}
        self.legacy_client = LegacyCSVClient(
            "Date Time;Ping Time;Packet Loss\n"
            "2026-09-23 11:55:00;10 ms;0 %\n"
        )

    def sensor_id(self, family: str, index: int = 0) -> int:
        return self.simulator.multichannel.sensor_ids("prtg-01", family)[index]

    def service(self, source=None) -> HistoryService:
        return HistoryService(
            server_provider=lambda: self.servers,
            client_factory=lambda: self.legacy_client,
            cache_ttl_seconds=60,
            cache_max_entries=128,
            normalized_source=source,
        )

    def normalized(
        self,
        family: str,
        *,
        source=None,
        points: int = 7,
        index: int = 0,
    ) -> NormalizedHistoryResult:
        selected_source = source or SimulatorHistorySource(self.simulator)
        return self.service(selected_source).get_normalized_history(
            "PRTG LAB 01",
            self.sensor_id(family, index),
            sensor_type=family,
            points=points,
        )

    @staticmethod
    def ping_csv(source_history: Mapping[str, Any]) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(
            ["Date Time", "Ping Time", "Packet Loss", "Availability"]
        )
        for sample in source_history["samples"]:
            channels = {
                channel["channel"]: channel["value"]
                for channel in sample["channels"]
            }
            writer.writerow(
                [
                    sample["timestamp"],
                    channels["Ping Time"],
                    channels["Packet Loss"],
                    channels["Availability"],
                ]
            )
        return buffer.getvalue()

    def test_ping_semantic_parity_with_legacy_history(self) -> None:
        sensor_id = self.sensor_id("ping")
        source = SimulatorHistorySource(self.simulator)
        source_history = source.fetch_history(
            self.server,
            sensor_id,
            points=7,
            start=None,
            end=None,
        )
        self.legacy_client.response = self.ping_csv(source_history)
        service = self.service(source)

        legacy = service.get_history(
            "PRTG LAB 01",
            sensor_id,
            minutes=120,
            average_seconds=300,
        )
        normalized = service.get_normalized_history(
            "PRTG LAB 01",
            sensor_id,
            sensor_type="ping",
            points=7,
        )
        grouped = normalized.by_channel()
        legacy_by_key = {
            dataset["key"]: dataset["data"]
            for dataset in legacy["datasets"]
        }

        self.assertEqual(len(grouped["latency_ms"]), legacy["count"])
        self.assertEqual(len(grouped["packet_loss_pct"]), legacy["count"])
        self.assertEqual(
            [sample.value for sample in grouped["latency_ms"]],
            [point["value"] for point in legacy_by_key["ping"]],
        )
        self.assertEqual(
            [sample.value for sample in grouped["packet_loss_pct"]],
            [point["value"] for point in legacy_by_key["packet_loss"]],
        )
        self.assertEqual(
            [
                sample.timestamp.replace(tzinfo=None).isoformat()
                for sample in grouped["latency_ms"]
            ],
            [point["t"] for point in legacy_by_key["ping"]],
        )
        self.assertEqual(len(grouped["availability_pct"]), legacy["count"])
        self.assertNotIn("availability", legacy_by_key)

    def test_cpu_history_is_numeric_ordered_and_utc(self) -> None:
        result = self.normalized("cpu")
        series = result.by_channel()["cpu_usage_pct"]

        self.assertEqual(len(series), 7)
        self.assertEqual([sample.timestamp for sample in series], sorted(
            sample.timestamp for sample in series
        ))
        self.assertTrue(all(sample.timestamp.tzinfo is UTC for sample in series))
        self.assertTrue(all(sample.unit == "%" for sample in series))
        self.assertGreater(len({sample.value for sample in series}), 1)

    def test_memory_history_keeps_percent_and_integer_bytes(self) -> None:
        grouped = self.normalized("memory").by_channel()

        self.assertEqual(
            set(grouped),
            {"memory_usage_pct", "memory_used_bytes"},
        )
        self.assertTrue(
            all(sample.unit == "bytes" for sample in grouped["memory_used_bytes"])
        )
        self.assertTrue(
            all(isinstance(sample.value, int) for sample in grouped["memory_used_bytes"])
        )

    def test_traffic_history_contains_in_out_and_utilization(self) -> None:
        result = self.normalized("traffic", points=9)
        grouped = result.by_channel()

        self.assertEqual(
            set(grouped),
            {"traffic_in_bps", "traffic_out_bps", "utilization_pct"},
        )
        self.assertEqual(len(grouped["traffic_in_bps"]), 9)
        self.assertEqual(len(grouped["traffic_out_bps"]), 9)
        self.assertTrue(
            all(sample.unit == "bps" for sample in grouped["traffic_in_bps"])
        )
        self.assertNotEqual(
            [sample.value for sample in grouped["traffic_in_bps"]],
            [sample.value for sample in grouped["traffic_out_bps"]],
        )

    def test_sfp_history_exposes_each_available_channel_independently(self) -> None:
        grouped = self.normalized("sfp", index=0).by_channel()

        self.assertEqual(
            set(grouped),
            {
                "rx_power_dbm",
                "tx_power_dbm",
                "temperature_c",
                "voltage_v",
                "bias_current_ma",
            },
        )
        self.assertTrue(all(len(series) == 7 for series in grouped.values()))

    def test_partial_sfp_history_does_not_require_a_complete_channel_set(self) -> None:
        source = SimulatorHistorySource(
            self.simulator,
            scenario="partial_channels",
        )
        result = self.normalized("sfp", source=source)

        self.assertGreaterEqual(len(result.channels), 1)
        self.assertLessEqual(len(result.channels), 3)
        self.assertEqual(result.issues, ())

    def test_unknown_channels_are_preserved_with_valid_samples(self) -> None:
        source = SimulatorHistorySource(
            self.simulator,
            scenario="unknown_channel",
        )
        result = self.normalized("traffic", source=source, points=5)

        self.assertEqual(len(result.unknown_channels), 5)
        self.assertTrue(result.samples)
        self.assertEqual(
            {item.source_channel for item in result.unknown_channels},
            {"Laser Health Index"},
        )

    def test_invalid_values_and_timestamps_preserve_valid_siblings(self) -> None:
        invalid_value = self.normalized(
            "traffic",
            source=SimulatorHistorySource(self.simulator, fault="n_a"),
            points=4,
        )
        invalid_timestamp = self.normalized(
            "traffic",
            source=SimulatorHistorySource(
                self.simulator,
                fault="invalid_timestamp",
            ),
            points=4,
        )

        self.assertEqual(
            {issue.code for issue in invalid_value.issues},
            {AdapterIssueCode.INVALID_VALUE},
        )
        self.assertEqual(
            {issue.code for issue in invalid_timestamp.issues},
            {AdapterIssueCode.INVALID_TIMESTAMP},
        )
        self.assertNotIn("traffic_in_bps", invalid_value.by_channel())
        self.assertIn("traffic_out_bps", invalid_value.by_channel())
        self.assertIn("traffic_out_bps", invalid_timestamp.by_channel())

    def test_invalid_units_are_exposed_without_fabricating_samples(self) -> None:
        result = self.normalized(
            "traffic",
            source=SimulatorHistorySource(
                self.simulator,
                fault="unknown_unit",
            ),
            points=4,
        )

        self.assertEqual(
            {issue.code for issue in result.issues},
            {AdapterIssueCode.UNKNOWN_UNIT},
        )
        self.assertNotIn("traffic_in_bps", result.by_channel())
        self.assertIn("traffic_out_bps", result.by_channel())

    def test_missing_sensor_and_source_failure_are_typed(self) -> None:
        service = self.service(SimulatorHistorySource(self.simulator))
        with self.assertRaises(HistorySensorNotFoundError):
            service.get_normalized_history(
                "PRTG LAB 01",
                999999,
                sensor_type="ping",
            )

        failing = StaticHistorySource()
        failing.error = HistoricalMetricsSourceError("synthetic failure")
        with self.assertRaisesRegex(
            HistoryUpstreamError,
            "fuente historica normalizada",
        ):
            self.service(failing).get_normalized_history(
                "PRTG LAB 01",
                self.sensor_id("ping"),
                sensor_type="ping",
            )

    def test_stable_identity_boundaries_and_provider_arguments(self) -> None:
        source = SimulatorHistorySource(self.simulator)
        start = datetime(2026, 9, 23, 11, 0, tzinfo=timezone(timedelta(hours=-6)))
        end = start + timedelta(hours=1)
        sensor_id = self.sensor_id("cpu")

        result = self.service(source).get_normalized_history(
            "PRTG LAB 01",
            sensor_id,
            sensor_type="cpu",
            points=3,
            start=start,
            end=end,
        )

        self.assertEqual(result.identity.server_id, "srv-prtg-01")
        self.assertEqual(result.identity.sensor_id, str(sensor_id))
        self.assertEqual(source.calls[0]["start"].tzinfo, UTC)
        self.assertEqual(source.calls[0]["end"].tzinfo, UTC)
        self.assertTrue(all(sample.identity == result.identity for sample in result.samples))

    def test_result_orders_and_deduplicates_last_source_observation(self) -> None:
        sensor_id = self.sensor_id("ping")
        timestamp = "2026-09-23T12:00:00Z"
        payload = {
            "samples": [
                {
                    "objid": sensor_id,
                    "timestamp": timestamp,
                    "channels": [{"channel": "Ping Time", "value": "10 ms"}],
                },
                {
                    "objid": sensor_id,
                    "timestamp": timestamp,
                    "channels": [{"channel": "Ping Time", "value": "20 ms"}],
                },
                {
                    "objid": sensor_id,
                    "timestamp": "2026-09-23T11:55:00Z",
                    "channels": [{"channel": "Ping Time", "value": "15 ms"}],
                },
            ]
        }

        result = self.service(StaticHistorySource(payload)).get_normalized_history(
            "PRTG LAB 01",
            sensor_id,
            sensor_type="ping",
            points=3,
        )
        series = result.by_channel()["latency_ms"]

        self.assertEqual([sample.value for sample in series], [15.0, 20.0])
        self.assertEqual(result.start, datetime(2026, 9, 23, 11, 55, tzinfo=UTC))
        self.assertEqual(result.end, datetime(2026, 9, 23, 12, 0, tzinfo=UTC))

    def test_normalized_path_has_no_cache_and_does_not_touch_legacy_cache(self) -> None:
        source = SimulatorHistorySource(self.simulator)
        service = self.service(source)
        sensor_id = self.sensor_id("cpu")

        for _ in range(2):
            service.get_normalized_history(
                "PRTG LAB 01",
                sensor_id,
                sensor_type="cpu",
                points=3,
            )

        self.assertEqual(len(source.calls), 2)
        self.assertEqual(service.cache_size(), 0)

        first = service.get_history(
            "PRTG LAB 01",
            sensor_id,
            minutes=120,
            average_seconds=300,
        )
        second = service.get_history(
            "PRTG LAB 01",
            sensor_id,
            minutes=120,
            average_seconds=300,
        )
        self.assertIs(first, second)
        self.assertEqual(self.legacy_client.calls, 1)
        self.assertEqual(service.cache_size(), 1)

    def test_invalid_normalized_request_is_rejected_before_source(self) -> None:
        source = SimulatorHistorySource(self.simulator)
        service = self.service(source)
        sensor_id = self.sensor_id("ping")

        invalid_calls = (
            {"sensor_type": "unknown"},
            {"sensor_type": "ping", "points": 0},
            {
                "sensor_type": "ping",
                "start": datetime(2026, 9, 23, 12, 0),
            },
            {
                "sensor_type": "ping",
                "start": self.start_time,
                "end": self.start_time - timedelta(minutes=5),
            },
        )
        for kwargs in invalid_calls:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HistoryValidationError):
                    service.get_normalized_history(
                        "PRTG LAB 01",
                        sensor_id,
                        **kwargs,
                    )
        self.assertEqual(source.calls, [])

    def test_history_service_has_no_flask_chart_or_sqlite_dependency(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "history_service.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        self.assertNotIn("flask", imported_roots)
        self.assertNotIn("sqlite3", imported_roots)
        self.assertNotIn("chart.js", source.casefold())


if __name__ == "__main__":
    unittest.main()
