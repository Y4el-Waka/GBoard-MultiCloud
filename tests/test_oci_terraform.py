from __future__ import annotations

import base64
import re
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OCI_ROOT = REPO_ROOT / "infra" / "oci"


def read(relative_name: str) -> str:
    return (OCI_ROOT / relative_name).read_text(encoding="utf-8")


class OCITerraformPreparationTests(unittest.TestCase):
    def test_configuration_is_split_and_versions_are_exact(self) -> None:
        required = {
            ".terraform.lock.hcl",
            "backend.tf",
            "bastion.tf",
            "compute.tf",
            "iam.tf",
            "locals.tf",
            "networking.tf",
            "ocir.tf",
            "outputs.tf",
            "providers.tf",
            "variables.tf",
            "versions.tf",
        }
        self.assertTrue(all((OCI_ROOT / name).is_file() for name in required))
        self.assertIn('required_version = "= 1.16.4"', read("versions.tf"))
        self.assertIn('version = "= 8.29.0"', read("versions.tf"))
        lock = read(".terraform.lock.hcl")
        self.assertIn('version     = "8.29.0"', lock)
        self.assertIn('constraints = "8.29.0"', lock)

    def test_scope_contains_only_reviewed_oci_resource_types(self) -> None:
        terraform = "\n".join(
            path.read_text(encoding="utf-8") for path in OCI_ROOT.glob("*.tf")
        )
        resources = set(re.findall(r'resource\s+"(oci_[^"]+)"', terraform))
        self.assertEqual(
            resources,
            {
                "oci_artifacts_container_repository",
                "oci_bastion_bastion",
                "oci_core_instance",
                "oci_core_nat_gateway",
                "oci_core_network_security_group",
                "oci_core_network_security_group_security_rule",
                "oci_core_route_table",
                "oci_core_security_list",
                "oci_core_service_gateway",
                "oci_core_subnet",
                "oci_core_vcn",
                "oci_core_volume",
                "oci_core_volume_attachment",
                "oci_identity_dynamic_group",
                "oci_identity_policy",
            },
        )
        for forbidden in (
            "oci_core_internet_gateway",
            "oci_core_public_ip",
            "oci_core_network_load_balancer",
            "oci_load_balancer_load_balancer",
            "oci_containerengine_cluster",
            "oci_database",
            "oci_redis",
            "oci_objectstorage_bucket",
        ):
            self.assertNotIn(f'resource "{forbidden}"', terraform)

    def test_tenancy_compartment_and_image_guards_are_required(self) -> None:
        variables = read("variables.tf")
        providers = read("providers.tf")
        self.assertRegex(providers, r"tenancy_ocid\s*=\s*var\.tenancy_ocid")
        self.assertIn("contains(var.allowed_tenancy_ocids, var.tenancy_ocid)", variables)
        self.assertIn(
            "contains(var.allowed_compartment_ocids, var.compartment_ocid)",
            variables,
        )
        for variable in (
            "tenancy_ocid",
            "allowed_tenancy_ocids",
            "compartment_ocid",
            "allowed_compartment_ocids",
            "availability_domain",
            "image_ocid",
            "admin_ssh_public_key",
            "vcn_cidr",
            "workload_subnet_cidr",
            "bastion_subnet_cidr",
            "bastion_client_cidrs",
            "base_package_nevras",
        ):
            block = re.search(
                rf'variable "{variable}" \{{(?P<body>.*?)\n\}}',
                variables,
                re.DOTALL,
            )
            self.assertIsNotNone(block, variable)
            self.assertNotIn("default", block.group("body"), variable)
        self.assertIn('var.region == "us-ashburn-1"', variables)
        self.assertIn('var.shape == "VM.Standard.E4.Flex"', variables)

    def test_network_is_private_and_ssh_ingress_is_bastion_only(self) -> None:
        networking = read("networking.tf")
        compute = read("compute.tf")
        self.assertEqual(networking.count("prohibit_public_ip_on_vnic = true"), 2)
        self.assertIn("assign_public_ip = false", compute)
        self.assertIn('source                    = "${oci_bastion_bastion.lab.private_endpoint_ip_address}/32"', networking)
        self.assertIn("min = 22", networking)
        self.assertIn("oci_core_nat_gateway", networking)
        self.assertIn("oci_core_service_gateway", networking)
        self.assertGreaterEqual(networking.count("destination               = \"169.254.169.254/32\""), 4)
        self.assertNotIn("oci_core_internet_gateway", networking)
        self.assertNotRegex(networking, r"destination_port_range\s*\{[^}]*min\s*=\s*(?:5067|9090)")
        self.assertNotIn('source                    = "0.0.0.0/0"', networking)

    def test_compute_storage_and_metadata_are_hardened(self) -> None:
        compute = read("compute.tf")
        variables = read("variables.tf")
        for contract in (
            'default     = "VM.Standard.E4.Flex"',
            "ocpus         = var.shape_ocpus",
            "memory_in_gbs = var.shape_memory_gib",
            "are_legacy_imds_endpoints_disabled = true",
            "is_pv_encryption_in_transit_enabled = true",
            "boot_volume_vpus_per_gb = 10",
            "vpus_per_gb         = 10",
            'device          = "/dev/oracleoci/oraclevdb"',
            "prevent_destroy = true",
        ):
            self.assertIn(contract, compute if contract not in variables else variables)
        self.assertIn("source_id               = var.image_ocid", compute)
        self.assertNotIn('source_type = "bootVolume"', compute)

    def test_ocir_access_is_repository_scoped_and_secretless(self) -> None:
        ocir = read("ocir.tf")
        iam = read("iam.tf")
        self.assertIn("is_immutable   = true", ocir)
        self.assertIn("is_public      = false", ocir)
        self.assertIn("prevent_destroy = true", ocir)
        self.assertIn("instance.id = '${oci_core_instance.coreview.id}'", iam)
        self.assertIn("to read repos in compartment id", iam)
        self.assertIn("target.repo.name", iam)
        for forbidden in ("manage repos", "manage all-resources", "auth_token"):
            self.assertNotIn(forbidden, iam.lower())

    def test_identifier_outputs_are_suppressed_from_routine_cli_output(self) -> None:
        outputs = read("outputs.tf")
        for name in (
            "vcn_id",
            "instance_id",
            "instance_private_ip",
            "bastion_id",
            "image_repository",
            "runtime_volume_id",
        ):
            block = re.search(
                rf'output "{name}" \{{(?P<body>.*?)\n\}}',
                outputs,
                re.DOTALL,
            )
            self.assertIsNotNone(block, name)
            self.assertIn("sensitive   = true", block.group("body"), name)

    def test_compose_preserves_shared_namespace_loopback_and_hardening(self) -> None:
        compose = read("templates/compose.oci.yaml.tftpl")
        for contract in (
            "COREVIEW_METRICS_PROVIDER: synthetic",
            "host_ip: 127.0.0.1",
            "$${COREVIEW_IMAGE:?Set COREVIEW_IMAGE to an approved OCIR digest URI}",
            "read_only: true",
            "no-new-privileges:true",
            "cap_drop:",
            "network_mode: service:simulator",
            "coreview-runtime:/runtime",
            "coreview-uploads:/app/static/uploads",
            "pull_policy: never",
        ):
            self.assertIn(contract, compose)
        self.assertEqual(compose.count("network_mode: service:simulator"), 2)
        self.assertNotIn("COREVIEW_METRICS_PROVIDER: prtg", compose)
        self.assertNotIn("build:", compose)

    def test_bootstrap_is_pinned_idempotent_secretless_and_fail_closed(self) -> None:
        bootstrap = read("templates/bootstrap.sh.tftpl")
        for contract in (
            'readonly bootstrap_marker="$install_root/.bootstrap-${bootstrap_revision}-complete"',
            'readonly runtime_disk="/dev/oracleoci/oraclevdb"',
            "systemctl mask docker.service docker.socket",
            "wipefs --noheadings",
            "Runtime volume filesystem is not ext4",
            "RequiresMountsFor=/var/lib/docker",
            "defaults,nodev,nosuid",
            "sha256sum --check --status",
            "${base_package_nevras}",
            "${ocir_helper_source_commit}",
            "${ocir_helper_source_sha256}",
            "${ocir_helper_binary_sha256}",
            "/usr/local/libexec/coreview/docker-credential-ocir-upstream",
            "docker-credential-coreview-ocir",
            "registry is outside the approved OCIR endpoint",
            "for attempt in $(seq 1 60)",
            "CoreView remains stopped",
            "TimeoutStartSec=900",
            '"credHelpers": {sys.argv[1]: "coreview-ocir"}',
            "docker pull \"$image_uri\"",
            "initialize-lab-secrets",
            "read -r -s",
            "openssl rand -hex 32",
            "chmod 0600",
            "ConditionPathExists=/opt/coreview/.env",
            "update-runtime-image",
            "Image update failed; the previous runtime configuration was restored.",
            "systemctl enable coreview-lab.service",
        ):
            self.assertIn(contract, bootstrap)
        self.assertNotIn("systemctl enable --now coreview-lab.service", bootstrap)
        self.assertNotIn("nofail", bootstrap)
        self.assertNotIn("docker login", bootstrap)
        self.assertIn("Static Docker credential storage is disabled", bootstrap)
        self.assertNotIn("git-${git_package_version}", bootstrap)
        self.assertNotIn("BEGIN PRIVATE KEY", bootstrap)
        self.assertNotRegex(bootstrap.lower(), r"(?:password|passphrase)\s*=\s*[\"'][^$]")

        rendered_size_upper_bound = len(bootstrap.encode("utf-8"))
        rendered_size_upper_bound += len(
            base64.b64encode(read("templates/compose.oci.yaml.tftpl").encode("utf-8"))
        )
        rendered_size_upper_bound += len(
            base64.b64encode((REPO_ROOT / "docker/servers.synthetic.json").read_bytes())
        )
        self.assertLess(rendered_size_upper_bound, 256 * 1024)
        result = subprocess.run(
            ["bash", "-n", str(OCI_ROOT / "templates" / "bootstrap.sh.tftpl")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bastion_is_private_short_lived_and_not_a_loopback_proxy(self) -> None:
        bastion = read("bastion.tf")
        variables = read("variables.tf")
        self.assertIn('bastion_type                 = "STANDARD"', bastion)
        self.assertIn("target_subnet_id             = oci_core_subnet.bastion.id", bastion)
        self.assertIn("client_cidr_block_allow_list = var.bastion_client_cidrs", bastion)
        self.assertIn("var.bastion_max_session_ttl_seconds >= 1800", variables)
        self.assertIn("var.bastion_max_session_ttl_seconds <= 10800", variables)
        self.assertNotRegex(bastion, r"(?:5067|9090)")

    def test_backend_is_external_native_locked_and_ignored(self) -> None:
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn('backend "oci" {}', read("backend.tf"))
        backend_example = read("backend.example.hcl")
        self.assertIn('auth                = "SecurityToken"', backend_example)
        self.assertIn("replace-with-approved-state-bucket", backend_example)
        for ignored in (
            ".terraform/",
            "*.tfstate",
            "*.tfvars",
            "backend.hcl",
            "*.tfplan",
        ):
            self.assertIn(ignored, gitignore)

    def test_documentation_is_honest_about_offline_status(self) -> None:
        phase = (REPO_ROOT / "docs" / "PHASE_1_3.md").read_text(encoding="utf-8")
        final = (REPO_ROOT / "docs" / "PHASE_1_3_FINAL.md").read_text(encoding="utf-8")
        deployment = read("DEPLOYMENT_RUNBOOK.md")
        destroy = read("DESTROY_RUNBOOK.md")
        for contract in (
            "Architecture: **Implemented**",
            "Real OCI plan: **Not executed**",
            "Workload deployment: **Not executed**",
            "OCI resources created: **0**",
        ):
            self.assertIn(contract.replace("**", ""), final)
            self.assertIn(contract, phase)
        self.assertIn("Approval OCI-1", deployment)
        self.assertIn("oci bastion session create-port-forwarding", deployment)
        self.assertIn("-L 5067:127.0.0.1:5067", deployment)
        self.assertIn("-L 9090:127.0.0.1:9090", deployment)
        self.assertNotIn("terraform apply -auto-approve", deployment)
        self.assertIn("prevent_destroy", destroy)
        self.assertIn("Residual-cost audit", destroy)


if __name__ == "__main__":
    unittest.main()
