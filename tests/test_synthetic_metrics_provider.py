from __future__ import annotations

import ast
import importlib
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import requests
from werkzeug.serving import WSGIRequestHandler, make_server

import datos_simulados
from history_service import (
    HistoricalMetricsSensorNotFoundError,
    HistoricalMetricsSourceError,
)
from synthetic_metrics_provider import SyntheticHistoricalMetricsSource


def source_sample(timestamp: str, sensor_id: int = 100001) -> dict[str, Any]:
    return {
        "server_id": "srv-prtg-01",
        "sensor_type": "ping",
        "objid": sensor_id,
        "timestamp": timestamp,
        "channels": [{"channel": "Ping Time", "value": "10 ms"}],
    }


def source_payload(
    timestamps: list[str],
    *,
    sensor_id: int = 100001,
    tick_seconds: int = 300,
) -> dict[str, Any]:
    return {
        "synthetic": True,
        "server_id": "srv-prtg-01",
        "server_name": "PRTG LAB 01",
        "sensor_id": sensor_id,
        "sensor_type": "ping",
        "tick_seconds": tick_seconds,
        "samples": [source_sample(value, sensor_id) for value in timestamps],
    }


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.closed = False

    def json(self) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def close(self) -> None:
        self.closed = True


class RecordingGet:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error: Exception | None = None

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


class SyntheticMetricsProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = {
            "id": "srv-lab-01",
            "name": "PRTG LAB 01",
            "url": "http://127.0.0.1:19090/prtg-01",
            "username": "synthetic-user",
            "password": "synthetic-password",
        }

    def provider(self, request_get: RecordingGet) -> SyntheticHistoricalMetricsSource:
        return SyntheticHistoricalMetricsSource(
            timeout=7,
            request_get=request_get,
        )

    def test_points_mode_uses_local_history_endpoint_without_credentials(self) -> None:
        response = FakeResponse(
            source_payload(
                [
                    "2026-09-23T12:00:00Z",
                    "2026-09-23T12:05:00Z",
                ]
            )
        )
        request_get = RecordingGet(response)

        result = self.provider(request_get).fetch_history(
            self.server,
            100001,
            points=2,
            start=None,
            end=None,
        )

        url, options = request_get.calls[0]
        self.assertEqual(
            url,
            "http://127.0.0.1:19090/prtg-01/api/multichannel_history.json",
        )
        self.assertEqual(options["params"], {"id": "100001", "points": 2})
        self.assertEqual(options["timeout"], 7)
        self.assertFalse(options["allow_redirects"])
        self.assertNotIn("username", options["params"])
        self.assertNotIn("password", options["params"])
        self.assertNotIn("passhash", options["params"])
        self.assertEqual(result["sensor_id"], 100001)
        self.assertTrue(response.closed)

    def test_sensor_metadata_uses_explicit_source_type_without_credentials(self) -> None:
        response = FakeResponse(
            {
                "server_id": "srv-prtg-01",
                "objid": 100001,
                "sensor_type": "traffic",
            }
        )
        request_get = RecordingGet(response)

        result = self.provider(request_get).fetch_sensor_metadata(
            self.server,
            100001,
        )

        url, options = request_get.calls[0]
        self.assertEqual(
            url,
            "http://127.0.0.1:19090/prtg-01/api/multichannel.json",
        )
        self.assertEqual(options["params"], {"id": "100001"})
        self.assertEqual(
            result,
            {
                "server_id": "srv-prtg-01",
                "sensor_id": "100001",
                "sensor_type": "traffic",
            },
        )
        self.assertNotIn("username", options["params"])
        self.assertNotIn("password", options["params"])
        self.assertNotIn("passhash", options["params"])

    def test_default_transport_ignores_environment_proxy_settings(self) -> None:
        response = FakeResponse(source_payload(["2026-09-23T12:00:00Z"]))
        session = mock.MagicMock()
        session.__enter__.return_value = session
        session.get.return_value = response

        with mock.patch(
            "synthetic_metrics_provider.requests.Session",
            return_value=session,
        ):
            result = SyntheticHistoricalMetricsSource(timeout=7).fetch_history(
                self.server,
                100001,
                points=1,
                start=None,
                end=None,
            )

        self.assertFalse(session.trust_env)
        session.get.assert_called_once()
        self.assertEqual(result["sensor_id"], 100001)

    def test_range_mode_calculates_real_window_and_filters_boundaries(self) -> None:
        probe = FakeResponse(source_payload(["2026-09-23T12:10:00Z"]))
        window = FakeResponse(
            source_payload(
                [
                    "2026-09-23T12:00:00Z",
                    "2026-09-23T12:05:00Z",
                    "2026-09-23T12:10:00Z",
                ]
            )
        )
        request_get = RecordingGet(probe, window)

        result = self.provider(request_get).fetch_history(
            self.server,
            "100001",
            points=None,
            start=datetime(2026, 9, 23, 12, 1, tzinfo=UTC),
            end=datetime(2026, 9, 23, 12, 6, tzinfo=UTC),
        )

        self.assertEqual(request_get.calls[0][1]["params"]["points"], 1)
        self.assertEqual(request_get.calls[1][1]["params"]["points"], 3)
        self.assertEqual(
            [sample["timestamp"] for sample in result["samples"]],
            ["2026-09-23T12:05:00Z"],
        )

    def test_range_never_returns_out_of_window_or_fabricated_samples(self) -> None:
        probe = FakeResponse(source_payload(["2026-09-23T12:10:00Z"]))
        window = FakeResponse(
            source_payload(
                [
                    "2026-09-23T12:05:00Z",
                    "2026-09-23T12:10:00Z",
                ]
            )
        )
        request_get = RecordingGet(probe, window)

        with self.assertRaisesRegex(
            HistoricalMetricsSourceError,
            "no available samples",
        ):
            self.provider(request_get).fetch_history(
                self.server,
                100001,
                points=None,
                start=datetime(2026, 9, 23, 12, 6, tzinfo=UTC),
                end=datetime(2026, 9, 23, 12, 9, tzinfo=UTC),
            )

    def test_range_rejects_naive_future_and_over_limit_boundaries(self) -> None:
        provider = self.provider(
            RecordingGet(FakeResponse(source_payload(["2026-09-23T12:10:00Z"])))
        )
        with self.assertRaisesRegex(HistoricalMetricsSourceError, "timezone-aware"):
            provider.fetch_history(
                self.server,
                100001,
                points=None,
                start=datetime(2026, 9, 23, 12, 0),
                end=datetime(2026, 9, 23, 12, 10, tzinfo=UTC),
            )

        future_get = RecordingGet(
            FakeResponse(source_payload(["2026-09-23T12:10:00Z"]))
        )
        with self.assertRaisesRegex(HistoricalMetricsSourceError, "no available"):
            self.provider(future_get).fetch_history(
                self.server,
                100001,
                points=None,
                start=datetime(2026, 9, 23, 12, 11, tzinfo=UTC),
                end=datetime(2026, 9, 23, 12, 20, tzinfo=UTC),
            )

        limit_get = RecordingGet(
            FakeResponse(source_payload(["2026-09-23T12:10:00Z"]))
        )
        with self.assertRaisesRegex(HistoricalMetricsSourceError, "resolution"):
            self.provider(limit_get).fetch_history(
                self.server,
                100001,
                points=None,
                start=datetime(2026, 9, 19, 0, 0, tzinfo=UTC),
                end=datetime(2026, 9, 23, 12, 10, tzinfo=UTC),
            )

    def test_non_loopback_userinfo_query_and_redirects_are_rejected(self) -> None:
        unsafe_urls = (
            "http://192.0.2.10:9090/prtg-01",
            "http://user:secret@127.0.0.1:9090/prtg-01",
            "http://127.0.0.1:9090/prtg-01?target=elsewhere",
        )
        for url in unsafe_urls:
            with self.subTest(url=url):
                request_get = RecordingGet()
                with self.assertRaises(HistoricalMetricsSourceError):
                    self.provider(request_get).fetch_history(
                        {**self.server, "url": url},
                        100001,
                        points=2,
                        start=None,
                        end=None,
                    )
                self.assertEqual(request_get.calls, [])

        redirect = RecordingGet(
            FakeResponse(source_payload(["2026-09-23T12:00:00Z"]), 302)
        )
        with self.assertRaisesRegex(HistoricalMetricsSourceError, "HTTP error"):
            self.provider(redirect).fetch_history(
                self.server,
                100001,
                points=1,
                start=None,
                end=None,
            )

    def test_missing_sensor_connection_and_invalid_payload_errors_are_typed(self) -> None:
        not_found = RecordingGet(FakeResponse({}, 404))
        with self.assertRaises(HistoricalMetricsSensorNotFoundError):
            self.provider(not_found).fetch_history(
                self.server,
                999999,
                points=1,
                start=None,
                end=None,
            )

        unavailable = RecordingGet()
        unavailable.error = requests.ConnectionError(
            "http://127.0.0.1/?password=must-not-leak"
        )
        with self.assertRaisesRegex(
            HistoricalMetricsSourceError,
            "unavailable",
        ) as captured:
            self.provider(unavailable).fetch_history(
                self.server,
                100001,
                points=1,
                start=None,
                end=None,
            )
        self.assertNotIn("must-not-leak", str(captured.exception))

        invalid = RecordingGet(FakeResponse(["not", "an", "object"]))
        with self.assertRaisesRegex(HistoricalMetricsSourceError, "invalid payload"):
            self.provider(invalid).fetch_history(
                self.server,
                100001,
                points=1,
                start=None,
                end=None,
            )

    def test_provider_has_no_flask_or_metrics_adapter_dependency(self) -> None:
        path = Path(__file__).resolve().parents[1] / "synthetic_metrics_provider.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        self.assertNotIn("flask", imported_roots)
        self.assertNotIn("metrics", imported_roots)
        self.assertNotIn("sqlite3", imported_roots)


