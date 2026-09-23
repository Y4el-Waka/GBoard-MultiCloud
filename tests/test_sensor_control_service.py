from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest import mock

from prtg_client import PRTGClientError
from sensor_control_service import (
    SensorControlService,
    SensorControlUpstreamError,
    SensorControlValidationError,
    SensorServerNotFoundError,
)


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.failing_operation: str | None = None
        self.failure_message = "synthetic upstream failure"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def _record(self, operation: str, *args) -> None:
        self.calls.append((operation, *args))
        if self.failing_operation == operation:
            raise PRTGClientError(self.failure_message)

    def pause_for(self, server, sensor_id, minutes, message) -> None:
        self._record("pause_for", server, sensor_id, minutes, message)

    def pause_indefinitely(self, server, sensor_id, message) -> None:
        self._record("pause_indefinitely", server, sensor_id, message)

    def resume(self, server, sensor_id) -> None:
        self._record("resume", server, sensor_id)


class SensorControlServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = {
            "id": "srv_test_01",
            "type": "prtg",
            "enabled": True,
            "name": "PRTG LAB 01",
            "url": "https://prtg-01.example.test",
            "username": "synthetic-user",
            "password": "synthetic-password",
        }
        self.servers = {"PRTG LAB 01": self.server}
        self.client = RecordingClient()
        self.history_service = mock.Mock()
        self.logger = mock.Mock()

    def service(
        self,
        *,
        servers=None,
        server_reloader=None,
    ) -> SensorControlService:
        selected_servers = self.servers if servers is None else servers
        return SensorControlService(
            server_provider=lambda: selected_servers,
            server_reloader=server_reloader,
            client_factory=lambda: self.client,
            history_service=self.history_service,
            logger=self.logger,
        )

    def test_pause_for_uses_server_and_parameters_then_invalidates_history(self) -> None:
        service = self.service()

        effective_minutes = service.pause_for(
            "PRTG LAB 01",
            100001,
            60,
            "Synthetic timed pause",
        )

        self.assertEqual(effective_minutes, 60)
        self.assertEqual(
            self.client.calls,
            [
                (
                    "pause_for",
                    self.server,
                    100001,
                    60,
                    "Synthetic timed pause",
                )
            ],
        )
        self.history_service.clear.assert_called_once_with()

    def test_pause_minutes_preserve_clamping_and_validation(self) -> None:
        service = self.service()

        low = service.pause_for("PRTG LAB 01", 100001, 0, "Low")
        high = service.pause_for(
            "PRTG LAB 01",
            100001,
            999999,
            "High",
        )

        self.assertEqual(low, 1)
        self.assertEqual(high, 365 * 24 * 60)
        self.assertEqual(self.client.calls[0][3], 1)
        self.assertEqual(self.client.calls[1][3], 365 * 24 * 60)

        with self.assertRaisesRegex(
            SensorControlValidationError,
            "duration_minutes debe ser numerico",
        ):
            service.pause_for("PRTG LAB 01", 100001, "invalid", "Bad")

        self.assertEqual(self.history_service.clear.call_count, 2)

    def test_pause_indefinitely_uses_expected_parameters_and_invalidates(self) -> None:
        service = self.service()

        service.pause_indefinitely(
            "PRTG LAB 01",
            100001,
            "Synthetic indefinite pause",
        )

        self.assertEqual(
            self.client.calls,
            [
                (
                    "pause_indefinitely",
                    self.server,
                    100001,
                    "Synthetic indefinite pause",
                )
            ],
        )
        self.history_service.clear.assert_called_once_with()

    def test_resume_uses_expected_parameters_and_invalidates(self) -> None:
        service = self.service()

        service.resume("PRTG LAB 01", 100001)

        self.assertEqual(
            self.client.calls,
            [("resume", self.server, 100001)],
        )
        self.history_service.clear.assert_called_once_with()

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
        service.resume("PRTG LAB 01", 100001)

        self.assertEqual(reload_count, 1)
        self.assertIs(self.client.calls[0][1], self.server)

        missing = self.service(servers={}, server_reloader=lambda: {})
        with self.assertRaisesRegex(
            SensorServerNotFoundError,
            "Grupo 'DESCONOCIDO' no encontrado",
        ):
            missing.resume("DESCONOCIDO", 100001)

    def test_invalid_target_is_rejected_before_client_or_invalidation(self) -> None:
        service = self.service()

        invalid_targets = (
            ("PRTG LAB 01", "not-a-number", "id debe ser numerico"),
            ("", 100001, "group e id son obligatorios"),
            ("PRTG LAB 01", 0, "group e id son obligatorios"),
        )

        for group, sensor_id, message in invalid_targets:
            with self.subTest(group=group, sensor_id=sensor_id):
                with self.assertRaisesRegex(
                    SensorControlValidationError,
                    message,
                ):
                    service.resume(group, sensor_id)

        self.assertEqual(self.client.calls, [])
        self.history_service.clear.assert_not_called()

    def test_upstream_errors_do_not_invalidate_history(self) -> None:
        operations = (
            (
                "pause_for",
                lambda service: service.pause_for(
                    "PRTG LAB 01",
                    100001,
                    60,
                    "Synthetic pause",
                ),
            ),
            (
                "pause_indefinitely",
                lambda service: service.pause_indefinitely(
                    "PRTG LAB 01",
                    100001,
                    "Synthetic indefinite pause",
                ),
            ),
            (
                "resume",
                lambda service: service.resume("PRTG LAB 01", 100001),
            ),
        )

        for operation, invoke in operations:
            with self.subTest(operation=operation):
                self.client.failing_operation = operation
                self.history_service.reset_mock()

                with self.assertRaises(SensorControlUpstreamError):
                    invoke(self.service())

                self.history_service.clear.assert_not_called()

    def test_upstream_errors_and_logs_do_not_expose_secrets(self) -> None:
        self.client.failing_operation = "pause_for"
        self.client.failure_message = (
            "password=do-not-leak-password&passhash=do-not-leak-passhash"
        )
        service = self.service()

        with self.assertRaises(SensorControlUpstreamError) as captured:
            service.pause_for(
                "PRTG LAB 01",
                100001,
                60,
                "Synthetic pause",
            )

        emitted = " ".join(
            str(value)
            for call in self.logger.warning.call_args_list
            for value in (*call.args, *call.kwargs.values())
        )
        combined = f"{captured.exception} {emitted}"
        self.assertNotIn("do-not-leak-password", combined)
        self.assertNotIn("do-not-leak-passhash", combined)
        self.assertNotIn("synthetic-password", combined)

    def test_module_has_no_flask_or_requests_import(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "sensor_control_service.py"
        )
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
        self.assertNotIn("requests", imported_roots)


if __name__ == "__main__":
    unittest.main()
