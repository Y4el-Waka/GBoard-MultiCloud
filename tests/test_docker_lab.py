from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import docker_healthcheck, docker_lab_smoke


REPO_ROOT = Path(__file__).resolve().parents[1]


class DockerLabTests(unittest.TestCase):
    def test_compose_keeps_three_processes_and_shared_loopback_namespace(self) -> None:
        compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")

        for service in ("simulator:", "collector:", "app:"):
            self.assertIn(service, compose)
        self.assertEqual(compose.count("network_mode: service:simulator"), 2)
        self.assertIn("host_ip: 127.0.0.1", compose)
        self.assertNotIn("host_ip: 0.0.0.0", compose)
        self.assertIn("driver: bridge", compose)
        self.assertIn("COREVIEW_METRICS_PROVIDER: synthetic", compose)
        self.assertIn('COREVIEW_METRICS_UI_ENABLED: "true"', compose)
        self.assertNotIn("COREVIEW_METRICS_PROVIDER: prtg", compose)
        self.assertIn("datos_simulados:app", compose)
        self.assertNotIn("--access-logfile\n      - '-'\n      - datos_simulados:app", compose)

    def test_compose_has_dependency_aware_healthchecks_and_hardening(self) -> None:
        compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertEqual(compose.count("condition: service_healthy"), 3)
        for health_target in ("simulator", "collector", "app"):
            self.assertIn(
                f'"scripts/docker_healthcheck.py", "{health_target}"',
                compose,
            )
        self.assertIn("read_only: true", compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("cap_drop:", compose)
        self.assertIn("driver: local", compose)

    def test_runtime_storage_is_named_and_synthetic_config_is_read_only(self) -> None:
        compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertEqual(compose.count("coreview-runtime:/runtime"), 2)
        self.assertIn("coreview-uploads:/app/static/uploads", compose)
        self.assertEqual(
            compose.count(
                "./docker/servers.synthetic.json:/config/servers.synthetic.json:ro"
            ),
            2,
        )
        self.assertIn("coreview-runtime:", compose)
        self.assertIn("coreview-uploads:", compose)

    def test_image_runs_non_root_and_does_not_copy_runtime_secrets(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn("USER 10001:10001", dockerfile)
        self.assertNotIn("COPY . ", dockerfile)
        self.assertNotIn("COPY .\n", dockerfile)
        self.assertNotIn("servers.synthetic.json", dockerfile)
        for protected in (
            ".env",
            "servers.json",
            "usuarios.db",
            "estado_sensores_prtg.json",
            "static/uploads",
        ):
            self.assertIn(protected, dockerignore)

    def test_synthetic_server_file_has_exact_stable_loopback_topology(self) -> None:
        servers = json.loads(
            (REPO_ROOT / "docker/servers.synthetic.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(len(servers), 13)
        self.assertEqual(
            [server["id"] for server in servers],
            [f"srv_lab_{index:02d}" for index in range(1, 14)],
        )
        self.assertEqual(
            [server["url"] for server in servers],
            [
                f"http://127.0.0.1:9090/prtg-{index:02d}"
                for index in range(1, 14)
            ],
        )
        self.assertTrue(all(server["type"] == "prtg" for server in servers))
        self.assertTrue(all(server["enabled"] is True for server in servers))

    def test_healthchecks_validate_availability_not_only_process_state(self) -> None:
        docker_healthcheck.validate_simulator(
            {
                "ok": True,
                "synthetic": True,
                "servers": 13,
                "sensors": 1000,
            }
        )
        docker_healthcheck.validate_app(
            {"ok": True, "data_file_exists": True, "servers": 13}
        )

        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "sensores": [{} for _ in range(1000)],
                        "collector": {
                            "servers_configured": 13,
                            "servers_with_error": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            mtime = snapshot.stat().st_mtime
            docker_healthcheck.validate_collector_snapshot(
                snapshot,
                now=mtime + 5,
                max_age_seconds=30,
            )
            with self.assertRaisesRegex(
                docker_healthcheck.HealthcheckError,
                "stale",
            ):
                docker_healthcheck.validate_collector_snapshot(
                    snapshot,
                    now=mtime + 31,
                    max_age_seconds=30,
                )

    def test_smoke_and_provider_destinations_remain_loopback_only(self) -> None:
        self.assertEqual(
            docker_lab_smoke._loopback_base_url(
                "http://127.0.0.1:5067",
                "test",
            ),
            "http://127.0.0.1:5067",
        )
        for unsafe in (
            "http://198.51.100.10:5067",
            "http://example.test:5067",
            "http://user:password@127.0.0.1:5067",
            "http://127.0.0.1:5067/?target=remote",
        ):
            with self.subTest(url=unsafe):
                with self.assertRaises(docker_lab_smoke.SmokeError):
                    docker_lab_smoke._loopback_base_url(unsafe, "test")


if __name__ == "__main__":
    unittest.main()
