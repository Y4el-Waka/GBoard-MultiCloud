from __future__ import annotations

import ast
import importlib
import json
import logging
import os
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from flask import Flask, Response, jsonify, request
from werkzeug.serving import WSGIRequestHandler, make_server

import config
from history_service import (
    HistoricalMetricsAuthenticationError,
    HistoricalMetricsInvalidResponseError,
    HistoricalMetricsRateLimitError,
    HistoricalMetricsSourceError,
    HistoricalMetricsTransportError,
    HistoricalMetricsUnknownSensorError,
    HistoricalMetricsUnsupportedSensorError,
    HistoryService,
)
from prtg_client import PRTGClient
from prtg_discovery import (
    PRTGDiscoveryConnectionError,
    PRTGDiscoveryUpstreamError,
)
from real_metrics_provider import (
    PRTGHistoryRateLimiter,
    RealHistoricalMetricsSource,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "prtg_discovery"


class QuietHandler(WSGIRequestHandler):
    def log(self, request_type, message, *args):
        return None


class FixturePRTGHTTPServer:
    def __init__(self) -> None:
        self.manifest = json.loads(
            (FIXTURES / "manifest.json").read_text(encoding="utf-8")
        )
        self.by_sensor_id = {
            str(case["sensor_payload"]["sensors"][0]["objid"]): (family, case)
            for family, case in self.manifest["cases"].items()
        }
        self.calls: list[tuple[str, dict[str, str]]] = []
        app = Flask("fixture-prtg")

        @app.before_request
        def record_and_authenticate():
            self.calls.append((request.path, request.args.to_dict(flat=True)))
            if (
                request.args.get("username") != "fixture-user"
                or request.args.get("passhash") != "fixture-passhash"
            ):
                return Response("unauthorized", status=401)
            return None

        @app.get("/<slug>/api/table.json")
        def table_json(slug: str):
            if request.args.get("content") != "sensors":
                return jsonify({"sensors": []})
            sensor_id = str(request.args.get("filter_objid") or "")
            selected = self.by_sensor_id.get(sensor_id)
            if selected is None:
                return jsonify({"sensors": []})
            payload = dict(selected[1]["sensor_payload"])
            payload["sensors"] = selected[1]["sensor_payload"]["sensors"]
            return jsonify(payload)

        @app.get("/<slug>/api/table.csv")
        def table_csv(slug: str):
            if request.args.get("content") != "channels":
                return Response("invalid", status=400)
            sensor_id = str(request.args.get("id") or "")
            selected = self.by_sensor_id.get(sensor_id)
            if selected is None:
                return Response('"Channel";"Last Value"\n', mimetype="text/csv")
            text = (FIXTURES / selected[1]["channels"]).read_text(encoding="utf-8")
            return Response(text, mimetype="text/csv")

        @app.get("/<slug>/api/historicdata.csv")
        def historic_csv(slug: str):
            sensor_id = str(request.args.get("id") or "")
            selected = self.by_sensor_id.get(sensor_id)
            if selected is None:
                return Response("not found", status=404)
            family, case = selected
            if slug == "localized" and family == "cpu":
                fixture = "history_cpu_es.csv"
            elif family == "sfp":
                fixture = "history_sfp_partial.csv"
            else:
                fixture = case.get("history")
            if not fixture:
                return Response("not found", status=404)
            return Response(
                (FIXTURES / fixture).read_text(encoding="utf-8"),
                mimetype="text/csv",
            )

        self._server = make_server(
            "127.0.0.1",
            0,
            app,
            threaded=True,
            request_handler=QuietHandler,
        )
        self.port = self._server.server_port
        self.thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self.thread.join(timeout=5)
        self._server.server_close()

    def server_config(
        self,
        *,
        server_id: str = "srv_fixture_a",
        name: str = "PRTG Fixture A",
        slug: str = "fixture",
        passhash: str = "fixture-passhash",
    ) -> dict[str, Any]:
        return {
            "id": server_id,
            "type": "prtg",
            "enabled": True,
            "name": name,
            "url": f"http://127.0.0.1:{self.port}/{slug}",
            "username": "fixture-user",
            "passhash": passhash,
        }


class RealMetricsProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.http = FixturePRTGHTTPServer()
        cls.http.start()
        cls.http_settings = config.HTTPSettings(
            retries=0,
            backoff=0,
            pool_connections=4,
            pool_maxsize=4,
        )
        cls.family_ids = {
            family: int(case["sensor_payload"]["sensors"][0]["objid"])
            for family, case in cls.http.manifest["cases"].items()
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.http.stop()

    def setUp(self) -> None:
        self.http.calls.clear()
        self.server = self.http.server_config()

    def client_factory(self):
        return PRTGClient(
            timeout=3,
            verify_ssl=True,
            http_settings=self.http_settings,
            logger=logging.getLogger("test.real_metrics_provider"),
        )

    def provider(self, **overrides: Any) -> RealHistoricalMetricsSource:
        options = {
            "client_factory": self.client_factory,
            "source_timezone": "UTC",
            "history_average_seconds": 300,
            "clock": lambda: datetime(2026, 9, 26, 12, 5, tzinfo=UTC),
        }
        options.update(overrides)
        return RealHistoricalMetricsSource(**options)

    def history_service(
        self,
        provider: RealHistoricalMetricsSource,
        servers: dict[str, dict[str, Any]] | None = None,
    ) -> HistoryService:
        selected = servers or {self.server["name"]: self.server}
        return HistoryService(
            server_provider=lambda: selected,
            client_factory=self.client_factory,
            cache_ttl_seconds=60,
            cache_max_entries=8,
            normalized_source=provider,
        )

    def test_metadata_classifies_five_families_with_explicit_evidence(self) -> None:
        provider = self.provider()

        for family in ("ping", "cpu", "memory", "traffic", "sfp"):
            with self.subTest(family=family):
                metadata = provider.fetch_sensor_metadata(
                    self.server,
                    self.family_ids[family],
                )
                self.assertEqual(metadata["server_id"], "srv_fixture_a")
                self.assertEqual(metadata["sensor_type"], family)
                self.assertEqual(metadata["classification_status"], "supported")
                self.assertEqual(
                    metadata["source_prtg_version"],
                    "fixture-0.4A",
                )
                self.assertTrue(metadata["classification_evidence"])
                self.assertTrue(metadata["channels"])

        traffic = provider.fetch_sensor_metadata(
            self.server,
            self.family_ids["traffic"],
        )
        traffic_kinds = {
            kind
            for channel in traffic["channels"]
            for kind in channel["kinds"]
        }
        self.assertIn("rate", traffic_kinds)
        self.assertIn("volume", traffic_kinds)
        volume_channels = [
            channel
            for channel in traffic["channels"]
            if "volume" in channel["kinds"]
        ]
        self.assertTrue(volume_channels)
        self.assertTrue(
            all("non_rate_volume" in channel["issues"] for channel in volume_channels)
        )

    def test_unknown_and_unsupported_are_explicit_and_never_ping(self) -> None:
        provider = self.provider()
        unknown = provider.fetch_sensor_metadata(
            self.server,
            self.family_ids["unknown"],
        )
        unsupported = provider.fetch_sensor_metadata(
            self.server,
            self.family_ids["unsupported"],
        )

        self.assertEqual(unknown["classification_status"], "unknown")
        self.assertEqual(unknown["sensor_type"], "unknown")
        self.assertEqual(unsupported["classification_status"], "unsupported")
        with self.assertRaises(HistoricalMetricsUnknownSensorError):
            provider.fetch_history(
                self.server,
                self.family_ids["unknown"],
                points=2,
                start=None,
                end=None,
            )
        with self.assertRaises(HistoricalMetricsUnsupportedSensorError):
            provider.fetch_history(
                self.server,
                self.family_ids["unsupported"],
                points=2,
                start=None,
                end=None,
            )

    def test_discovery_failures_preserve_transport_and_invalid_categories(self) -> None:
        cases = (
            (PRTGDiscoveryConnectionError("secret"), HistoricalMetricsTransportError),
            (PRTGDiscoveryUpstreamError("secret"), HistoricalMetricsInvalidResponseError),
        )
        for discovery_error, source_error in cases:
            with self.subTest(source_error=source_error.__name__):
                discovery = mock.Mock()
                discovery.discover_sensor.side_effect = discovery_error
                provider = self.provider(discovery=discovery)
                with self.assertRaises(source_error):
                    provider.fetch_sensor_metadata(
                        self.server,
                        self.family_ids["ping"],
                    )

    def test_metadata_cache_uses_compound_identity_ttl_and_no_error_entries(self) -> None:
        monotonic = [100.0]
        provider = self.provider(
            monotonic_clock=lambda: monotonic[0],
            metadata_cache_ttl_seconds=10,
        )
        sensor_id = self.family_ids["ping"]

        provider.fetch_sensor_metadata(self.server, sensor_id)
        first_call_count = len(self.http.calls)
        provider.fetch_sensor_metadata(self.server, sensor_id)
        self.assertEqual(len(self.http.calls), first_call_count)
        self.assertEqual(provider.metadata_cache_size(), 1)

        second_server = self.http.server_config(
            server_id="srv_fixture_b",
            name="PRTG Fixture B",
        )
        provider.fetch_sensor_metadata(second_server, sensor_id)
        self.assertEqual(provider.metadata_cache_size(), 2)

        monotonic[0] = 111.0
        provider.fetch_sensor_metadata(self.server, sensor_id)
        self.assertGreater(len(self.http.calls), first_call_count)

        bad_server = {**self.server, "passhash": "wrong-fixture-secret"}
        with self.assertRaises(HistoricalMetricsAuthenticationError):
            provider.fetch_sensor_metadata(bad_server, self.family_ids["cpu"])
        self.assertEqual(provider.metadata_cache_size(), 2)

    def test_rate_limiter_is_per_server_and_resets_after_window(self) -> None:
        now = [10.0]
        limiter = PRTGHistoryRateLimiter(
            max_requests=2,
            window_seconds=60,
            clock=lambda: now[0],
        )

        limiter.acquire("srv_a")
        limiter.acquire("srv_a")
        with self.assertRaises(HistoricalMetricsRateLimitError):
            limiter.acquire("srv_a")
        limiter.acquire("srv_b")
        now[0] = 71.0
        limiter.acquire("srv_a")

    def test_points_and_range_use_real_bounds_without_fabricating_samples(self) -> None:
        provider = self.provider(source_timezone="America/Mexico_City")
        payload = provider.fetch_history(
            self.server,
            self.family_ids["ping"],
            points=2,
            start=None,
            end=None,
        )
        history_call = next(
            options
            for path, options in self.http.calls
            if path.endswith("historicdata.csv")
        )

        self.assertEqual(payload["format"], "prtg_historic_csv")
        self.assertEqual(payload["requested_points"], 2)
        self.assertEqual(history_call["avg"], "300")
        self.assertEqual(history_call["edate"], "2026-09-26-06-05-00")
        self.assertEqual(history_call["sdate"], "2026-09-26-05-55-00")

        ranged = self.provider().fetch_history(
            self.server,
            self.family_ids["cpu"],
            points=None,
            start=datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
            end=datetime(2026, 9, 26, 12, 5, tzinfo=UTC),
        )
        self.assertIsNone(ranged["requested_points"])
        self.assertEqual(
            ranged["requested_start"],
            datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
        )

    def test_history_service_normalizes_all_families_and_partial_sfp(self) -> None:
        service = self.history_service(self.provider())
        expected = {
            "ping": {"latency_ms", "packet_loss_pct", "availability_pct"},
            "cpu": {"cpu_usage_pct"},
            "memory": {"memory_usage_pct", "memory_used_bytes"},
            "traffic": {"traffic_in_bps", "traffic_out_bps", "utilization_pct"},
            "sfp": {"rx_power_dbm", "tx_power_dbm"},
        }

        traffic = None
        for family, channels in expected.items():
            with self.subTest(family=family):
                result = service.get_normalized_history(
                    self.server["name"],
                    self.family_ids[family],
                    sensor_type=family,
                    points=2,
                )
                self.assertEqual(set(result.by_channel()), channels)
                self.assertTrue(all(sample.timestamp.tzinfo is UTC for sample in result.samples))
                self.assertLessEqual(
                    len({sample.timestamp for sample in result.samples}),
                    2,
                )
                if family == "traffic":
                    traffic = result

        self.assertIsNotNone(traffic)
        self.assertEqual(traffic.by_channel()["traffic_in_bps"][0].unit, "bps")
        self.assertEqual(traffic.by_channel()["traffic_out_bps"][0].unit, "bps")

    def test_localized_csv_uses_explicit_timezone_and_decimal_comma(self) -> None:
        localized = self.http.server_config(
            server_id="srv_fixture_localized",
            name="PRTG Fixture Localized",
            slug="localized",
        )
        service = self.history_service(
            self.provider(
                source_timezone="America/Mexico_City",
                clock=lambda: datetime(2026, 9, 26, 18, 5, tzinfo=UTC),
            ),
            {localized["name"]: localized},
        )

        result = service.get_normalized_history(
            localized["name"],
            self.family_ids["cpu"],
            sensor_type="cpu",
            points=2,
        )

        samples = result.by_channel()["cpu_usage_pct"]
        self.assertEqual([sample.value for sample in samples], [42.5, 47.5])
        self.assertEqual(samples[0].timestamp.hour, 18)
        self.assertIs(samples[0].timestamp.tzinfo, UTC)

    def test_points_mode_does_not_present_samples_outside_requested_window(self) -> None:
        service = self.history_service(
            self.provider(
                clock=lambda: datetime(2026, 9, 26, 13, 5, tzinfo=UTC),
            )
        )

        result = service.get_normalized_history(
            self.server["name"],
            self.family_ids["ping"],
            sensor_type="ping",
            points=2,
        )

        self.assertEqual(result.samples, ())
        self.assertEqual(result.channels, ())

    def test_provider_is_read_only_safe_and_has_no_synthetic_catalog_dependency(self) -> None:
        tree = ast.parse(
            (ROOT / "real_metrics_provider.py").read_text(encoding="utf-8")
        )
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("flask", imports)
        self.assertNotIn("synthetic_sensor_catalog", imports)
        source = (ROOT / "real_metrics_provider.py").read_text(encoding="utf-8")
        self.assertNotIn("pause_for", source)
        self.assertNotIn("resume(", source)

    def test_provider_rejects_credentialed_urls_queries_and_non_tls_hosts(self) -> None:
        provider = self.provider()
        unsafe_urls = (
            "https://user:secret@192.0.2.10/prtg",
            "https://192.0.2.10/prtg?passhash=secret",
            "http://192.0.2.10/prtg",
        )
        for url in unsafe_urls:
            with self.subTest(url=url):
                with self.assertRaises(HistoricalMetricsSourceError):
                    provider.fetch_sensor_metadata(
                        {**self.server, "url": url},
                        self.family_ids["ping"],
                    )


class RealMetricsFlaskIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.http = FixturePRTGHTTPServer()
        cls.http.start()
        cls.runtime = tempfile.TemporaryDirectory()
        root = Path(cls.runtime.name)
        cls.server = cls.http.server_config(
            server_id="srv_fixture_api",
            name="PRTG Fixture API",
        )
        snapshot = root / "snapshot.json"
        snapshot.write_text(
            json.dumps(
                {
                    "ultima_actualizacion": "2026-09-26 12:00:00",
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
                "APP_SECRET_KEY": "fixture-api-session-secret",
                "DB_PATH": str(root / "app.db"),
                "OUTPUT_PATH": str(snapshot),
                "PRTG_SERVERS_JSON": json.dumps([cls.server]),
                "PRTG_VERIFY_SSL": "0",
                "COREVIEW_METRICS_PROVIDER": "disabled",
                "SEED_DEFAULT_USERS": "0",
                "DEFAULT_USERS_JSON": "[]",
                "SESSION_COOKIE_SECURE": "0",
            },
            clear=False,
        )
        cls.environment.start()
        sys.modules.pop("app", None)
        cls.app_module = importlib.import_module("app")
        http_settings = config.HTTPSettings(0, 0, 4, 4)

        def client_factory():
            return PRTGClient(
                timeout=3,
                verify_ssl=True,
                http_settings=http_settings,
            )

        cls.provider = RealHistoricalMetricsSource(
            client_factory=client_factory,
            source_timezone="UTC",
            clock=lambda: datetime(2026, 9, 26, 12, 5, tzinfo=UTC),
        )
        cls.flask_app = cls.app_module.create_app(
            normalized_history_source=cls.provider
        )
        cls.flask_app.config.update(TESTING=True)
        cls.app_module.add_user(
            username="fixture-admin",
            password="synthetic-password",
            rol="admon",
            email="fixture-admin@example.test",
        )
        cls.manifest = cls.http.manifest

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("app", None)
        cls.environment.stop()
        cls.runtime.cleanup()
        cls.http.stop()

    def setUp(self) -> None:
        self.client = self.flask_app.test_client()

    def login(self) -> None:
        response = self.client.post(
            "/",
            data={"username": "fixture-admin", "password": "synthetic-password"},
        )
        self.assertEqual(response.status_code, 302)

    def sensor_id(self, family: str) -> int:
        return int(
            self.manifest["cases"][family]["sensor_payload"]["sensors"][0]["objid"]
        )

    def test_composition_root_selects_prtg_only_when_explicitly_configured(self) -> None:
        source = mock.Mock()
        settings = replace(
            self.app_module.APP_SETTINGS,
            metrics_provider="prtg",
            prtg_source_timezone="UTC",
        )
        with (
            mock.patch.object(self.app_module, "APP_SETTINGS", settings),
            mock.patch.object(
                self.app_module,
                "RealHistoricalMetricsSource",
                return_value=source,
            ) as constructor,
        ):
            configured_app = self.app_module.create_app()

        self.assertIs(
            configured_app.history_service._normalized_source,
            source,
        )
        constructor.assert_called_once()

    def test_full_fixture_http_to_authenticated_v2_json(self) -> None:
        unauthenticated = self.client.get(
            "/api/v2/metrics/sensor-metadata",
            query_string={"server": self.server["name"], "sensor_id": self.sensor_id("ping")},
        )
        self.assertEqual(unauthenticated.status_code, 401)
        self.login()

        expected = {
            "ping": {"latency_ms", "packet_loss_pct", "availability_pct"},
            "cpu": {"cpu_usage_pct"},
            "memory": {"memory_usage_pct", "memory_used_bytes"},
            "traffic": {"traffic_in_bps", "traffic_out_bps", "utilization_pct"},
            "sfp": {"rx_power_dbm", "tx_power_dbm"},
        }
        for family, expected_channels in expected.items():
            sensor_id = self.sensor_id(family)
            metadata = self.client.get(
                "/api/v2/metrics/sensor-metadata",
                query_string={"server": self.server["name"], "sensor_id": sensor_id},
            )
            history = self.client.get(
                "/api/v2/metrics/history",
                query_string={
                    "server": self.server["name"],
                    "sensor_id": sensor_id,
                    "sensor_type": family,
                    "points": 2,
                },
            )
            self.assertEqual(metadata.status_code, 200, family)
            self.assertEqual(history.status_code, 200, family)
            payload = history.get_json()
            self.assertEqual(payload["schema_version"], "2.0")
            self.assertEqual(payload["sensor"]["server_id"], "srv_fixture_api")
            self.assertEqual(set(payload["channels"]), expected_channels)
            for channel in payload["channels"].values():
                self.assertTrue(channel["samples"])
                self.assertTrue(
                    all(isinstance(item["value"], (int, float)) for item in channel["samples"])
                )
                self.assertTrue(
                    all(item["timestamp"].endswith("Z") for item in channel["samples"])
                )

        traffic = self.client.get(
            "/api/v2/metrics/history",
            query_string={
                "server": self.server["name"],
                "sensor_id": self.sensor_id("traffic"),
                "sensor_type": "traffic",
                "points": 2,
            },
        )
        self.assertEqual(traffic.status_code, 502)
        self.assertEqual(traffic.get_json()["code"], "upstream_rate_limited")


if __name__ == "__main__":
    unittest.main()
