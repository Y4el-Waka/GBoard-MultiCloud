from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INFRA_ROOT = REPO_ROOT / "infra"

CLOUDS = ("aws", "gcp", "azure", "oci")
COMPOSE_FILES = {
    "aws": "templates/compose.aws.yaml.tftpl",
    "gcp": "templates/compose.gcp.yaml.tftpl",
    "azure": "templates/compose.azure.yaml.tftpl",
    "oci": "templates/compose.oci.yaml.tftpl",
}
PUBLIC_OUTPUTS = {
    "aws": {"aws_region"},
    "gcp": {"region", "zone"},
    "azure": set(),
    "oci": {"region"},
}
EXPECTED_PROVIDERS = {
    "aws": ("hashicorp/aws", "6.61.0", "s3"),
    "gcp": ("hashicorp/google", "7.17.0", "gcs"),
    "azure": ("hashicorp/azurerm", "5.7.0", "azurerm"),
    "oci": ("oracle/oci", "8.29.0", "oci"),
}


def read(cloud: str, relative_name: str) -> str:
    return (INFRA_ROOT / cloud / relative_name).read_text(encoding="utf-8")


class MultiCloudContractTests(unittest.TestCase):
    def test_common_runtime_contract_is_preserved(self) -> None:
        for cloud, relative_name in COMPOSE_FILES.items():
            compose = read(cloud, relative_name)
            with self.subTest(cloud=cloud):
                for service in ("simulator:", "collector:", "app:"):
                    self.assertIn(service, compose)
                self.assertEqual(compose.count("network_mode: service:simulator"), 2)
                self.assertGreaterEqual(compose.count("host_ip: 127.0.0.1"), 2)
                self.assertIn("published: \"9090\"", compose)
                self.assertIn("published: \"5067\"", compose)
                self.assertIn("COREVIEW_METRICS_PROVIDER: synthetic", compose)
                self.assertIn("coreview-runtime:/runtime", compose)
                self.assertIn("coreview-uploads:/app/static/uploads", compose)
                self.assertIn("read_only: true", compose)
                self.assertIn("no-new-privileges:true", compose)
                self.assertNotIn("COREVIEW_METRICS_PROVIDER: prtg", compose)

    def test_exact_terraform_and_provider_versions_are_cross_cloud_invariants(self) -> None:
        for cloud, (provider, version, backend) in EXPECTED_PROVIDERS.items():
            versions = read(cloud, "versions.tf")
            lock = read(cloud, ".terraform.lock.hcl")
            backend_text = read(cloud, "backend.tf")
            with self.subTest(cloud=cloud):
                self.assertIn('required_version = "= 1.16.4"', versions)
                self.assertIn(f'source  = "{provider}"', versions)
                self.assertIn(f'version = "= {version}"', versions)
                self.assertIn(f'version     = "{version}"', lock)
                self.assertIn(f'backend "{backend}"', backend_text)

    def test_identity_bearing_outputs_are_suppressed_from_routine_output(self) -> None:
        for cloud in CLOUDS:
            outputs = read(cloud, "outputs.tf")
            blocks = re.findall(
                r'output "(?P<name>[^"]+)" \{(?P<body>.*?)\n\}',
                outputs,
                re.DOTALL,
            )
            self.assertTrue(blocks, cloud)
            for name, body in blocks:
                with self.subTest(cloud=cloud, output=name):
                    if name in PUBLIC_OUTPUTS[cloud]:
                        continue
                    self.assertIn("sensitive   = true", body)

    def test_operator_state_and_plan_files_remain_ignored(self) -> None:
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (
            "backend.hcl",
            "*.tfvars",
            "*.tfstate",
            "*.tfplan",
            "*.plan",
            ".terraform/",
        ):
            self.assertIn(pattern, gitignore)

    def test_evidence_matrix_does_not_overstate_cloud_validation(self) -> None:
        phase = (REPO_ROOT / "docs" / "PHASE_1_4.md").read_text(encoding="utf-8")
        contracts = (
            "AWS | Implemented | Passed | Executed: 16 add / 0 change / 0 destroy | Not deployed | Created and verified",
            "GCP | Implemented | Passed | Not executed | Not deployed | Proposed, not created",
            "Azure | Implemented | Passed | Not executed | Not deployed | Proposed, not created",
            "OCI | Implemented | Passed | Not executed | Not deployed | Proposed, not created",
        )
        for contract in contracts:
            self.assertIn(contract, phase)


if __name__ == "__main__":
    unittest.main()
