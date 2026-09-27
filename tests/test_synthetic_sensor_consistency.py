from __future__ import annotations

import unittest

import datos_simulados
from metrics.prtg_adapter import PRTGMetricAdapter
from simulator_multichannel import SyntheticMultichannelSimulator
from synthetic_sensor_catalog import (
    SyntheticSensorCatalog,
    SyntheticSensorCatalogEntry,
)


EXPECTED_CHANNELS = {
    "ping": {"latency_ms", "packet_loss_pct", "availability_pct"},
    "cpu": {"cpu_usage_pct"},
    "memory": {"memory_usage_pct", "memory_used_bytes"},
    "traffic": {"traffic_in_bps", "traffic_out_bps", "utilization_pct"},
    "sfp": {
        "rx_power_dbm",
        "tx_power_dbm",
        "temperature_c",
        "voltage_v",
        "bias_current_ma",
    },
}

EXPECTED_LEGACY_NAMES = {
    "ping": {"Ping"},
    "cpu": {"CPU Load"},
    "memory": {"Memory"},
    "traffic": {"Traffic In", "Traffic Out"},
    "sfp": {"SFP Optics"},
}


class SyntheticSensorConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.simulator = datos_simulados.Simulator(13, 1000, 20260923)
        cls.adapter = PRTGMetricAdapter()

    def test_all_1000_sensors_share_identity_family_name_value_and_history(self) -> None:
        checked = 0
        seen_identities: set[tuple[str, int]] = set()

        for slug, server in self.simulator.servers.items():
            for sensor_id, legacy_sensor in server.sensors.items():
                with self.subTest(server=server.server_id, sensor=sensor_id):
                    entry = self.simulator.catalog.get(server.server_id, sensor_id)
                    identity = (server.server_id, sensor_id)
                    self.assertNotIn(identity, seen_identities)
                    seen_identities.add(identity)

                    self.assertIs(legacy_sensor.catalog_entry, entry)
                    self.assertEqual(entry.server_slug, slug)
                    self.assertEqual(legacy_sensor.objid, entry.sensor_id)
                    self.assertIn(
                        legacy_sensor.name.removeprefix("MF-"),
                        EXPECTED_LEGACY_NAMES[entry.family],
                    )

                    current = self.simulator.multichannel.sensor_source(
                        slug,
                        sensor_id,
                    )
                    self.assertEqual(current["server_id"], server.server_id)
                    self.assertEqual(current["objid"], sensor_id)
                    self.assertEqual(current["sensor_type"], entry.family)
                    self.assertEqual(current["sensor"], legacy_sensor.name)

                    primary = next(
                        channel["value"]
                        for channel in current["channels"]
                        if channel["channel"] == entry.primary_source_channel
                    )
                    self.assertEqual(legacy_sensor.lastvalue, primary)

                    history = self.simulator.multichannel.historic_source(
                        slug,
                        sensor_id,
                        points=2,
                    )
                    self.assertEqual(history["server_id"], server.server_id)
                    self.assertEqual(history["sensor_id"], sensor_id)
                    self.assertEqual(history["sensor_type"], entry.family)
                    for sample in history["samples"]:
                        self.assertEqual(sample["server_id"], server.server_id)
                        self.assertEqual(sample["objid"], sensor_id)
                        self.assertEqual(sample["sensor_type"], entry.family)
                        adapted = self.adapter.adapt_sensor(
                            sample,
                            server_id=server.server_id,
                            sensor_type=entry.family,
                        )
                        keys = {item.channel_key for item in adapted.samples}
                        self.assertTrue(keys)
                        self.assertTrue(keys.issubset(EXPECTED_CHANNELS[entry.family]))
                        self.assertFalse(adapted.issues)
                        self.assertFalse(adapted.unknown_channels)
                    checked += 1

        self.assertEqual(checked, 1000)
        self.assertEqual(len(seen_identities), 1000)
        self.assertEqual(len(self.simulator.catalog), 1000)

    def test_reported_ping_memory_and_cpu_traffic_mismatches_are_impossible(self) -> None:
        ping = next(
            entry
            for entry in self.simulator.catalog.entries()
            if entry.family == "ping"
        )
        cpu = next(
            entry
            for entry in self.simulator.catalog.entries()
            if entry.family == "cpu"
        )

        ping_source = self.simulator.multichannel.sensor_source(
            ping.server_slug,
            ping.sensor_id,
        )
        cpu_source = self.simulator.multichannel.sensor_source(
            cpu.server_slug,
            cpu.sensor_id,
        )
        ping_keys = {
            sample.channel_key
            for sample in self.adapter.adapt_sensor(
                ping_source,
                server_id=ping.server_id,
                sensor_type=ping.family,
            ).samples
        }
        cpu_keys = {
            sample.channel_key
            for sample in self.adapter.adapt_sensor(
                cpu_source,
                server_id=cpu.server_id,
                sensor_type=cpu.family,
            ).samples
        }

        self.assertEqual(ping_source["sensor"].removeprefix("MF-"), "Ping")
        self.assertEqual(ping_source["sensor_type"], "ping")
        self.assertEqual(
            ping_keys,
            {"latency_ms", "packet_loss_pct", "availability_pct"},
        )
        self.assertNotIn("memory_usage_pct", ping_keys)

        self.assertEqual(cpu_source["sensor"].removeprefix("MF-"), "CPU Load")
        self.assertEqual(cpu_source["sensor_type"], "cpu")
        self.assertEqual(cpu_keys, {"cpu_usage_pct"})
        self.assertNotIn("traffic_in_bps", cpu_keys)
        self.assertNotIn("traffic_out_bps", cpu_keys)

    def test_catalog_is_deterministic_and_preserves_distribution(self) -> None:
        same = datos_simulados.Simulator(13, 1000, 20260923)
        other_seed = datos_simulados.Simulator(13, 1000, 20260924)

        baseline_entries = self.simulator.catalog.entries()
        self.assertEqual(baseline_entries, same.catalog.entries())
        self.assertEqual(baseline_entries, other_seed.catalog.entries())
        self.assertEqual(
            self.simulator.catalog.family_counts(),
            {
                "ping": 400,
                "cpu": 200,
                "memory": 100,
                "traffic": 200,
                "sfp": 100,
            },
        )

        sample = baseline_entries[7]
        first_channels = self.simulator.multichannel.sensor_source(
            sample.server_slug,
            sample.sensor_id,
        )["channels"]
        same_channels = same.multichannel.sensor_source(
            sample.server_slug,
            sample.sensor_id,
        )["channels"]
        changed_channels = other_seed.multichannel.sensor_source(
            sample.server_slug,
            sample.sensor_id,
        )["channels"]
        self.assertEqual(first_channels, same_channels)
        self.assertNotEqual(first_channels, changed_channels)

    def test_same_numeric_sensor_id_on_two_servers_is_isolated(self) -> None:
        catalog = SyntheticSensorCatalog()
        ping_entry = SyntheticSensorCatalogEntry(
            server_id="srv_lab_a",
            server_slug="prtg-a",
            sensor_id=42,
            family="ping",
            legacy_name="Ping",
            primary_source_channel="Ping Time",
        )
        traffic_entry = SyntheticSensorCatalogEntry(
            server_id="srv_lab_b",
            server_slug="prtg-b",
            sensor_id=42,
            family="traffic",
            legacy_name="Traffic In",
            primary_source_channel="Traffic In",
        )
        catalog.add(ping_entry)
        catalog.add(traffic_entry)

        def server(entry: SyntheticSensorCatalogEntry):
            sensor = datos_simulados.SensorState(
                objid=42,
                parentid=1,
                device=f"DEVICE-{entry.server_slug}",
                catalog_entry=entry,
                is_mf=False,
                status_raw=3,
                lastvalue="",
            )
            return datos_simulados.SimulatedPRTG(
                slug=entry.server_slug,
                server_id=entry.server_id,
                name=entry.server_id,
                devices={1: "192.0.2.1"},
                device_names={1: sensor.device},
                sensors={42: sensor},
            )

        multichannel = SyntheticMultichannelSimulator(
            {
                "prtg-a": server(ping_entry),
                "prtg-b": server(traffic_entry),
            },
            catalog=catalog,
            seed=20260923,
        )

        ping = multichannel.sensor_source("prtg-a", 42)
        traffic = multichannel.sensor_source("prtg-b", 42)
        self.assertEqual((ping["server_id"], ping["objid"]), ("srv_lab_a", 42))
        self.assertEqual(ping["sensor_type"], "ping")
        self.assertEqual(
            (traffic["server_id"], traffic["objid"]),
            ("srv_lab_b", 42),
        )
        self.assertEqual(traffic["sensor_type"], "traffic")
        self.assertEqual(catalog.get("srv_lab_a", 42), ping_entry)
        self.assertEqual(catalog.get("srv_lab_b", 42), traffic_entry)


if __name__ == "__main__":
    unittest.main()
