from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_DIGEST = "sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e"
TRIVY_DIGEST = "sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"


class ContainerSupplyChainTests(unittest.TestCase):
    def test_base_image_and_scanner_are_immutable(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        scanner = (REPO_ROOT / "scripts/container_supply_chain.sh").read_text(
            encoding="utf-8"
        )

        self.assertRegex(
            dockerfile.splitlines()[0],
            rf"^FROM python:3\.12-slim-bookworm@{BASE_DIGEST}$",
        )
        self.assertIn(f'aquasec/trivy@{TRIVY_DIGEST}', scanner)
        self.assertNotIn("aquasec/trivy:latest", scanner)

    def test_runtime_install_uses_only_hash_locked_dependencies(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        lock = (REPO_ROOT / "requirements.lock").read_text(encoding="utf-8")
        compatibility = (REPO_ROOT / "requirements.txt").read_text(
            encoding="utf-8"
        )

        self.assertIn("COPY requirements.lock", dockerfile)
        self.assertIn("--require-hashes", dockerfile)
        self.assertIn("python -m pip uninstall --yes pip", dockerfile)
        self.assertIn("-r requirements.lock", compatibility)
        requirement_lines = [
            line for line in lock.splitlines() if re.match(r"^[a-zA-Z0-9_.-]+==", line)
        ]
        self.assertGreaterEqual(len(requirement_lines), 10)
        self.assertGreaterEqual(lock.count("--hash=sha256:"), len(requirement_lines))
        self.assertNotRegex(lock, r"(?m)^[a-zA-Z0-9_.-]+(?<!==)$")

    def test_supply_chain_emits_standard_artifacts_and_gates_fixable_risk(self) -> None:
        scanner = (REPO_ROOT / "scripts/container_supply_chain.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("sbom.cdx.json", scanner)
        self.assertIn("vulnerabilities.json", scanner)
        self.assertIn("--format cyclonedx", scanner)
        self.assertIn("--severity HIGH,CRITICAL", scanner)
        self.assertIn("--ignore-unfixed", scanner)
        self.assertIn("--exit-code 1", scanner)

    def test_ci_runs_tests_clean_compose_smoke_sbom_scan_and_cleanup(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/container-ci.yml").read_text(
            encoding="utf-8"
        )

        for contract in (
            "python -m unittest discover -s tests -v",
            "node --test tests/frontend/test_metrics_history.js",
            "python -m compileall .",
            "docker compose config --quiet",
            "docker compose down --volumes --remove-orphans || true",
            "docker compose up --detach --build --wait",
            "python scripts/docker_lab_smoke.py",
            "COREVIEW_LAB_EXPECT_PERSISTED=1",
            "scripts/container_supply_chain.sh",
            "docker compose down --volumes --remove-orphans",
        ):
            self.assertIn(contract, workflow)
        self.assertIn("openssl rand", workflow)
        self.assertNotIn("password:", workflow.lower())
        self.assertNotIn("docker push", workflow)
        self.assertRegex(workflow, r"actions/checkout@[0-9a-f]{40}")
        self.assertRegex(workflow, r"actions/setup-python@[0-9a-f]{40}")
        self.assertRegex(workflow, r"actions/upload-artifact@[0-9a-f]{40}")

    def test_compose_security_contract_remains_intact(self) -> None:
        compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")

        for contract in (
            "host_ip: 127.0.0.1",
            "read_only: true",
            "no-new-privileges:true",
            "cap_drop:",
            "COREVIEW_METRICS_PROVIDER: synthetic",
            "network_mode: service:simulator",
        ):
            self.assertIn(contract, compose)
        self.assertNotIn("COREVIEW_METRICS_PROVIDER: prtg", compose)

    def test_frontend_remote_assets_are_an_explicit_frozen_allowlist(self) -> None:
        expected = {
            "https://cdn.tailwindcss.com",
            "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css",
            "https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js",
            "https://cdnjs.cloudflare.com/ajax/libs/toastr.js/latest/toastr.min.css",
            "https://cdnjs.cloudflare.com/ajax/libs/toastr.js/latest/toastr.min.js",
            "https://cdn.jsdelivr.net/npm/chart.js",
            "https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js",
            "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap",
        }
        observed: set[str] = set()
        pattern = re.compile(
            r"https://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^\"'()\s]*"
        )

        for relative in (
            "templates/login.html",
            "templates/dashboard_prtg.html",
            "static/dashboard.css",
            "static/dashboard.js",
            "static/metrics-history.js",
        ):
            observed.update(
                pattern.findall((REPO_ROOT / relative).read_text(encoding="utf-8"))
            )

        self.assertEqual(observed, expected)

    def test_build_context_and_artifacts_exclude_runtime_secrets(self) -> None:
        dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

        for protected in (".env", "servers.json", "usuarios.db", "artifacts"):
            self.assertIn(protected, dockerignore)
        for generated in ("artifacts/", ".cache/"):
            self.assertIn(generated, gitignore)
        self.assertNotIn("COPY . ", dockerfile)
        self.assertNotIn("COPY .\n", dockerfile)


if __name__ == "__main__":
    unittest.main()
