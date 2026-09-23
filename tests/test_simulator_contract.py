from __future__ import annotations

import csv
import io
import ipaddress
import unittest
from datetime import datetime, timedelta
from unittest import mock

import datos_simulados


DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
    )
)


class SimulatorContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.simulator = datos_simulados.Simulator(
            server_count=13,
            sensor_count=1000,
            seed=20260923,
        )

    def test_default_lab_shape_and_synthetic_identity(self) -> None:
        self.assertEqual(len(self.simulator.servers), 13)

        sensors = [
            sensor
            for server in self.simulator.servers.values()
            for sensor in server.sensors.values()
        ]
        self.assertEqual(len(sensors), 1000)
        self.assertEqual(len({sensor.objid for sensor in sensors}), 1000)

        for slug, server in self.simulator.servers.items():
            with self.subTest(server=slug):
                self.assertRegex(slug, r"^prtg-\d{2}$")
                self.assertRegex(server.name, r"^PRTG LAB \d{2}$")
                self.assertTrue(server.sensors)

                for device_name in server.device_names.values():
                    self.assertRegex(device_name, r"^SITE-\d{2}-\d{3}$")

                for address in server.devices.values():
                    parsed = ipaddress.ip_address(address)
                    self.assertTrue(
                        any(parsed in network for network in DOCUMENTATION_NETWORKS),
                        address,
                    )

                for sensor in server.sensors.values():
                    self.assertIn(sensor.status_raw, datos_simulados.STATUS_NAMES)
                    self.assertEqual(sensor.message, "Synthetic monitoring data")

    def test_supported_status_contract(self) -> None:
        self.assertEqual(
            datos_simulados.STATUS_NAMES,
            {
                1: "Unknown",
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

    def test_table_endpoints_match_prtg_subset(self) -> None:
        with mock.patch.object(datos_simulados, "sim", self.simulator):
            client = datos_simulados.app.test_client()

            devices_response = client.get("/prtg-01/api/table.json?content=devices")
            sensors_response = client.get("/prtg-01/api/table.json?content=sensors")

        self.assertEqual(devices_response.status_code, 200)
        self.assertEqual(sensors_response.status_code, 200)

        devices = devices_response.get_json()["devices"]
        sensors = sensors_response.get_json()["sensors"]

        self.assertTrue(devices)
        self.assertTrue(sensors)
        self.assertEqual(set(devices[0]), {"objid", "host"})
        self.assertEqual(
            set(sensors[0]),
            {
                "device",
                "sensor",
                "status",
                "status_raw",
                "lastvalue",
                "message",
                "parentid",
                "objid",
            },
        )

    def test_historic_csv_preserves_current_ping_contract(self) -> None:
        server = self.simulator.servers["prtg-01"]
        sensor_id = next(iter(server.sensors))
        end = datetime(2026, 9, 23, 12, 0, 0)
        start = end - timedelta(minutes=15)

        query = {
            "id": sensor_id,
            "sdate": start.strftime("%Y-%m-%d-%H-%M-%S"),
            "edate": end.strftime("%Y-%m-%d-%H-%M-%S"),
            "avg": 300,
        }

        with mock.patch.object(datos_simulados, "sim", self.simulator):
            response = datos_simulados.app.test_client().get(
                "/prtg-01/api/historicdata.csv",
                query_string=query,
            )

        self.assertEqual(response.status_code, 200)
        rows = list(
            csv.reader(
                io.StringIO(response.get_data(as_text=True)),
                delimiter=";",
            )
        )
        self.assertEqual(
            rows[0],
            [
                "Date Time",
                "Ping Time",
                "Minimum",
                "Maximum",
                "Packet Loss",
                "Downtime",
                "Coverage",
            ],
        )
        self.assertGreaterEqual(len(rows), 2)
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows[1:]))

    def test_pause_indefinite_and_resume_contract(self) -> None:
        server = self.simulator.servers["prtg-01"]
        sensor_id = next(iter(server.sensors))
        sensor = server.sensors[sensor_id]
        original_status = sensor.status_raw

        with mock.patch.object(datos_simulados, "sim", self.simulator):
            client = datos_simulados.app.test_client()

            timed = client.get(
                "/prtg-01/api/pauseobjectfor.htm",
                query_string={
                    "id": sensor_id,
                    "duration": 60,
                    "pausemsg": "Synthetic test pause",
                },
            )
            self.assertEqual(timed.status_code, 200)
            self.assertEqual(sensor.status_raw, 12)
            self.assertIsNotNone(sensor.pause_until)
            self.assertFalse(sensor.pause_indefinite)

            indefinite = client.get(
                "/prtg-01/api/pause.htm",
                query_string={
                    "id": sensor_id,
                    "action": 0,
                    "pausemsg": "Synthetic indefinite pause",
                },
            )
            self.assertEqual(indefinite.status_code, 200)
            self.assertEqual(sensor.status_raw, 9)
            self.assertTrue(sensor.pause_indefinite)
            self.assertIsNone(sensor.pause_until)

            resumed = client.get(
                "/prtg-01/api/pause.htm",
                query_string={"id": sensor_id, "action": 1},
            )
            self.assertEqual(resumed.status_code, 200)
            self.assertEqual(sensor.status_raw, original_status)
            self.assertFalse(sensor.pause_indefinite)
            self.assertIsNone(sensor.pause_until)


if __name__ == "__main__":
    unittest.main()
