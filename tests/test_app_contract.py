from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ApplicationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = tempfile.TemporaryDirectory()
        root = Path(cls.runtime.name)
        cls.db_path = root / "app.db"
        cls.data_path = root / "snapshot.json"
        cls.servers_path = root / "servers.json"
        cls.server = {
            "id": "srv_test_01",
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
        cls.snapshot = {
            "ultima_actualizacion": "2026-09-23 12:00:00",
            "sensores": [
                {
                    "device": "SITE-01-001",
                    "sensor": "Ping",
                    "status": "Up",
                    "status_code": 3,
                    "lastvalue": "10 ms",
                    "grupo": "PRTG LAB 01",
                    "timestamp": "2026-09-23 12:00:00",
                    "message": "Synthetic monitoring data",
                    "ip_address": "192.0.2.10",
                    "sensor_id": "100001",
                    "parent_id": "10001",
                    "is_mf": False,
                    "mf_alert_active": False,
                    "mf_alert_started_at": None,
                    "mf_sla_deadline": None,
                    "mf_sla_hours": 6,
                    "mf_sla_breached": False,
                }
            ],
            "logs": {"last_up": [], "last_down": []},
            "metrics": {"window_minutes": 120, "series": []},
            "collector": {
                "type": "prtg",
                "servers_configured": 1,
                "servers_with_error": 0,
            },
        }
        cls.data_path.write_text(
            json.dumps(cls.snapshot),
            encoding="utf-8",
        )

        cls.environment = mock.patch.dict(
            os.environ,
            {
                "APP_SECRET_KEY": "synthetic-test-session-secret",
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
        sys.modules.pop("app", None)
        cls.app_module = importlib.import_module("app")
        cls.flask_app = cls.app_module.app
        cls.flask_app.config.update(TESTING=True)

        created = cls.app_module.add_user(
            username="test-admin",
            password="synthetic-password",
            rol="admon",
            email="test-admin@example.test",
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
        self.app_module.cache_clear()

    def _login(self):
        return self.client.post(
            "/",
            data={
                "username": "test-admin",
                "password": "synthetic-password",
            },
        )

    def test_healthcheck_contract(self) -> None:
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data_file_exists"])
        self.assertEqual(payload["servers"], 1)
        self.assertIn("history_cache_entries", payload)

    def test_invalid_login_contract(self) -> None:
        response = self.client.post(
            "/",
            data={
                "username": "test-admin",
                "password": "incorrect-synthetic-password",
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("Credenciales incorrectas", response.get_data(as_text=True))

    def test_unauthenticated_access_contract(self) -> None:
        dashboard = self.client.get("/dashboard")
        sensors = self.client.get("/api/sensors")

        self.assertEqual(dashboard.status_code, 302)
        self.assertTrue(dashboard.headers["Location"].endswith("/"))
        self.assertEqual(sensors.status_code, 401)
        self.assertEqual(sensors.get_json()["error"], "Sesion requerida")

    def test_valid_login_dashboard_and_me_contract(self) -> None:
        login = self._login()
        self.assertEqual(login.status_code, 302)
        self.assertTrue(login.headers["Location"].endswith("/dashboard"))

        dashboard = self.client.get("/dashboard")
        me = self.client.get("/api/me")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("test-admin", dashboard.get_data(as_text=True))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(
            me.get_json(),
            {
                "id": me.get_json()["id"],
                "username": "test-admin",
                "email": "test-admin@example.test",
                "rol": "admon",
            },
        )

    def test_authenticated_sensors_endpoint_preserves_snapshot(self) -> None:
        self._login()
        response = self.client.get("/api/sensors")

        self.assertEqual(response.status_code, 200)
        try:
            self.assertEqual(response.get_json(), self.snapshot)
        finally:
            response.close()

    def test_history_endpoint_preserves_current_ping_datasets(self) -> None:
        self._login()
        historical_csv = (
            "Date Time;Ping Time;Minimum;Maximum;Packet Loss;Downtime;Coverage\n"
            "2026-09-23 11:55:00;10.00 ms;8.00 ms;12.00 ms;0.00 %;0.00 %;100.00 %\n"
            "2026-09-23 12:00:00;11.00 ms;9.00 ms;13.00 ms;1.00 %;0.00 %;100.00 %\n"
        )
        with mock.patch.object(
            self.app_module.PRTGClient,
            "get_historic_csv",
            return_value=historical_csv,
        ) as get_historic_csv:
            response = self.client.get(
                "/api/history",
                query_string={
                    "group": "PRTG LAB 01",
                    "id": 100001,
                    "minutes": 120,
                    "avg": 300,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            [dataset["key"] for dataset in payload["datasets"]],
            [
                "ping",
                "minimum",
                "maximum",
                "packet_loss",
                "downtime",
                "coverage",
            ],
        )
        self.assertEqual(payload["unit"], "ms")
        self.assertEqual(payload["count"], 2)

        historic_call = get_historic_csv.call_args
        self.assertEqual(historic_call.args[0]["name"], "PRTG LAB 01")
        self.assertEqual(historic_call.args[1], 100001)
        self.assertEqual(historic_call.kwargs["average_seconds"], 300)

    def test_pause_and_resume_endpoints_preserve_prtg_calls(self) -> None:
        self._login()
        with (
            mock.patch.object(
                self.app_module.PRTGClient,
                "pause_for",
            ) as pause_for,
            mock.patch.object(
                self.app_module.PRTGClient,
                "pause_indefinitely",
            ) as pause_indefinitely,
            mock.patch.object(
                self.app_module.PRTGClient,
                "resume",
            ) as resume,
        ):
            paused = self.client.post(
                "/api/sensors/pause",
                json={
                    "group": "PRTG LAB 01",
                    "id": 100001,
                    "duration_minutes": 60,
                    "sensor_name": "Ping",
                    "device_name": "SITE-01-001",
                    "message": "Synthetic test pause",
                },
            )
            pause_call = pause_for.call_args

            paused_indefinitely = self.client.post(
                "/api/sensors/pause",
                json={
                    "group": "PRTG LAB 01",
                    "id": 100001,
                    "indefinite": True,
                    "sensor_name": "Ping",
                    "device_name": "SITE-01-001",
                    "message": "Synthetic indefinite pause",
                },
            )
            indefinite_call = pause_indefinitely.call_args

            resumed = self.client.post(
                "/api/sensors/resume",
                json={
                    "group": "PRTG LAB 01",
                    "id": 100001,
                    "sensor_name": "Ping",
                    "device_name": "SITE-01-001",
                },
            )
            resume_call = resume.call_args

        self.assertEqual(paused.status_code, 200)
        self.assertTrue(paused.get_json()["ok"])
        self.assertEqual(pause_call.args[0]["name"], "PRTG LAB 01")
        self.assertEqual(pause_call.args[1], 100001)
        self.assertEqual(pause_call.args[2], 60)

        self.assertEqual(paused_indefinitely.status_code, 200)
        self.assertTrue(paused_indefinitely.get_json()["ok"])
        self.assertEqual(
            paused_indefinitely.get_json()["message"],
            "Sensor pausado indefinidamente",
        )
        self.assertEqual(indefinite_call.args[0]["name"], "PRTG LAB 01")
        self.assertEqual(indefinite_call.args[1], 100001)

        self.assertEqual(resumed.status_code, 200)
        self.assertTrue(resumed.get_json()["ok"])
        self.assertEqual(resume_call.args[0]["name"], "PRTG LAB 01")
        self.assertEqual(resume_call.args[1], 100001)

    def test_prtg_client_errors_preserve_gateway_responses(self) -> None:
        self._login()
        transport_error = self.app_module.PRTGClientError(
            "sanitized synthetic failure"
        )

        with mock.patch.object(
            self.app_module.PRTGClient,
            "get_historic_csv",
            side_effect=transport_error,
        ):
            history = self.client.get(
                "/api/history",
                query_string={
                    "group": "PRTG LAB 01",
                    "id": 100001,
                },
            )

        with mock.patch.object(
            self.app_module.PRTGClient,
            "pause_for",
            side_effect=transport_error,
        ):
            paused = self.client.post(
                "/api/sensors/pause",
                json={
                    "group": "PRTG LAB 01",
                    "id": 100001,
                    "duration_minutes": 60,
                },
            )

        with mock.patch.object(
            self.app_module.PRTGClient,
            "resume",
            side_effect=transport_error,
        ):
            resumed = self.client.post(
                "/api/sensors/resume",
                json={
                    "group": "PRTG LAB 01",
                    "id": 100001,
                },
            )

        self.assertEqual(history.status_code, 502)
        self.assertEqual(
            history.get_json(),
            {"error": "No se pudo consultar PRTG"},
        )
        self.assertEqual(paused.status_code, 502)
        self.assertEqual(
            paused.get_json(),
            {"error": "No se pudo pausar el sensor en PRTG"},
        )
        self.assertEqual(resumed.status_code, 502)
        self.assertEqual(
            resumed.get_json(),
            {"error": "No se pudo reanudar el sensor en PRTG"},
        )


if __name__ == "__main__":
    unittest.main()
