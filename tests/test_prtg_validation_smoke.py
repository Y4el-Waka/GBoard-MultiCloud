from __future__ import annotations

import io
import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

from scripts import prtg_metrics_smoke


class FakeResult:
    def __init__(self) -> None:
        self.start = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
        self.end = datetime(2026, 9, 26, 12, 5, tzinfo=UTC)
        self.samples = (
            SimpleNamespace(unit="bps"),
            SimpleNamespace(unit="bps"),
        )
        self.issues = (
            SimpleNamespace(
                code="invalid_value",
                message="password=must-not-leak",
            ),
        )
        self.unknown_channels = (
            SimpleNamespace(source_channel="Customer Secret Channel"),
        )

    def by_channel(self):
        return {
            "traffic_in_bps": (SimpleNamespace(unit="bps"),),
            "traffic_out_bps": (SimpleNamespace(unit="bps"),),
        }


class PRTGValidationSmokeTests(unittest.TestCase):
    def test_summary_is_allowlisted_and_never_claims_reviewed_validation(self) -> None:
        metadata = SimpleNamespace(
            identity=SimpleNamespace(
                server_id="srv_authorized_lab",
                sensor_id="2001",
            ),
            sensor_type="traffic",
        )
        source_metadata = {
            "source_sensor_type": "snmptraffic",
            "source_prtg_version": "24.3.98.1210",
            "classification_status": "supported",
            "password": "must-not-leak",
            "source_url": "https://must-not-leak.example.test",
            "channels": (
                {
                    "source_channel": "Customer Secret In",
                    "canonical_key": "traffic_in_bps",
                    "source_units": ("Mbit/s",),
                    "kinds": ("rate", "volume"),
                    "issues": ("non_rate_volume",),
                },
                {
                    "source_channel": "Customer Secret Out",
                    "canonical_key": "traffic_out_bps",
                    "source_units": ("Mbit/s", "bad;unit"),
                    "kinds": ("rate",),
                    "issues": (),
                },
            ),
        }
        history_payload = {
            "aggregation_seconds": 300,
            "password": "must-not-leak",
            "historic_csv": (
                '"Date Time";"Customer Secret In";'
                '"Customer Secret In(RAW)"\n'
                '"2026-09-26T12:00:00Z";"85 Mbit/s";"85000000"\n'
                '"2026-09-26T12:05:00Z";"86 Mbit/s";"86000000"\n'
            ),
        }

        summary = prtg_metrics_smoke.build_safe_summary(
            expected_family="traffic",
            metadata=metadata,
            result=FakeResult(),
            source_metadata=source_metadata,
            history_payload=history_payload,
        )
        serialized = json.dumps(summary, sort_keys=True)

        self.assertTrue(summary["smoke_execution_succeeded"])
        self.assertFalse(summary["validated_against_real_prtg"])
        self.assertEqual(summary["compatibility_status"], "not_tested")
        self.assertTrue(summary["evidence_review_required"])
        self.assertEqual(summary["history"]["delimiter"], "semicolon")
        self.assertEqual(summary["history"]["timestamp_mode"], "aware")
        self.assertEqual(summary["scope"]["prtg_version"], "24.3.98.1210")
        self.assertEqual(
            {
                item["key"]
                for item in summary["adapter"]["canonical_channels"]
            },
            {"traffic_in_bps", "traffic_out_bps"},
        )
        self.assertIn("redacted_unit", serialized)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("Customer Secret", serialized)
        self.assertNotIn("historic_csv", serialized)

    def test_expected_family_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            prtg_metrics_smoke._require_expected_family("cpu", "traffic")

    def test_capture_source_delegates_without_additional_requests(self) -> None:
        source = mock.Mock()
        source.fetch_sensor_metadata.return_value = {"sensor_type": "ping"}
        source.fetch_history.return_value = {"historic_csv": "fixture"}
        capture = prtg_metrics_smoke.ValidationCaptureSource(source)
        server = {"id": "srv_fixture"}

        capture.fetch_sensor_metadata(server, "1")
        capture.fetch_history(
            server,
            "1",
            points=3,
            start=None,
            end=None,
        )

        source.fetch_sensor_metadata.assert_called_once_with(server, "1")
        source.fetch_history.assert_called_once_with(
            server,
            "1",
            points=3,
            start=None,
            end=None,
        )

    def test_missing_authorization_refuses_before_loading_configuration(self) -> None:
        arguments = [
            "prtg_metrics_smoke.py",
            "--server-id",
            "srv_fixture",
            "--sensor-id",
            "1",
            "--expected-family",
            "ping",
        ]
        with (
            mock.patch("sys.argv", arguments),
            mock.patch.object(prtg_metrics_smoke, "load_environment") as loader,
            mock.patch("sys.stderr", new_callable=io.StringIO),
        ):
            exit_code = prtg_metrics_smoke.main()

        self.assertEqual(exit_code, 2)
        loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
