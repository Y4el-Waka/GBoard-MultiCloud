from __future__ import annotations

import ast
import json
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from prtg_client import PRTGConnectionError
from prtg_discovery import (
    PRTGDiscoveryNotFoundError,
    PRTGDiscoveryUpstreamError,
    PRTGDiscoveryValidationError,
    PRTGSensorDiscovery,
    PRTGSensorFamily,
    PRTGSourceValueKind,
    characterize_historic_csv,
    classify_sensor,
    parse_channel_csv,
    parse_sensor_metadata,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "prtg_discovery"


class FixtureClient:
    def __init__(self, sensor_payload, channel_csv, historic_csv="history") -> None:
        self.sensor_payload = sensor_payload
        self.channel_csv = channel_csv
        self.historic_csv = historic_csv
        self.calls: list[tuple[str, object]] = []
        self.error: Exception | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def get_sensor_metadata(self, server, sensor_id):
        self.calls.append(("sensor", sensor_id))
        if self.error:
            raise self.error
        return self.sensor_payload

    def get_channels_csv(self, server, sensor_id):
        self.calls.append(("channels", sensor_id))
        if self.error:
            raise self.error
        return self.channel_csv

    def get_historic_csv(
        self,
        server,
        sensor_id,
        start,
        end,
        *,
        average_seconds,
        allow_redirects,
    ):
        self.calls.append(
            (
                "history",
                sensor_id,
                start,
                end,
                average_seconds,
                allow_redirects,
            )
        )
        if self.error:
            raise self.error
        return self.historic_csv


class PRTGDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(
            (FIXTURES / "manifest.json").read_text(encoding="utf-8")
        )
        cls.server = {
            **cls.manifest["server"],
            "url": "https://prtg-lab.example.test",
            "username": "fixture-user",
            "passhash": "fixture-passhash",
        }

    def case(self, family: str):
        case = self.manifest["cases"][family]
        channel_csv = (FIXTURES / case["channels"]).read_text(encoding="utf-8")
        history_csv = (
            (FIXTURES / case["history"]).read_text(encoding="utf-8")
            if "history" in case
            else ""
        )
        return case["sensor_payload"], channel_csv, history_csv

    def discover(self, family: str):
        sensor_payload, channel_csv, history_csv = self.case(family)
        client = FixtureClient(sensor_payload, channel_csv, history_csv)
        sensor_id = sensor_payload["sensors"][0]["objid"]
        result = PRTGSensorDiscovery(
            client_factory=lambda: client
        ).discover_sensor(self.server, sensor_id)
        return result, client

    def test_fixture_provenance_does_not_claim_real_prtg_validation(self) -> None:
        self.assertEqual(
            self.manifest["evidence"],
            "sanitized_local_fixture",
        )
        self.assertFalse(self.manifest["validated_against_real_prtg"])

    def test_compound_identity_uses_configured_server_and_sensor_ids(self) -> None:
        payload, _, _ = self.case("ping")
        first = parse_sensor_metadata(
            payload,
            server_id="srv_discovery_a",
            sensor_id="210001",
        )
        second = parse_sensor_metadata(
            payload,
            server_id="srv_discovery_b",
            sensor_id="210001",
        )

        self.assertNotEqual(first.identity, second.identity)
        self.assertEqual(first.identity.sensor_id, second.identity.sensor_id)
        self.assertEqual(first.device_id, "310001")
        self.assertNotEqual(first.group_name, first.identity.server_id)

    def test_sensor_discovery_returns_metadata_and_channels_without_history(self) -> None:
        result, client = self.discover("ping")

        self.assertEqual(result.sensor.identity.server_id, "srv_discovery_lab_01")
        self.assertEqual(result.sensor.identity.sensor_id, "210001")
        self.assertEqual(result.sensor.prtg_sensor_type, "ping")
        self.assertEqual(result.sensor.declared_channel_count, 3)
        self.assertEqual(
            [channel.canonical_key for channel in result.channels],
            ["latency_ms", "packet_loss_pct", "availability_pct"],
        )
        self.assertEqual(client.calls, [("sensor", "210001"), ("channels", "210001")])
        self.assertEqual(result.historic.sensor_id, "210001")

    def test_classifies_all_supported_families_from_type_and_channels(self) -> None:
        for family in ("ping", "cpu", "memory", "traffic", "sfp"):
            with self.subTest(family=family):
                result, _ = self.discover(family)
                self.assertEqual(result.classification.family.value, family)
                self.assertIn("channel", result.classification.reason)

    def test_visible_names_do_not_override_explicit_evidence(self) -> None:
        ping_payload, ping_channels, _ = self.case("ping")
        ping_payload = deepcopy(ping_payload)
        ping_payload["sensors"][0]["sensor"] = "Memory"
        ping = classify_sensor(
            parse_sensor_metadata(
                ping_payload,
                server_id="srv_discovery_lab_01",
                sensor_id="210001",
            ),
            parse_channel_csv(ping_channels),
        )

        cpu_payload, cpu_channels, _ = self.case("cpu")
        cpu_payload = deepcopy(cpu_payload)
        cpu_payload["sensors"][0]["sensor"] = "Traffic In"
        cpu = classify_sensor(
            parse_sensor_metadata(
                cpu_payload,
                server_id="srv_discovery_lab_01",
                sensor_id="210002",
            ),
            parse_channel_csv(cpu_channels),
        )

        self.assertEqual(ping.family, PRTGSensorFamily.PING)
        self.assertEqual(cpu.family, PRTGSensorFamily.CPU)

    def test_sfp_uses_channel_signature_when_prtg_type_is_generic(self) -> None:
        result, _ = self.discover("sfp")
        self.assertEqual(result.sensor.prtg_sensor_type, "snmpcustomadvanced")
        self.assertEqual(result.classification.family, PRTGSensorFamily.SFP)
        self.assertEqual(
            {channel.source_channel_id for channel in result.channels},
            {"1", "2", "3", "4", "5"},
        )

    def test_unknown_and_unsupported_are_not_coerced_to_ping(self) -> None:
        unknown, _ = self.discover("unknown")
        unsupported, _ = self.discover("unsupported")

        self.assertEqual(
            unknown.classification.family,
            PRTGSensorFamily.UNKNOWN,
        )
        self.assertEqual(
            unsupported.classification.family,
            PRTGSensorFamily.UNSUPPORTED,
        )
        self.assertNotEqual(unknown.classification.family, PRTGSensorFamily.PING)

    def test_unknown_channel_remains_observable(self) -> None:
        _, channel_csv, _ = self.case("unknown")
        channel = parse_channel_csv(channel_csv)[0]

        self.assertIsNone(channel.canonical_key)
        self.assertIn("unknown_channel", channel.issues)
        self.assertEqual(channel.name, "Synthetic Health Index")

    def test_traffic_distinguishes_volume_from_speed(self) -> None:
        _, channel_csv, _ = self.case("traffic")
        channels = {
            channel.canonical_key: channel
            for channel in parse_channel_csv(channel_csv)
        }
        inbound = channels["traffic_in_bps"]

        self.assertEqual(
            [value.kind for value in inbound.values],
            [PRTGSourceValueKind.VOLUME, PRTGSourceValueKind.RATE],
        )
        self.assertEqual(
            [value.source_unit for value in inbound.values],
            ["MByte", "Mbit/s"],
        )
        self.assertFalse(inbound.values[0].compatible_with_canonical)
        self.assertTrue(inbound.values[1].compatible_with_canonical)
        self.assertIn("non_rate_volume", inbound.issues)

    def test_volume_only_does_not_prove_a_traffic_rate_signature(self) -> None:
        payload, channel_csv, _ = self.case("traffic")
        volume_only = "\n".join(
            line.replace(
                ';"Last Value (speed)";"Last Value (speed)(RAW)"',
                "",
            )
            for line in channel_csv.splitlines()
        )
        volume_only = "\n".join(
            ";".join(line.split(";")[:4])
            for line in volume_only.splitlines()
        )
        metadata = parse_sensor_metadata(
            payload,
            server_id="srv_discovery_lab_01",
            sensor_id="210004",
        )
        classification = classify_sensor(
            metadata,
            parse_channel_csv(volume_only),
        )

        self.assertEqual(classification.family, PRTGSensorFamily.UNKNOWN)

    def test_incompatible_and_unknown_units_are_explicit(self) -> None:
        incompatible = (
            '"Channel";"Last Value"\n'
            '"CPU Load";"-3.2 dBm"\n'
        )
        unknown = (
            '"Channel";"Last Value"\n'
            '"CPU Load";"42 widgets"\n'
        )

        incompatible_channel = parse_channel_csv(incompatible)[0]
        unknown_channel = parse_channel_csv(unknown)[0]
        self.assertIn("incompatible_unit", incompatible_channel.issues)
        self.assertIn("unknown_unit", unknown_channel.issues)
        self.assertFalse(
            incompatible_channel.values[0].compatible_with_canonical
        )

    def test_historic_fixtures_characterize_headers_empty_values_and_timezones(self) -> None:
        for family in ("ping", "cpu", "memory", "traffic", "sfp"):
            with self.subTest(family=family):
                _, _, history = self.case(family)
                result = characterize_historic_csv(
                    history,
                    average_seconds=300,
                )
                self.assertEqual(result.delimiter, ";")
                self.assertEqual(result.timestamp_header, "Date Time")
                self.assertEqual(result.row_count, 2)
                self.assertIn(
                    result.timestamp_mode,
                    {"naive", "aware"},
                )
                self.assertTrue(result.channel_headers)
                self.assertEqual(result.average_seconds, 300)

        ping = characterize_historic_csv(
            self.case("ping")[2],
            average_seconds=300,
        )
        sfp = characterize_historic_csv(
            self.case("sfp")[2],
            average_seconds=300,
        )
        self.assertEqual(ping.timestamp_mode, "naive")
        self.assertEqual(ping.empty_value_count, 1)
        self.assertEqual(ping.invalid_timestamp_count, 0)
        self.assertIn(("Ping Time", ("ms",)), ping.channel_units)
        self.assertEqual(sfp.timestamp_mode, "aware")
        self.assertIn(("RX Power", ("dBm",)), sfp.channel_units)

    def test_historic_characterization_reports_invalid_timestamps(self) -> None:
        result = characterize_historic_csv(
            '"Date Time";"CPU Load"\n"not-a-date";"42 %"\n',
            average_seconds=60,
        )

        self.assertEqual(result.timestamp_mode, "invalid")
        self.assertEqual(result.invalid_timestamp_count, 1)
        self.assertEqual(result.channel_units, (("CPU Load", ("%",)),))

    def test_on_demand_history_is_read_only_and_disables_redirects(self) -> None:
        payload, channel_csv, history = self.case("ping")
        client = FixtureClient(payload, channel_csv, history)
        discovery = PRTGSensorDiscovery(client_factory=lambda: client)

        returned = discovery.fetch_historic_csv(
            self.server,
            210001,
            datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
            datetime(2026, 9, 26, 12, 5, tzinfo=UTC),
            average_seconds=300,
        )

        self.assertEqual(returned, history)
        self.assertEqual(client.calls[0][0], "history")
        self.assertFalse(client.calls[0][-1])

    def test_invalid_identity_and_missing_sensor_are_typed(self) -> None:
        payload, channel_csv, _ = self.case("ping")
        discovery = PRTGSensorDiscovery(
            client_factory=lambda: FixtureClient(payload, channel_csv)
        )
        with self.assertRaises(PRTGDiscoveryValidationError):
            discovery.discover_sensor({**self.server, "id": ""}, 210001)
        with self.assertRaises(PRTGDiscoveryValidationError):
            discovery.discover_sensor(self.server, "not-an-id")
        with self.assertRaises(PRTGDiscoveryNotFoundError):
            parse_sensor_metadata(
                {"sensors": []},
                server_id="srv_discovery_lab_01",
                sensor_id="210001",
            )

    def test_upstream_failures_and_logs_do_not_expose_secrets(self) -> None:
        payload, channel_csv, _ = self.case("ping")
        client = FixtureClient(payload, channel_csv)
        client.error = PRTGConnectionError(
            "password=do-not-leak passhash=do-not-leak"
        )
        logger = mock.Mock()
        discovery = PRTGSensorDiscovery(
            client_factory=lambda: client,
            logger=logger,
        )

        with self.assertRaises(PRTGDiscoveryUpstreamError) as captured:
            discovery.discover_sensor(
                {
                    **self.server,
                    "password": "fixture-password-do-not-leak",
                    "passhash": "fixture-passhash-do-not-leak",
                },
                210001,
            )

        emitted = repr(logger.warning.call_args_list)
        combined = f"{captured.exception} {emitted}"
        self.assertNotIn("fixture-password-do-not-leak", combined)
        self.assertNotIn("fixture-passhash-do-not-leak", combined)
        self.assertNotIn("password=do-not-leak", combined)

    def test_discovery_is_additive_and_does_not_depend_on_synthetic_catalog(self) -> None:
        tree = ast.parse(
            (ROOT / "prtg_discovery.py").read_text(encoding="utf-8")
        )
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertNotIn("synthetic_sensor_catalog", imports)
        self.assertNotIn("flask", imports)
        self.assertNotIn("sqlite3", imports)
        self.assertNotIn("requests", imports)

        for production_file in ("app.py", "monitor.py", "history_service.py"):
            production_tree = ast.parse(
                (ROOT / production_file).read_text(encoding="utf-8")
            )
            imported_modules = {
                node.module
                for node in ast.walk(production_tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            self.assertNotIn("prtg_discovery", imported_modules)


if __name__ == "__main__":
    unittest.main()
