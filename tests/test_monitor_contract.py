from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC
from pathlib import Path
from unittest import mock


class MonitorContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.class_runtime = tempfile.TemporaryDirectory()
        runtime = Path(cls.class_runtime.name)
        cls.server = {
            "id": "srv_test_01",
            "type": "prtg",
            "enabled": True,
            "name": "PRTG LAB 01",
            "url": "http://127.0.0.1:19090/prtg-01",
            "username": "synthetic-user",
            "password": "synthetic-test-password",
        }
        cls.environment = mock.patch.dict(
            os.environ,
            {
                "DB_PATH": str(runtime / "bootstrap.db"),
                "OUTPUT_PATH": str(runtime / "bootstrap.json"),
                "PRTG_SERVERS_JSON": json.dumps([cls.server]),
                "PRTG_SERVERS_FILE": str(runtime / "servers.json"),
                "PRTG_VERIFY_SSL": "0",
                "UPDATE_INTERVAL": "30",
                "REQUEST_TIMEOUT": "10",
                "MAX_WORKERS": "2",
                "METRICS_WINDOW_MINUTES": "120",
                "DEVICE_CACHE_TTL_SECONDS": "600",
                "PREVIOUS_STATE_MAX_AGE_MINUTES": "1440",
                "MF_SLA_HOURS": "6",
                "SENSOR_EVENT_RETENTION_DAYS": "90",
            },
            clear=False,
        )
        cls.environment.start()
        sys.modules.pop("monitor", None)
        cls.monitor_module = importlib.import_module("monitor")

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("monitor", None)
        cls.environment.stop()
        cls.class_runtime.cleanup()

    def setUp(self) -> None:
        self.runtime = tempfile.TemporaryDirectory()
        root = Path(self.runtime.name)
        self.db_path = root / "monitor.db"
        self.output_path = root / "snapshot.json"
        self.db_patch = mock.patch.object(
            self.monitor_module,
            "DB_PATH",
            self.db_path,
        )
        self.output_patch = mock.patch.object(
            self.monitor_module,
            "OUTPUT_PATH",
            self.output_path,
        )
        self.db_patch.start()
        self.output_patch.start()

    def tearDown(self) -> None:
        self.output_patch.stop()
        self.db_patch.stop()
        self.runtime.cleanup()

    def _sensor_payload(self) -> dict:
        return {
            "sensors": [
                {
                    "device": "SITE-01-001",
                    "sensor": "MF-Ping",
                    "status": "Down",
                    "status_raw": 5,
                    "lastvalue": "Request timed out",
                    "message": "Synthetic monitoring data",
                    "parentid": 10001,
                    "objid": 100001,
                },
                {
                    "device": "SITE-01-001",
                    "sensor": "CPU Load",
                    "status": "Up",
                    "status_raw": 3,
                    "lastvalue": "25 %",
                    "message": "Synthetic monitoring data",
                    "parentid": 10001,
                    "objid": 100002,
                },
                {
                    "device": "SITE-01-002",
                    "sensor": "Memory",
                    "status": "Warning",
                    "status_raw": 4,
                    "lastvalue": "85 %",
                    "message": "Synthetic monitoring data",
                    "parentid": 10002,
                    "objid": 100003,
                },
            ]
        }

    def test_load_servers_from_environment_and_file(self) -> None:
        loaded = self.monitor_module.load_servers()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], "PRTG LAB 01")

        servers_path = Path(self.runtime.name) / "servers-file.json"
        servers_path.write_text(json.dumps([self.server]), encoding="utf-8")

        with mock.patch.dict(
            os.environ,
            {
                "PRTG_SERVERS_JSON": "",
                "PRTG_SERVERS_FILE": str(servers_path),
            },
            clear=False,
        ):
            loaded_from_file = self.monitor_module.load_servers()

        self.assertEqual(loaded_from_file, loaded)

    def test_device_cache_expiry_uses_timezone_aware_utc(self) -> None:
        collector = self.monitor_module.PRTGMonitor([self.server])
        client = mock.Mock()
        client.get_devices.return_value = {
            "devices": [{"objid": 10001, "host": "192.0.2.10"}]
        }

        first = collector._get_device_ip_map(self.server, client)
        second = collector._get_device_ip_map(self.server, client)
        expires_at, cached = collector.device_ip_cache["PRTG LAB 01"]

        self.assertEqual(first, {"10001": "192.0.2.10"})
        self.assertIs(second, cached)
        self.assertEqual(expires_at.tzinfo, UTC)
        client.get_devices.assert_called_once_with(self.server)

    def test_status_normalization_map_is_frozen(self) -> None:
        self.assertEqual(
            self.monitor_module.PRTG_STATUS_MAP,
            {
                0: "None",
                1: "Unknown",
                2: "Scanning",
                3: "Up",
                4: "Warning",
                5: "Down",
                8: "Paused by Dependency",
                9: "Paused by Schedule",
                10: "Unusual",
                12: "Paused Until",
                13: "Down Acknowledged",
                14: "Down Partial",
            },
        )

    def test_snapshot_mf_persistence_and_event_deduplication(self) -> None:
        payload = self._sensor_payload()
        device_map = {
            "10001": "192.0.2.10",
            "10002": "198.51.100.20",
        }

        collector = self.monitor_module.PRTGMonitor([self.server])
        collector._fetch_server = mock.Mock(
            return_value=("PRTG LAB 01", payload, device_map)
        )

        with mock.patch.object(
            self.monitor_module,
            "load_servers",
            return_value=[self.server],
        ):
            first = collector.fetch()
            second = collector.fetch()

        self.assertEqual(
            set(first),
            {"ultima_actualizacion", "sensores", "logs", "metrics", "collector"},
        )
        self.assertEqual(set(first["logs"]), {"last_up", "last_down"})
        self.assertEqual(set(first["metrics"]), {"window_minutes", "series"})
        self.assertEqual(len(first["sensores"]), 3)
        self.assertEqual(len(first["logs"]["last_down"]), 1)
        self.assertEqual(len(first["logs"]["last_up"]), 0)
        self.assertEqual(len(first["metrics"]["series"]), 1)
        self.assertEqual(len(second["metrics"]["series"]), 2)

        required_sensor_fields = {
            "device",
            "sensor",
            "status",
            "status_code",
            "lastvalue",
            "grupo",
            "timestamp",
            "message",
            "ip_address",
            "sensor_id",
            "parent_id",
            "is_mf",
            "mf_alert_active",
            "mf_alert_started_at",
            "mf_sla_deadline",
            "mf_sla_hours",
            "mf_sla_breached",
        }
        for sensor in first["sensores"]:
            self.assertTrue(required_sensor_fields.issubset(sensor))

        mf_sensor = first["sensores"][0]
        self.assertTrue(mf_sensor["is_mf"])
        self.assertTrue(mf_sensor["mf_alert_active"])
        self.assertEqual(mf_sensor["mf_sla_hours"], 6)
        self.assertIsNotNone(mf_sensor["mf_alert_started_at"])
        self.assertIsNotNone(mf_sensor["mf_sla_deadline"])

        metric_point = first["metrics"]["series"][0]
        self.assertEqual(
            set(metric_point),
            {"t", "total", "up", "warning", "paused", "ack", "down"},
        )
        self.assertEqual(metric_point["total"], 3)
        self.assertEqual(metric_point["up"], 1)
        self.assertEqual(metric_point["warning"], 1)
        self.assertEqual(metric_point["down"], 1)

        on_disk = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["collector"]["servers_configured"], 1)
        self.assertEqual(len(on_disk["sensores"]), 3)

        with sqlite3.connect(self.db_path) as connection:
            state_count = connection.execute(
                "SELECT COUNT(*) FROM sensor_state"
            ).fetchone()[0]
            event_count_after_same_state = connection.execute(
                "SELECT COUNT(*) FROM sensor_events"
            ).fetchone()[0]
            mf_count = connection.execute(
                "SELECT COUNT(*) FROM mf_alert_state"
            ).fetchone()[0]

        self.assertEqual(state_count, 3)
        self.assertEqual(event_count_after_same_state, 1)
        self.assertEqual(mf_count, 1)

        restored = self.monitor_module.PRTGMonitor([self.server])
        self.assertEqual(
            restored.previous_states[("PRTG LAB 01", "100001")],
            "Down",
        )

        payload["sensors"][0]["status"] = "Up"
        payload["sensors"][0]["status_raw"] = 3
        payload["sensors"][0]["lastvalue"] = "10 ms"

        with mock.patch.object(
            self.monitor_module,
            "load_servers",
            return_value=[self.server],
        ):
            recovered = collector.fetch()

        self.assertEqual(len(recovered["logs"]["last_up"]), 1)

        with sqlite3.connect(self.db_path) as connection:
            events = connection.execute(
                "SELECT event_type FROM sensor_events ORDER BY id"
            ).fetchall()
            mf_count_after_recovery = connection.execute(
                "SELECT COUNT(*) FROM mf_alert_state"
            ).fetchone()[0]

        self.assertEqual([row[0] for row in events], ["alert", "recovery"])
        self.assertEqual(mf_count_after_recovery, 0)


if __name__ == "__main__":
    unittest.main()
