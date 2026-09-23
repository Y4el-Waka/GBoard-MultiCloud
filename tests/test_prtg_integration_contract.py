from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

import requests

import config
import datos_simulados
from prtg_client import PRTGClient


class FlaskResponseAdapter:
    def __init__(self, response) -> None:
        self._response = response
        self.status_code = response.status_code
        self.text = response.get_data(as_text=True)

    def json(self):
        return self._response.get_json()

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"synthetic HTTP {self.status_code}")


class FlaskSessionAdapter:
    """Adapt requests.Session.get to the simulator's Flask test client."""

    def __init__(self) -> None:
        self.client = datos_simulados.app.test_client()

    def get(self, url, **kwargs):
        path = urlsplit(url).path
        response = self.client.get(
            path,
            query_string=kwargs.get("params", {}),
        )
        return FlaskResponseAdapter(response)

    def close(self) -> None:
        return None


class PRTGIntegrationContractTests(unittest.TestCase):
    def test_monitor_consumes_13_synthetic_servers_and_1000_sensors(self) -> None:
        simulator = datos_simulados.Simulator(
            server_count=13,
            sensor_count=1000,
            seed=20260923,
        )
        servers = [
            {
                "id": f"srv-{slug}",
                "type": "prtg",
                "enabled": True,
                "name": simulated.name,
                "url": f"http://127.0.0.1:19090/{slug}",
                "username": "synthetic-user",
                "password": "synthetic-password",
            }
            for slug, simulated in simulator.servers.items()
        ]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "monitor.db"
            output_path = root / "snapshot.json"

            environment = {
                "DB_PATH": str(db_path),
                "OUTPUT_PATH": str(output_path),
                "PRTG_SERVERS_JSON": json.dumps(servers),
                "PRTG_VERIFY_SSL": "0",
                "REQUEST_TIMEOUT": "10",
            }
            with (
                mock.patch.object(config, "load_environment"),
                mock.patch.dict(os.environ, environment, clear=False),
            ):
                sys.modules.pop("monitor", None)
                monitor_module = importlib.import_module("monitor")

            try:
                def client_factory(**kwargs):
                    return PRTGClient(
                        **kwargs,
                        session=FlaskSessionAdapter(),
                    )

                with (
                    mock.patch.object(datos_simulados, "sim", simulator),
                    mock.patch.object(
                        monitor_module,
                        "load_servers",
                        return_value=servers,
                    ),
                    mock.patch.object(
                        monitor_module,
                        "PRTGClient",
                        side_effect=client_factory,
                    ),
                ):
                    collector = monitor_module.PRTGMonitor(servers)
                    snapshot = collector.fetch()
            finally:
                sys.modules.pop("monitor", None)

            persisted = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(snapshot["collector"]["servers_configured"], 13)
        self.assertEqual(snapshot["collector"]["servers_with_error"], 0)
        self.assertEqual(len(snapshot["sensores"]), 1000)
        self.assertEqual(
            {sensor["grupo"] for sensor in snapshot["sensores"]},
            {server["name"] for server in servers},
        )
        self.assertEqual(len(persisted["sensores"]), 1000)
        self.assertEqual(
            set(snapshot),
            {"ultima_actualizacion", "sensores", "logs", "metrics", "collector"},
        )


if __name__ == "__main__":
    unittest.main()