class QuietRequestHandler(WSGIRequestHandler):
    def log(self, type: str, message: str, *args: Any) -> None:
        return None


class SyntheticMetricsHTTPIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = tempfile.TemporaryDirectory()
        root = Path(cls.runtime.name)
        cls.db_path = root / "app.db"
        cls.data_path = root / "snapshot.json"
        cls.servers_path = root / "servers.json"

        cls.simulator = datos_simulados.Simulator(
            server_count=1,
            sensor_count=100,
            seed=20260923,
            tick_seconds=300,
            start_time=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
        )
        cls.simulator.tick(8)
        cls.simulator_patch = mock.patch.object(
            datos_simulados,
            "sim",
            cls.simulator,
        )
        cls.simulator_patch.start()
        cls.http_server = make_server(
            "127.0.0.1",
            0,
            datos_simulados.app,
            threaded=True,
            request_handler=QuietRequestHandler,
        )
        cls.http_thread = threading.Thread(
            target=cls.http_server.serve_forever,
            name="synthetic-metrics-http-test",
            daemon=True,
        )
        cls.http_thread.start()

        cls.server = {
            "id": "srv-lab-http-01",
            "type": "prtg",
            "enabled": True,
            "name": "PRTG LAB 01",
            "url": (
                f"http://127.0.0.1:{cls.http_server.server_port}/prtg-01"
            ),
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
                "APP_SECRET_KEY": "synthetic-http-session-secret",
                "DB_PATH": str(cls.db_path),
                "OUTPUT_PATH": str(cls.data_path),
                "PRTG_SERVERS_JSON": json.dumps([cls.server]),
                "PRTG_SERVERS_FILE": str(cls.servers_path),
                "PRTG_VERIFY_SSL": "0",
                "REQUEST_TIMEOUT": "3",
                "HISTORY_CACHE_TTL": "60",
                "SEED_DEFAULT_USERS": "0",
                "DEFAULT_USERS_JSON": "[]",
                "SESSION_COOKIE_SECURE": "0",
                "FLASK_DEBUG": "0",
                "COREVIEW_METRICS_PROVIDER": "synthetic",
            },
            clear=False,
        )
        cls.environment.start()
        sys.modules.pop("app", None)
        cls.app_module = importlib.import_module("app")
        cls.flask_app = cls.app_module.app
        cls.flask_app.config.update(TESTING=True)

        created = cls.app_module.add_user(
            username="metrics-http-admin",
            password="synthetic-password",
            rol="admon",
            email="metrics-http-admin@example.test",
        )
        if not created:
            raise RuntimeError("No se pudo crear el usuario temporal de pruebas")

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("app", None)
        cls.environment.stop()
        cls.http_server.shutdown()
        cls.http_server.server_close()
        cls.http_thread.join(timeout=5)
        cls.simulator_patch.stop()
        cls.runtime.cleanup()

    def setUp(self) -> None:
        self.client = self.flask_app.test_client()
        self.simulator.set_scenario("normal")

    def login(self) -> None:
        response = self.client.post(
            "/",
            data={
                "username": "metrics-http-admin",
                "password": "synthetic-password",
            },
        )
        self.assertEqual(response.status_code, 302)

    def sensor_id(self, family: str, index: int = 0) -> int:
        return self.simulator.multichannel.sensor_ids("prtg-01", family)[index]

    def query(self, family: str, *, index: int = 0, points: int = 5):
        return self.client.get(
            "/api/v2/metrics/history",
            query_string={
                "server": "PRTG LAB 01",
                "sensor_id": self.sensor_id(family, index),
                "sensor_type": family,
                "points": points,
            },
        )

    def test_normal_bootstrap_requires_authentication(self) -> None:
        response = self.query("ping")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "Sesion requerida"})

    def test_real_http_points_flow_supports_all_metric_families(self) -> None:
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

        for family, channel_keys in expected_channels.items():
            with self.subTest(family=family):
                response = self.query(family)
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["schema_version"], "2.0")
                self.assertEqual(
                    payload["sensor"]["server_id"],
                    "srv-lab-http-01",
                )
                self.assertEqual(set(payload["channels"]), channel_keys)
                for channel in payload["channels"].values():
                    timestamps = [
                        item["timestamp"] for item in channel["samples"]
                    ]
                    self.assertEqual(timestamps, sorted(timestamps))
                    self.assertTrue(
                        all(timestamp.endswith("Z") for timestamp in timestamps)
                    )

        traffic = self.query("traffic").get_json()["channels"]
        self.assertEqual(traffic["traffic_in_bps"]["unit"], "bps")
        self.assertEqual(traffic["traffic_out_bps"]["unit"], "bps")
        self.assertNotEqual(
            traffic["traffic_in_bps"]["samples"],
            traffic["traffic_out_bps"]["samples"],
        )

    def test_real_http_metadata_resolves_every_metric_family(self) -> None:
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
                sensor = response.get_json()["sensor"]
                self.assertEqual(sensor["server_id"], "srv-lab-http-01")
                self.assertEqual(sensor["sensor_type"], family)

    def test_real_http_range_mode_returns_only_requested_ticks(self) -> None:
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
        payload = response.get_json()
        samples = payload["channels"]["cpu_usage_pct"]["samples"]
        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0]["timestamp"], "2026-09-23T12:30:00Z")
        self.assertEqual(samples[-1]["timestamp"], "2026-09-23T12:40:00Z")
        self.assertEqual(payload["range"]["start"], samples[0]["timestamp"])
        self.assertEqual(payload["range"]["end"], samples[-1]["timestamp"])

    def test_real_http_partial_sfp_unknowns_and_issues_survive(self) -> None:
        self.login()
        partial = self.query("sfp", index=2)
        self.assertEqual(partial.status_code, 200)
        self.assertEqual(
            set(partial.get_json()["channels"]),
            {"rx_power_dbm", "tx_power_dbm"},
        )

        self.simulator.set_scenario("unknown_channel")
        unknown = self.query("traffic", points=2)
        self.assertEqual(unknown.status_code, 200)
        self.assertTrue(unknown.get_json()["unknown_channels"])
        self.assertIn("traffic_out_bps", unknown.get_json()["channels"])

        self.simulator.set_scenario("invalid_value")
        invalid = self.query("traffic", points=2)
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(
            {item["code"] for item in invalid.get_json()["issues"]},
            {"invalid_value"},
        )
        self.assertIn("traffic_out_bps", invalid.get_json()["channels"])

    def test_real_http_missing_sensor_is_404(self) -> None:
        self.login()
        response = self.client.get(
            "/api/v2/metrics/history",
            query_string={
                "server": "PRTG LAB 01",
                "sensor_id": 999999,
                "sensor_type": "ping",
                "points": 5,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["code"], "sensor_not_found")

    def test_real_provider_connection_failure_maps_to_safe_502(self) -> None:
        self.login()
        configured_server = self.flask_app.prtg_servers["PRTG LAB 01"]
        original_url = configured_server["url"]
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocked_port = blocker.getsockname()[1]
        configured_server["url"] = (
            f"http://127.0.0.1:{blocked_port}/prtg-01"
        )
        try:
            response = self.query("ping", points=2)
        finally:
            configured_server["url"] = original_url
            blocker.close()

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["code"], "upstream_error")
        serialized = response.get_data(as_text=True).casefold()
        self.assertNotIn("password", serialized)
        self.assertNotIn("127.0.0.1", serialized)

    def test_disabled_configuration_preserves_unavailable_v2_behavior(self) -> None:
        disabled_settings = replace(
            self.app_module.APP_SETTINGS,
            metrics_provider="disabled",
        )
        with mock.patch.object(
            self.app_module,
            "APP_SETTINGS",
            disabled_settings,
        ):
            disabled_app = self.app_module.create_app()
        disabled_app.config.update(TESTING=True)
        client = disabled_app.test_client()
        login = client.post(
            "/",
            data={
                "username": "metrics-http-admin",
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
                "points": 3,
            },
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["code"], "upstream_error")


if __name__ == "__main__":
    unittest.main()
