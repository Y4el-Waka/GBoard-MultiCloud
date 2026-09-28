from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"^  {re.escape(service)}:\n(?P<body>(?:    .*\n|\n)*)",
        compose,
        re.MULTILINE,
    )
    if not match:
        raise AssertionError(f"missing Compose service: {service}")
    return match.group("body")


class ResourceLimitContractTests(unittest.TestCase):
    def test_compose_limits_match_measured_candidates(self) -> None:
        compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
        expected = {
            "simulator": "128m",
            "collector": "96m",
            "app": "128m",
        }
        for service, memory in expected.items():
            block = service_block(compose, service)
            with self.subTest(service=service):
                self.assertIn("cpus: 1.0", block)
                self.assertIn(f"mem_limit: {memory}", block)
                self.assertIn("<<: *common-security", block)
        self.assertEqual(compose.count("network_mode: service:simulator"), 2)

    def test_burst_runner_is_bounded_synthetic_and_loopback_only(self) -> None:
        source = (REPO_ROOT / "scripts/docker_lab_burst.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("1 <= args.requests <= 1000", source)
        self.assertIn("1 <= args.workers <= 32", source)
        self.assertIn("_loopback_base_url", source)
        self.assertIn('"synthetic_only": True', source)
        self.assertIn('"error_type": type(exc).__name__', source)
        self.assertNotIn("password\"", source.split("report =", 1)[1])

    def test_ci_runs_burst_and_restricted_soak_before_evidence_upload(self) -> None:
        workflow = (
            REPO_ROOT / ".github/workflows/container-ci.yml"
        ).read_text(encoding="utf-8")
        burst = workflow.index("python scripts/docker_lab_burst.py")
        soak = workflow.index("python scripts/docker_lab_soak.py")
        upload = workflow.index("actions/upload-artifact@")
        cleanup = workflow.index("if: always()", upload)
        self.assertLess(burst, soak)
        self.assertLess(soak, upload)
        self.assertLess(upload, cleanup)
        self.assertIn("--duration 300", workflow)
        self.assertIn("--interval 15", workflow)
        self.assertIn("docker compose down --volumes --remove-orphans", workflow)


if __name__ == "__main__":
    unittest.main()
