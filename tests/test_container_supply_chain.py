from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_DIGEST = "sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e"
NODE_DIGEST = "sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402"
TRIVY_DIGEST = "sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"


class ContainerSupplyChainTests(unittest.TestCase):
    def test_base_image_and_scanner_are_immutable(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        scanner = (REPO_ROOT / "scripts/container_supply_chain.sh").read_text(
            encoding="utf-8"
        )

        self.assertRegex(dockerfile.splitlines()[0], rf"^FROM node:22-alpine@{NODE_DIGEST} AS frontend-builder$")
        self.assertIn(f"FROM python:3.12-slim-bookworm@{BASE_DIGEST}", dockerfile)
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
            "npm ci --ignore-scripts --no-audit --no-fund",
            "npm run build:frontend",
            "git diff --exit-code -- static/vendor",
            "npm run test:frontend",
            "python -m compileall .",
            "docker compose config --quiet",
            "docker compose down --volumes --remove-orphans || true",
            "docker compose up --detach --build --wait",
            "python scripts/docker_lab_smoke.py",
            "scripts/run_offline_frontend_smoke.sh",
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
        self.assertRegex(workflow, r"actions/setup-node@[0-9a-f]{40}")
        self.assertRegex(workflow, r"actions/upload-artifact@[0-9a-f]{40}")
        self.assertIn("path: artifacts", workflow)

    def test_ci_image_export_is_manual_separate_and_after_validation(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/container-ci.yml").read_text(
            encoding="utf-8"
        )
        exporter = (REPO_ROOT / "scripts/export_ci_image.sh").read_text(
            encoding="utf-8"
        )

        scan = workflow.index("scripts/container_supply_chain.sh")
        evidence_upload = workflow.index("name: coreview-container-evidence")
        privacy_guard = workflow.index(
            "name: Refuse image export outside a private repository"
        )
        image_export = workflow.index("scripts/export_ci_image.sh")
        image_upload = workflow.index("name: coreview-validated-image-")
        cleanup = workflow.index("name: Clean Compose resources")
        for required_control in (
            "Run Python tests",
            "Run JavaScript tests",
            "Run static validation",
            "Prove clean-state startup",
            "Run full synthetic smoke",
            "Run bounded API burst under resource limits",
            "Run restricted resource soak",
            "Run browser smoke without external network",
            "Verify persistence across consumer restart",
            "Generate SBOM and scan image",
        ):
            self.assertLess(workflow.index(required_control), image_export)
        self.assertLess(scan, evidence_upload)
        self.assertLess(evidence_upload, privacy_guard)
        self.assertLess(privacy_guard, image_export)
        self.assertLess(image_export, image_upload)
        self.assertLess(image_upload, cleanup)
        self.assertIn("export_validated_image", workflow)
        self.assertIn("default: false", workflow)
        self.assertEqual(workflow.count("retention-days: 3"), 1)
        self.assertIn("compression-level: 0", workflow)
        self.assertIn("runner.temp }}/coreview-validated-image", workflow)
        self.assertIn("success()", workflow)
        self.assertIn("github.event.repository.private == true", workflow)
        self.assertIn("github.event.repository.private != true", workflow)
        self.assertNotIn("id-token: write", workflow)
        self.assertNotIn("docker push", workflow)

        self.assertIn("docker image save --output", exporter)
        self.assertIn("sha256sum --check", exporter)
        self.assertIn("GITHUB_SHA", exporter)
        self.assertIn("GITHUB_RUN_ID", exporter)
        self.assertIn("GITHUB_RUN_ATTEMPT", exporter)
        self.assertIn("GITHUB_WORKFLOW_REF", exporter)
        self.assertIn("image-inspect.json", exporter)
        self.assertIn("validated_image_id", exporter)
        self.assertIn("secret_checks", exporter)
        self.assertIn("forbidden secret environment key", exporter)
        self.assertIn("Image build history contains", exporter)
        self.assertIn("--network none", exporter)
        self.assertIn("--read-only", exporter)
        self.assertIn("--cap-drop ALL", exporter)
        self.assertIn("--security-opt no-new-privileges", exporter)
        self.assertIn("forbidden runtime file", exporter)
        self.assertNotIn("docker build", exporter)
        self.assertNotIn("docker push", exporter)

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

    def test_frontend_runtime_has_no_remote_assets(self) -> None:
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

        self.assertEqual(observed, set())

    def test_build_context_and_artifacts_exclude_runtime_secrets(self) -> None:
        dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

        for protected in (
            ".env",
            "servers.json",
            "usuarios.db",
            "artifacts",
            "node_modules",
            "static/vendor",
        ):
            self.assertIn(protected, dockerignore)
        for generated in ("artifacts/", ".cache/"):
            self.assertIn(generated, gitignore)
        self.assertNotIn("COPY . ", dockerfile)
        self.assertNotIn("COPY .\n", dockerfile)
        self.assertIn("COPY --from=frontend-builder", dockerfile)


if __name__ == "__main__":
    unittest.main()
