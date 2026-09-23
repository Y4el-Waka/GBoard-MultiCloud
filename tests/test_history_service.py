from __future__ import annotations

import ast
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from history_service import (
    HistoryServerNotFoundError,
    HistoryService,
    HistoryUpstreamError,
    HistoryValidationError,
    parse_history_csv,
)
from prtg_client import PRTGConnectionError


PING_HISTORY_CSV = (
    "Date Time;Ping Time;Minimum;Maximum;Packet Loss;Downtime;Coverage\n"
    "2026-09-23 11:55:00;10.00 ms;8.00 ms;12.00 ms;0.00 %;0.00 %;100.00 %\n"
    "2026-09-23 12:00:00;11.00 ms;9.00 ms;13.00 ms;1.00 %;0.00 %;100.00 %\n"
)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class RecordingClient:
    def __init__(self, response: str = PING_HISTORY_CSV) -> None:
        self.response = response
        self.error: Exception | None = None
        self.calls: list[dict] = []

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
        self.calls.append(
            {
                "server": server,
                "sensor_id": sensor_id,
                "start": start,
                "end": end,
                "average_seconds": average_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response


class HistoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = {
            "id": "srv_test_01",
            "type": "prtg",
            "enabled": True,
            "name": "PRTG LAB 01",
            "url": "http://127.0.0.1:19090/prtg-01",
            "username": "synthetic-user",
            "password": "synthetic-password",
        }
        self.servers = {"PRTG LAB 01": self.server}
        self.client = RecordingClient()
        self.clock = MutableClock(datetime(2026, 9, 23, 12, 0, 0))

    def service(
        self,
        *,
        servers=None,
        server_reloader=None,
        cache_ttl_seconds: int = 60,
        cache_max_entries: int = 128,
    ) -> HistoryService:
        selected_servers = self.servers if servers is None else servers
        return HistoryService(
            server_provider=lambda: selected_servers,
            server_reloader=server_reloader,
            client_factory=lambda: self.client,
            cache_ttl_seconds=cache_ttl_seconds,
            cache_max_entries=cache_max_entries,
            clock=self.clock,
        )

    def get_history(
        self,
        service: HistoryService,
        sensor_id: int = 100001,
    ) -> dict:
        return service.get_history(
            "PRTG LAB 01",
            sensor_id,
            minutes=120,
            average_seconds=300,
        )

    def test_server_lookup_reloads_once_and_reports_not_found(self) -> None:
        reload_count = 0

        def reload_servers():
            nonlocal reload_count
            reload_count += 1
            return self.servers

        service = self.service(
            servers={},
            server_reloader=reload_servers,
        )

        self.get_history(service)
        self.assertEqual(reload_count, 1)
        self.assertIs(self.client.calls[0]["server"], self.server)

        missing = self.service(servers={}, server_reloader=lambda: {})
        with self.assertRaisesRegex(
            HistoryServerNotFoundError,
            "Grupo 'DESCONOCIDO' no encontrado",
        ):
            missing.get_history(
                "DESCONOCIDO",
                100001,
                minutes=120,
                average_seconds=300,
            )

    def test_request_to_prtg_uses_expected_server_range_and_average(self) -> None:
        service = self.service()
        end = datetime(2026, 9, 23, 12, 0, 0)

        payload = service.get_history(
            "PRTG LAB 01",
            100001,
            minutes=120,
            average_seconds=300,
            end=end,
        )

        call = self.client.calls[0]
        self.assertIs(call["server"], self.server)
        self.assertEqual(call["sensor_id"], 100001)
        self.assertEqual(call["start"], datetime(2026, 9, 23, 10, 0, 0))
        self.assertEqual(call["end"], end)
        self.assertEqual(call["average_seconds"], 300)
        self.assertEqual(payload["unit"], "ms")
        self.assertEqual(payload["count"], 2)

    def test_parser_preserves_current_ping_dataset_contract(self) -> None:
        payload = parse_history_csv(PING_HISTORY_CSV)

        expected_points = {
            "ping": [10.0, 11.0],
            "minimum": [8.0, 9.0],
            "maximum": [12.0, 13.0],
            "packet_loss": [0.0, 1.0],
            "downtime": [0.0, 0.0],
            "coverage": [100.0, 100.0],
        }
        expected_metadata = [
            ("ping", "Tiempo de Ping", "ms", "y", "Ping Time"),
            ("minimum", "Mínimo", "ms", "y", "Minimum"),
            ("maximum", "Máximo", "ms", "y", "Maximum"),
            ("packet_loss", "Pérdida de paquetes", "%", "y1", "Packet Loss"),
            ("downtime", "Tiempo de inactividad", "%", "y1", "Downtime"),
            ("coverage", "Cobertura", "%", "y1", "Coverage"),
        ]

        self.assertEqual(
            [
                (
                    dataset["key"],
                    dataset["label"],
                    dataset["unit"],
                    dataset["axis"],
                    dataset["column"],
                )
                for dataset in payload["datasets"]
            ],
            expected_metadata,
        )
        for dataset in payload["datasets"]:
            self.assertEqual(
                [point["value"] for point in dataset["data"]],
                expected_points[dataset["key"]],
            )
            self.assertEqual(
                [point["t"] for point in dataset["data"]],
                ["2026-09-23T11:55:00", "2026-09-23T12:00:00"],
            )

        self.assertEqual(payload["series"], payload["datasets"][0]["data"])
        self.assertEqual(payload["unit"], "ms")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["column"], "Ping Time")
        self.assertEqual(
            payload["header"],
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
        self.assertEqual(payload["error"], "")

    def test_invalid_source_preserves_parser_error_payload(self) -> None:
        empty = parse_history_csv("")
        html = parse_history_csv("<!doctype html><title>PRTG</title>")

        self.assertEqual(empty["series"], [])
        self.assertEqual(empty["count"], 0)
        self.assertEqual(empty["error"], "Respuesta historica vacia")
        self.assertEqual(html["series"], [])
        self.assertIn("HTML", html["error"])

    def test_cache_miss_then_hit_avoids_second_upstream_request(self) -> None:
        service = self.service()

        first = self.get_history(service)
        second = self.get_history(service)

        self.assertIs(first, second)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(service.cache_size(), 1)

    def test_cache_entry_expires_after_ttl(self) -> None:
        service = self.service(cache_ttl_seconds=60)

        self.get_history(service)
        self.clock.advance(seconds=61)
        self.get_history(service)

        self.assertEqual(len(self.client.calls), 2)
        self.assertEqual(service.cache_size(), 1)

    def test_lru_evicts_least_recently_used_entry(self) -> None:
        service = self.service(cache_max_entries=2)

        self.get_history(service, 100001)
        self.get_history(service, 100002)
        self.get_history(service, 100001)
        self.get_history(service, 100003)
        self.get_history(service, 100002)

        self.assertEqual(len(self.client.calls), 4)
        self.assertEqual(service.cache_size(), 2)

    def test_sensor_and_full_cache_invalidation_are_public(self) -> None:
        service = self.service()

        self.get_history(service, 100001)
        self.get_history(service, 100002)
        service.invalidate_sensor("PRTG LAB 01", 100001)
        self.get_history(service, 100001)
        self.get_history(service, 100002)

        self.assertEqual(len(self.client.calls), 3)
        self.assertEqual(service.cache_size(), 2)

        service.clear()
        self.assertEqual(service.cache_size(), 0)

    def test_invalid_historic_ranges_are_rejected(self) -> None:
        service = self.service()

        invalid_ranges = (
            (4, 300),
            (1441, 300),
            (120, 59),
            (120, 3601),
            (True, 300),
        )

        for minutes, average_seconds in invalid_ranges:
            with self.subTest(
                minutes=minutes,
                average_seconds=average_seconds,
            ):
                with self.assertRaises(HistoryValidationError):
                    service.get_history(
                        "PRTG LAB 01",
                        100001,
                        minutes=minutes,
                        average_seconds=average_seconds,
                    )

        self.assertEqual(self.client.calls, [])

    def test_upstream_client_error_is_mapped_to_history_error(self) -> None:
        self.client.error = PRTGConnectionError(
            "Synthetic upstream connection failure"
        )
        service = self.service()

        with self.assertRaisesRegex(
            HistoryUpstreamError,
            "No se pudo consultar PRTG",
        ):
            self.get_history(service)

        self.assertEqual(service.cache_size(), 0)

    def test_module_has_no_flask_import(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "history_service.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        self.assertNotIn("flask", imported_roots)


if __name__ == "__main__":
    unittest.main()
