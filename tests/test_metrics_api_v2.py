from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

import datos_simulados
from history_service import (
    HistoricalMetricsSensorNotFoundError,
    HistoricalMetricsSourceError,
)


class SimulatorMetricsHistorySource:
    """Small injectable provider used only by the HTTP contract tests."""

    def __init__(self, simulator: datos_simulados.Simulator) -> None:
        self.simulator = simulator
        self.scenario: str | None = None
        self.fault: str | None = None
        self.error: Exception | None = None
        self.calls: list[dict[str, Any]] = []

    def fetch_history(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
        *,
        points: int | None,
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
        if self.error is not None:
            raise self.error

        requested_points = points
        if requested_points is None:
            if start is None or end is None:
                raise HistoricalMetricsSourceError("range boundaries are required")
            seconds = (end - start).total_seconds()
            requested_points = int(seconds // self.simulator.multichannel.tick_seconds) + 1
            requested_points = max(1, min(1000, requested_points))

        try:
            return self.simulator.multichannel_history(
                "prtg-01",
                int(sensor_id),
                points=requested_points,
                scenario=self.scenario,
                fault=self.fault,
            )
        except KeyError:
            raise HistoricalMetricsSensorNotFoundError(
                f"synthetic sensor {sensor_id} was not found"
            ) from None

    def fetch_sensor_metadata(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
    ) -> Mapping[str, Any]:
        if self.error is not None:
            raise self.error
        try:
            payload = self.simulator.multichannel_sensor(
                "prtg-01",
                int(sensor_id),
            )
        except KeyError:
            raise HistoricalMetricsSensorNotFoundError(
                f"synthetic sensor {sensor_id} was not found"
            ) from None
        return {
            "server_id": payload["server_id"],
            "sensor_id": payload["objid"],
            "sensor_type": payload["sensor_type"],
        }


class MetricsAPIV2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = tempfile.TemporaryDirectory()
        root = Path(cls.runtime.name)
        cls.db_path = root / "app.db"
        cls.data_path = root / "snapshot.json"
        cls.servers_path = root / "servers.json"
        cls.server = {
            "id": "srv-prtg-01",
            "type": "prtg",
            "enabled": True,
            "name": "PRTG LAB 01",
            "url": "http://127.0.0.1:19090/prtg-01",
            "username": "synthetic-user",
            "password": "synthetic-test-password",
        }
        cls.servers_path.write_text(
            json.dumps([cls.server]),
            encoding="utf-8",
        )
        cls.data_path.write_text(
            json.dumps(
                {
                    "ultima_actualizacion": "2026-09-23 12:00:00",
                    "sensores": [],
                    "logs": {"last_up": [], "last_down": []},
                    "metrics": {"window_minutes": 120, "series": []},
                    "collector": {
                        "type": "prtg",
                        "servers_configured": 1,
                        "servers_with_error": 0,
                    },
                }
            ),
            encoding="utf-8",
        )

        cls.environment = mock.patch.dict(
            os.environ,
            {
                "APP_SECRET_KEY": "synthetic-v2-session-secret",
                "DB_PATH": str(cls.db_path),
                "OUTPUT_PATH": str(cls.data_path),
                "PRTG_SERVERS_JSON": json.dumps([cls.server]),
                "PRTG_SERVERS_FILE": str(cls.servers_path),
                "PRTG_VERIFY_SSL": "0",
                "REQUEST_TIMEOUT": "10",
                "HISTORY_CACHE_TTL": "60",
                "SEED_DEFAULT_USERS": "0",
                "DEFAULT_USERS_JSON": "[]",
                "SESSION_COOKIE_SECURE": "0",
                "FLASK_DEBUG": "0",
            },
            clear=False,
        )
        cls.environment.start()

        cls.simulator = datos_simulados.Simulator(
            server_count=1,
            sensor_count=100,
            seed=20260923,
            tick_seconds=300,
            start_time=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
        )
        cls.simulator.tick(8)
        cls.source = SimulatorMetricsHistorySource(cls.simulator)

        sys.modules.pop("app", None)
        cls.app_module = importlib.import_module("app")
        cls.flask_app = cls.app_module.create_app(
            normalized_history_source=cls.source
        )
        cls.flask_app.config.update(TESTING=True)

        created = cls.app_module.add_user(
            username="metrics-admin",
            password="synthetic-password",
            rol="admon",
            email="metrics-admin@example.test",
        )
        if not created:
            raise RuntimeError("No se pudo crear el usuario temporal de pruebas")

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("app", None)
        cls.environment.stop()
        cls.runtime.cleanup()

    def setUp(self) -> None:
        self.client = self.flask_app.test_client()
        self.source.scenario = None
        self.source.fault = None
        self.source.error = None
        self.source.calls.clear()

    def login(self) -> None:
        response = self.client.post(
            "/",
            data={
                "username": "metrics-admin",
                "password": "synthetic-password",
            },
        )
        self.assertEqual(response.status_code, 302)

    def sensor_id(self, family: str, index: int = 0) -> int:
        return self.simulator.multichannel.sensor_ids("prtg-01", family)[index]

    def query(
        self,
        family: str,
        *,
        sensor_index: int = 0,
        points: int = 5,
        extra: Mapping[str, Any] | None = None,
    ):
        query: dict[str, Any] = {
            "server": "PRTG LAB 01",
            "sensor_id": self.sensor_id(family, sensor_index),
            "sensor_type": family,
            "points": points,
        }
        query.update(extra or {})
        return self.client.get(
            "/api/v2/metrics/history",
            query_string=query,
        )

    def test_authentication_is_required(self) -> None:
        response = self.query("ping")

        self.assertEqual(response.status_code, 401)

        metadata = self.client.get(
            "/api/v2/metrics/sensor-metadata",
            query_string={
                "server": "PRTG LAB 01",
                "sensor_id": self.sensor_id("ping"),
            },
        )
        self.assertEqual(metadata.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "Sesion requerida"})
        self.assertEqual(self.source.calls, [])

    def test_sensor_metadata_resolves_explicit_type_and_stable_identity(self) -> None:
        self.login()
        for family in ("ping", "cpu", "memory", "traffic", "sfp"):
            with self.subTest(family=family):
                response = self.client.get(
                    "/api/v2/metrics/sensor-metadata",
                    query_string={
                        "server": "PRTG LAB 01",
                        "sensor_id": self.sensor_id(family),
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.get_json(),
                    {
                        "schema_version": "2.0",
                        "sensor": {
                            "server_id": "srv-prtg-01",
                            "sensor_id": str(self.sensor_id(family)),
                            "sensor_type": family,
                        },
                    },
                )

    def test_sensor_metadata_errors_are_typed_and_safe(self) -> None:
        self.login()
        missing_params = self.client.get(
            "/api/v2/metrics/sensor-metadata",
            query_string={"server": "PRTG LAB 01"},
        )
        missing_sensor = self.client.get(
            "/api/v2/metrics/sensor-metadata",
            query_string={"server": "PRTG LAB 01", "sensor_id": 999999},
        )
        self.source.error = HistoricalMetricsSourceError(
            "password=must-not-leak"
        )
        upstream = self.client.get(
            "/api/v2/metrics/sensor-metadata",
            query_string={
                "server": "PRTG LAB 01",
                "sensor_id": self.sensor_id("ping"),
            },
        )

        self.assertEqual(missing_params.status_code, 400)
        self.assertEqual(missing_sensor.status_code, 404)
        self.assertEqual(missing_sensor.get_json()["code"], "sensor_not_found")
        self.assertEqual(upstream.status_code, 502)
        self.assertEqual(upstream.get_json()["code"], "upstream_error")
        self.assertNotIn("must-not-leak", upstream.get_data(as_text=True))

    def test_required_identity_parameters_are_validated(self) -> None:
        self.login()
        base = {
            "server": "PRTG LAB 01",
            "sensor_id": self.sensor_id("ping"),
            "sensor_type": "ping",
            "points": 5,
        }

        for missing in ("server", "sensor_id", "sensor_type"):
            query = dict(base)
            query.pop(missing)
            with self.subTest(missing=missing):
                response = self.client.get(
                    "/api/v2/metrics/history",
                    query_string=query,
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["code"], "invalid_request")

        self.assertEqual(self.source.calls, [])

    def test_points_mode_validation_and_maximum(self) -> None:
        self.login()
        for value in ("abc", "0", "-1", "1001"):
            with self.subTest(points=value):
                response = self.query("ping", extra={"points": value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["code"], "invalid_request")
        self.assertEqual(self.source.calls, [])

    def test_query_mode_is_explicit_and_mutually_exclusive(self) -> None:
        self.login()
        identity = {
            "server": "PRTG LAB 01",
            "sensor_id": self.sensor_id("ping"),
            "sensor_type": "ping",
        }
        cases = (
            identity,
            {**identity, "start": "2026-09-23T12:00:00Z"},
            {**identity, "end": "2026-09-23T12:30:00Z"},
            {
                **identity,
                "points": 5,
                "start": "2026-09-23T12:00:00Z",
                "end": "2026-09-23T12:30:00Z",
            },
        )

        for query in cases:
            with self.subTest(query=query):
                response = self.client.get(
                    "/api/v2/metrics/history",
                    query_string=query,
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["code"], "invalid_request")
        self.assertEqual(self.source.calls, [])

    def test_range_timestamps_require_timezone_and_strict_order(self) -> None:
        self.login()
        identity = {
            "server": "PRTG LAB 01",
            "sensor_id": self.sensor_id("ping"),
            "sensor_type": "ping",
        }
        cases = (
            ("2026-09-23T12:00:00", "2026-09-23T12:30:00Z"),
            ("not-a-date", "2026-09-23T12:30:00Z"),
            ("2026-09-23T12:30:00Z", "2026-09-23T12:30:00Z"),
            ("2026-09-23T13:00:00Z", "2026-09-23T12:30:00Z"),
        )

        for start, end in cases:
            with self.subTest(start=start, end=end):
                response = self.client.get(
                    "/api/v2/metrics/history",
                    query_string={**identity, "start": start, "end": end},
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["code"], "invalid_request")
        self.assertEqual(self.source.calls, [])

    def test_points_success_for_all_supported_sensor_families(self) -> None:
        self.login()
        expected_channels = {
            "ping": {"latency_ms", "packet_loss_pct", "availability_pct"},
            "cpu": {"cpu_usage_pct"},
            "memory": {"memory_usage_pct", "memory_used_bytes"},
            "traffic": {
                "traffic_in_bps",
                "traffic_out_bps",
                "utilization_pct",
            },
            "sfp": {
                "rx_power_dbm",
                "tx_power_dbm",
                "temperature_c",
                "voltage_v",
                "bias_current_ma",
            },
        }

        for family, channels in expected_channels.items():
            with self.subTest(family=family):
                response = self.query(family)
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["schema_version"], "2.0")
                self.assertEqual(payload["sensor"]["server_id"], "srv-prtg-01")
                self.assertEqual(payload["sensor"]["sensor_type"], family)
                self.assertEqual(set(payload["channels"]), channels)
                for channel in payload["channels"].values():
                    timestamps = [
                        sample["timestamp"] for sample in channel["samples"]
                    ]
                    self.assertEqual(timestamps, sorted(timestamps))
                    self.assertTrue(all(value.endswith("Z") for value in timestamps))
                    self.assertTrue(
                        all(
                            isinstance(sample["value"], (int, float))
                            and not isinstance(sample["value"], bool)
                            for sample in channel["samples"]
                        )
                    )

    def test_range_mode_reaches_provider_with_utc_boundaries(self) -> None:
        self.login()
        end = self.simulator.multichannel.current_time
        start = end - timedelta(minutes=10)
        response = self.client.get(
            "/api/v2/metrics/history",
            query_string={
                "server": "PRTG LAB 01",
                "sensor_id": self.sensor_id("cpu"),
                "sensor_type": "cpu",
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.source.calls[-1]["points"])
        self.assertIs(self.source.calls[-1]["start"].tzinfo, UTC)
        self.assertIs(self.source.calls[-1]["end"].tzinfo, UTC)
        samples = response.get_json()["channels"]["cpu_usage_pct"]["samples"]
        self.assertEqual(len(samples), 3)

    def test_traffic_contract_has_independent_in_and_out_bps_series(self) -> None:
        self.login()
        response = self.query("traffic", points=7)

        self.assertEqual(response.status_code, 200)
        channels = response.get_json()["channels"]
        inbound = channels["traffic_in_bps"]
        outbound = channels["traffic_out_bps"]
        self.assertEqual(inbound["unit"], "bps")
        self.assertEqual(outbound["unit"], "bps")
        self.assertEqual(len(inbound["samples"]), 7)
        self.assertEqual(len(outbound["samples"]), 7)
        self.assertNotEqual(inbound["samples"], outbound["samples"])

    def test_sfp_contract_accepts_rx_tx_only_subset(self) -> None:
        self.login()
        response = self.query("sfp", sensor_index=2)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.get_json()["channels"]),
            {"rx_power_dbm", "tx_power_dbm"},
        )

    def test_partial_issues_and_unknown_channels_are_successful(self) -> None:
        self.login()
        self.source.scenario = "unknown_channel"
        unknown_response = self.query("traffic", points=3)
        self.assertEqual(unknown_response.status_code, 200)
        self.assertTrue(unknown_response.get_json()["unknown_channels"])
        self.assertTrue(unknown_response.get_json()["channels"])

        self.source.scenario = None
        self.source.fault = "n_a"
        issue_response = self.query("traffic", points=3)
        self.assertEqual(issue_response.status_code, 200)
        self.assertEqual(
            {item["code"] for item in issue_response.get_json()["issues"]},
            {"invalid_value"},
        )
        self.assertIn("traffic_out_bps", issue_response.get_json()["channels"])

    def test_unknown_server_and_sensor_use_distinct_404_codes(self) -> None:
        self.login()
        missing_server = self.client.get(
            "/api/v2/metrics/history",
            query_string={
                "server": "PRTG LAB 99",
                "sensor_id": self.sensor_id("ping"),
                "sensor_type": "ping",
                "points": 5,
            },
        )
        missing_sensor = self.client.get(
            "/api/v2/metrics/history",
            query_string={
                "server": "PRTG LAB 01",
                "sensor_id": 999999,
                "sensor_type": "ping",
                "points": 5,
            },
        )

        self.assertEqual(missing_server.status_code, 404)
        self.assertEqual(missing_server.get_json()["code"], "server_not_found")
        self.assertEqual(missing_sensor.status_code, 404)
        self.assertEqual(missing_sensor.get_json()["code"], "sensor_not_found")

    def test_invalid_sensor_type_is_a_400(self) -> None:
        self.login()
        response = self.client.get(
            "/api/v2/metrics/history",
            query_string={
                "server": "PRTG LAB 01",
                "sensor_id": self.sensor_id("ping"),
                "sensor_type": "not-a-sensor-family",
                "points": 5,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "invalid_request")

    def test_upstream_and_unexpected_errors_are_safe(self) -> None:
        self.login()
        self.source.error = HistoricalMetricsSourceError(
            "password=must-not-leak passhash=must-not-leak"
        )
        upstream = self.query("ping")
        self.assertEqual(upstream.status_code, 502)
        self.assertEqual(upstream.get_json()["code"], "upstream_error")
        self.assertNotIn("must-not-leak", upstream.get_data(as_text=True))

        with mock.patch.object(
            self.flask_app.history_service,
            "get_normalized_history",
            side_effect=RuntimeError("password=must-not-leak"),
        ):
            unexpected = self.query("ping")
        self.assertEqual(unexpected.status_code, 500)
        self.assertEqual(unexpected.get_json()["code"], "internal_error")
        self.assertNotIn("must-not-leak", unexpected.get_data(as_text=True))
        self.assertNotIn("traceback", unexpected.get_data(as_text=True).casefold())

    def test_unconfigured_normalized_provider_is_a_502(self) -> None:
        app_without_provider = self.app_module.create_app()
        app_without_provider.config.update(TESTING=True)
        client = app_without_provider.test_client()
        login = client.post(
            "/",
            data={
                "username": "metrics-admin",
                "password": "synthetic-password",
            },
        )
        self.assertEqual(login.status_code, 302)

        response = client.get(
            "/api/v2/metrics/history",
            query_string={
                "server": "PRTG LAB 01",
                "sensor_id": self.sensor_id("ping"),
                "sensor_type": "ping",
                "points": 5,
            },
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["code"], "upstream_error")


if __name__ == "__main__":
    unittest.main()
