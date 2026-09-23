from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

import datos_simulados
from metrics.prtg_adapter import AdapterIssueCode, PRTGMetricAdapter
from simulator_multichannel import SUPPORTED_SCENARIOS


class MultichannelSimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start_time = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        self.simulator = datos_simulados.Simulator(
            server_count=2,
            sensor_count=100,
            seed=20260923,
            tick_seconds=300,
            start_time=self.start_time,
        )
        self.adapter = PRTGMetricAdapter()
        self.slug = "prtg-01"

    def sensor_id(self, family: str, index: int = 0) -> int:
        return self.simulator.multichannel.sensor_ids(self.slug, family)[index]

    def source(
        self,
        family: str,
        *,
        tick: int | None = None,
        scenario: str | None = None,
        fault: str | None = None,
        index: int = 0,
    ) -> dict:
        return self.simulator.multichannel.sensor_source(
            self.slug,
            self.sensor_id(family, index),
            tick=tick,
            scenario=scenario,
            fault=fault,
        )

    def adapt(self, source: dict):
        return self.adapter.adapt_sensor(
            source,
            server_id=source["server_id"],
            sensor_type=source["sensor_type"],
        )

    def canonical_value(
        self,
        family: str,
        channel_key: str,
        *,
        tick: int,
        scenario: str = "normal",
    ) -> float:
        result = self.adapt(self.source(family, tick=tick, scenario=scenario))
        return next(
            sample.value
            for sample in result.samples
            if sample.channel_key == channel_key
        )

    def test_same_seed_reproduces_profiles_values_and_time(self) -> None:
        other = datos_simulados.Simulator(
            server_count=2,
            sensor_count=100,
            seed=20260923,
            tick_seconds=300,
            start_time=self.start_time,
        )

        self.simulator.tick(7)
        other.tick(7)
        first_id = self.sensor_id("traffic")

        self.assertEqual(
            self.simulator.multichannel.family_counts(),
            other.multichannel.family_counts(),
        )
        self.assertEqual(
            self.simulator.multichannel_sensor(self.slug, first_id),
            other.multichannel_sensor(self.slug, first_id),
        )
        self.assertEqual(
            self.simulator.multichannel.current_time,
            other.multichannel.current_time,
        )

    def test_different_seed_changes_values_but_not_ids_or_family_assignment(self) -> None:
        other = datos_simulados.Simulator(
            server_count=2,
            sensor_count=100,
            seed=20260924,
            start_time=self.start_time,
        )
        sensor_id = self.sensor_id("ping")

        self.assertEqual(
            self.simulator.multichannel.sensor_ids(self.slug),
            other.multichannel.sensor_ids(self.slug),
        )
        self.assertEqual(
            self.simulator.multichannel.family_counts(),
            other.multichannel.family_counts(),
        )
        self.assertNotEqual(
            self.simulator.multichannel_sensor(self.slug, sensor_id)["channels"],
            other.multichannel_sensor(self.slug, sensor_id)["channels"],
        )

    def test_family_distribution_is_deterministic_and_ping_is_majority(self) -> None:
        full = datos_simulados.Simulator(13, 1000, 20260923)

        self.assertEqual(
            full.multichannel.family_counts(),
            {
                "ping": 400,
                "cpu": 200,
                "memory": 100,
                "traffic": 200,
                "sfp": 100,
            },
        )
        self.assertEqual(sum(full.multichannel.family_counts().values()), 1000)

    def test_seed_is_configurable_from_environment(self) -> None:
        with mock.patch.dict(os.environ, {"SIMULATOR_SEED": "424242"}):
            configured = datos_simulados._environment_int(
                "SIMULATOR_SEED",
                20260923,
            )
        self.assertEqual(configured, 424242)

    def test_logical_clock_advances_without_sleeping(self) -> None:
        self.assertEqual(self.simulator.multichannel.current_tick, 0)
        self.assertEqual(self.simulator.multichannel.current_time, self.start_time)

        emitted = self.simulator.tick(3)

        self.assertEqual(self.simulator.multichannel.current_tick, 3)
        self.assertEqual(emitted, self.start_time + timedelta(minutes=15))
        source = self.source("ping")
        self.assertEqual(source["timestamp"], "2026-09-23T12:15:00Z")

    def test_ping_evolves_continuously(self) -> None:
        values = [
            self.canonical_value("ping", "latency_ms", tick=tick)
            for tick in range(6)
        ]

        self.assertGreater(len(set(values)), 1)
        self.assertTrue(
            all(abs(after - before) < 10 for before, after in zip(values, values[1:]))
        )

    def test_cpu_normal_is_gradual_and_spike_is_progressive(self) -> None:
        normal = [
            self.canonical_value("cpu", "cpu_usage_pct", tick=tick)
            for tick in range(5)
        ]
        spike = [
            self.canonical_value(
                "cpu",
                "cpu_usage_pct",
                tick=tick,
                scenario="cpu_spike",
            )
            for tick in (0, 3, 6)
        ]

        self.assertTrue(
            all(abs(after - before) < 10 for before, after in zip(normal, normal[1:]))
        )
        self.assertEqual(spike, sorted(spike))
        self.assertGreater(spike[-1], spike[0])

    def test_memory_changes_slowly_and_emits_byte_units(self) -> None:
        usage = [
            self.canonical_value("memory", "memory_usage_pct", tick=tick)
            for tick in range(6)
        ]
        source_units = {
            self.source("memory", tick=tick)["channels"][1]["value"].split()[-1]
            for tick in range(3)
        }

        self.assertTrue(
            all(abs(after - before) < 2 for before, after in zip(usage, usage[1:]))
        )
        self.assertEqual(source_units, {"KByte", "MByte", "GByte"})

    def test_traffic_in_out_are_independent_and_units_vary(self) -> None:
        inbound = [
            self.canonical_value("traffic", "traffic_in_bps", tick=tick)
            for tick in range(6)
        ]
        outbound = [
            self.canonical_value("traffic", "traffic_out_bps", tick=tick)
            for tick in range(6)
        ]
        source_units = {
            channel["value"].split()[-1]
            for tick in range(3)
            for channel in self.source("traffic", tick=tick)["channels"][:2]
        }

        self.assertNotEqual(inbound, outbound)
        self.assertGreater(len(set(inbound)), 1)
        self.assertGreater(len(set(outbound)), 1)
        self.assertEqual(source_units, {"Kbit/s", "Mbit/s", "Gbit/s"})

    def test_traffic_saturation_increases_both_directions(self) -> None:
        inbound = [
            self.canonical_value(
                "traffic",
                "traffic_in_bps",
                tick=tick,
                scenario="traffic_saturation",
            )
            for tick in (0, 4, 8)
        ]
        outbound = [
            self.canonical_value(
                "traffic",
                "traffic_out_bps",
                tick=tick,
                scenario="traffic_saturation",
            )
            for tick in (0, 4, 8)
        ]

        self.assertEqual(inbound, sorted(inbound))
        self.assertEqual(outbound, sorted(outbound))
        self.assertGreater(inbound[-1], inbound[0])
        self.assertGreater(outbound[-1], outbound[0])

    def test_sfp_normal_variation_is_small_and_degradation_is_gradual(self) -> None:
        normal = [
            self.canonical_value("sfp", "rx_power_dbm", tick=tick)
            for tick in range(5)
        ]
        degraded = [
            self.canonical_value(
                "sfp",
                "rx_power_dbm",
                tick=tick,
                scenario="sfp_degradation",
            )
            for tick in (0, 4, 8)
        ]

        self.assertTrue(
            all(abs(after - before) < 0.2 for before, after in zip(normal, normal[1:]))
        )
        self.assertEqual(degraded, sorted(degraded, reverse=True))
        self.assertLess(degraded[-1], degraded[0])

    def test_packet_loss_scenario_increases_loss_progressively(self) -> None:
        losses = [
            self.canonical_value(
                "ping",
                "packet_loss_pct",
                tick=tick,
                scenario="packet_loss",
            )
            for tick in (0, 4, 8)
        ]

        self.assertEqual(losses, sorted(losses))
        self.assertGreater(losses[-1], losses[0])

    def test_sfp_partial_sensors_cover_full_and_reduced_channel_sets(self) -> None:
        channel_counts = {
            len(
                self.simulator.multichannel.sensor_source(
                    self.slug,
                    sensor_id,
                )["channels"]
            )
            for sensor_id in self.simulator.multichannel.sensor_ids(self.slug, "sfp")
        }

        self.assertTrue({1, 2, 3, 5}.issubset(channel_counts))
        partial = self.source("sfp", scenario="partial_channels")
        result = self.adapt(partial)
        self.assertGreaterEqual(len(result.samples), 1)
        self.assertLessEqual(len(result.samples), 3)
        self.assertEqual(result.issues, ())

    def test_all_supported_families_normalize_through_adapter(self) -> None:
        expected = {
            "ping": {"latency_ms", "packet_loss_pct", "availability_pct"},
            "cpu": {"cpu_usage_pct"},
            "memory": {"memory_usage_pct", "memory_used_bytes"},
            "traffic": {"traffic_in_bps", "traffic_out_bps", "utilization_pct"},
            "sfp": {"rx_power_dbm", "tx_power_dbm", "temperature_c", "voltage_v", "bias_current_ma"},
        }

        for family, canonical_keys in expected.items():
            with self.subTest(family=family):
                source = self.source(family)
                result = self.adapt(source)
                self.assertTrue({sample.channel_key for sample in result.samples})
                self.assertTrue(
                    {sample.channel_key for sample in result.samples}.issubset(canonical_keys)
                )
                self.assertEqual(result.issues, ())
                self.assertEqual(result.unknown_channels, ())

    def test_source_remains_prtg_like_not_canonical(self) -> None:
        traffic = self.source("traffic")

        self.assertEqual(traffic["sensor_type"], "traffic")
        self.assertEqual(
            {channel["channel"] for channel in traffic["channels"]},
            {"Traffic In", "Traffic Out", "Utilization"},
        )
        self.assertNotIn("traffic_in_bps", repr(traffic["channels"]))
        self.assertTrue(all(isinstance(channel["value"], str) for channel in traffic["channels"]))

    def test_unknown_channel_scenario_preserves_known_samples(self) -> None:
        source = self.source("sfp", scenario="unknown_channel")
        result = self.adapt(source)

        self.assertGreater(len(result.samples), 0)
        self.assertEqual(result.issues, ())
        self.assertEqual(len(result.unknown_channels), 1)
        self.assertEqual(result.unknown_channels[0].source_channel, "Laser Health Index")

    def test_invalid_value_faults_are_explicit_and_do_not_zero_data(self) -> None:
        expectations = {
            "n_a": AdapterIssueCode.INVALID_VALUE,
            "dashes": AdapterIssueCode.INVALID_VALUE,
            "unknown_unit": AdapterIssueCode.UNKNOWN_UNIT,
            "invalid_timestamp": AdapterIssueCode.INVALID_TIMESTAMP,
        }

        for fault, issue_code in expectations.items():
            with self.subTest(fault=fault):
                source = self.source("traffic", fault=fault)
                result = self.adapt(source)
                self.assertEqual(result.issues[0].code, issue_code)
                self.assertGreater(len(result.samples), 0)
                self.assertNotIn(0, [sample.value for sample in result.samples])

        scenario_result = self.adapt(
            self.source("traffic", scenario="invalid_value")
        )
        self.assertEqual(
            scenario_result.issues[0].code,
            AdapterIssueCode.INVALID_VALUE,
        )

    def test_scenarios_are_small_explicit_and_validated(self) -> None:
        self.assertEqual(
            SUPPORTED_SCENARIOS,
            {
                "normal",
                "cpu_spike",
                "traffic_saturation",
                "sfp_degradation",
                "packet_loss",
                "partial_channels",
                "unknown_channel",
                "invalid_value",
            },
        )
        with self.assertRaisesRegex(ValueError, "unsupported scenario"):
            self.simulator.set_scenario("unbounded-random-chaos")

    def test_multichannel_history_has_ordered_samples_for_every_family(self) -> None:
        for family in ("ping", "cpu", "memory", "traffic", "sfp"):
            with self.subTest(family=family):
                history = self.simulator.multichannel_history(
                    self.slug,
                    self.sensor_id(family),
                    points=7,
                )
                self.assertEqual(len(history["samples"]), 7)
                timestamps = [sample["timestamp"] for sample in history["samples"]]
                self.assertEqual(timestamps, sorted(timestamps))
                for source in history["samples"]:
                    result = self.adapt(source)
                    self.assertGreater(len(result.samples), 0)
                    self.assertEqual(result.issues, ())

    def test_opt_in_http_endpoints_expose_current_and_historic_sources(self) -> None:
        sensor_id = self.sensor_id("traffic")
        with mock.patch.object(datos_simulados, "sim", self.simulator):
            client = datos_simulados.app.test_client()
            current = client.get(
                f"/{self.slug}/api/multichannel.json",
                query_string={"id": sensor_id, "advance": 2},
            )
            historic = client.get(
                f"/{self.slug}/api/multichannel_history.json",
                query_string={"id": sensor_id, "points": 5},
            )
            server = client.get(f"/{self.slug}/api/multichannel.json")

        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.get_json()["sensor_type"], "traffic")
        self.assertEqual(self.simulator.multichannel.current_tick, 2)
        self.assertEqual(historic.status_code, 200)
        self.assertEqual(len(historic.get_json()["samples"]), 5)
        self.assertEqual(server.status_code, 200)
        self.assertEqual(len(server.get_json()["sensors"]), 50)

    def test_legacy_endpoints_and_health_are_unchanged_after_ticks(self) -> None:
        with mock.patch.object(datos_simulados, "sim", self.simulator):
            client = datos_simulados.app.test_client()
            health_before = client.get("/healthz").get_json()
            devices_before = client.get(
                f"/{self.slug}/api/table.json?content=devices"
            ).get_json()
            sensors_before = client.get(
                f"/{self.slug}/api/table.json?content=sensors"
            ).get_json()

            self.simulator.tick(20)

            health_after = client.get("/healthz").get_json()
            devices_after = client.get(
                f"/{self.slug}/api/table.json?content=devices"
            ).get_json()
            sensors_after = client.get(
                f"/{self.slug}/api/table.json?content=sensors"
            ).get_json()

        self.assertEqual(health_before, health_after)
        self.assertEqual(
            set(health_before),
            {"ok", "synthetic", "servers", "sensors", "seed"},
        )
        self.assertEqual(devices_before, devices_after)
        self.assertEqual(sensors_before, sensors_after)

    def test_legacy_pause_resume_still_controls_source_status(self) -> None:
        sensor_id = self.sensor_id("ping")
        original_status = self.simulator.servers[self.slug].sensors[sensor_id].status_raw

        self.simulator.pause_for(self.slug, sensor_id, 60, "Synthetic pause")
        paused = self.simulator.multichannel_sensor(self.slug, sensor_id)
        self.simulator.set_pause(self.slug, sensor_id, paused=False)
        resumed = self.simulator.multichannel_sensor(self.slug, sensor_id)

        self.assertEqual(paused["status_raw"], 12)
        self.assertEqual(resumed["status_raw"], original_status)

    def test_invalid_multichannel_http_parameters_are_isolated_from_legacy(self) -> None:
        sensor_id = self.sensor_id("ping")
        with mock.patch.object(datos_simulados, "sim", self.simulator):
            client = datos_simulados.app.test_client()
            bad_scenario = client.get(
                f"/{self.slug}/api/multichannel.json",
                query_string={"id": sensor_id, "scenario": "not-supported"},
            )
            bad_points = client.get(
                f"/{self.slug}/api/multichannel_history.json",
                query_string={"id": sensor_id, "points": 0},
            )
            legacy = client.get(f"/{self.slug}/api/table.json?content=sensors")

        self.assertEqual(bad_scenario.status_code, 400)
        self.assertEqual(bad_points.status_code, 400)
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(len(legacy.get_json()["sensors"]), 50)


if __name__ == "__main__":
    unittest.main()
