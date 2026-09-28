from __future__ import annotations

import base64
import re
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GCP_ROOT = REPO_ROOT / "infra" / "gcp"


def read(relative_name: str) -> str:
    return (GCP_ROOT / relative_name).read_text(encoding="utf-8")


class GCPTerraformPreparationTests(unittest.TestCase):
    def test_configuration_is_split_and_versions_are_exact(self) -> None:
        required = {
            ".terraform.lock.hcl",
            "artifact_registry.tf",
            "backend.tf",
            "compute.tf",
            "iam.tf",
            "locals.tf",
            "networking.tf",
            "outputs.tf",
            "providers.tf",
            "services.tf",
            "variables.tf",
            "versions.tf",
        }
        self.assertTrue(all((GCP_ROOT / name).is_file() for name in required))

        versions = read("versions.tf")
        self.assertIn('required_version = "= 1.16.4"', versions)
        self.assertIn('version = "= 7.17.0"', versions)
        lock = read(".terraform.lock.hcl")
        self.assertIn('version     = "7.17.0"', lock)
        self.assertIn('constraints = "7.17.0"', lock)

    def test_scope_has_no_project_creation_or_unrequested_services(self) -> None:
        terraform = "\n".join(
            path.read_text(encoding="utf-8") for path in GCP_ROOT.glob("*.tf")
        )
        resources = set(re.findall(r'resource\s+"(google_[^"]+)"', terraform))
        self.assertEqual(
            resources,
            {
                "google_artifact_registry_repository",
                "google_artifact_registry_repository_iam_member",
                "google_compute_disk",
                "google_compute_firewall",
                "google_compute_instance",
                "google_compute_network",
                "google_compute_router",
                "google_compute_router_nat",
                "google_compute_subnetwork",
                "google_project_service",
                "google_service_account",
            },
        )
        for forbidden in (
            "google_project",
            "google_compute_address",
            "google_compute_global_address",
            "google_compute_forwarding_rule",
            "google_compute_instance_group",
            "google_container_cluster",
            "google_redis_instance",
            "google_sql_database_instance",
            "google_service_account_key",
        ):
            self.assertNotIn(f'resource "{forbidden}"', terraform)

    def test_project_region_network_and_image_are_explicit_inputs(self) -> None:
        variables = read("variables.tf")
        for variable in (
            "project_id",
            "allowed_project_ids",
            "subnet_cidr",
            "boot_image",
            "image_tag",
        ):
            block = re.search(
                rf'variable "{variable}" \{{(?P<body>.*?)\n\}}',
                variables,
                re.DOTALL,
            )
            self.assertIsNotNone(block, variable)
            self.assertNotIn("default", block.group("body"), variable)
        self.assertIn("contains(var.allowed_project_ids, var.project_id)", variables)
        self.assertIn('default     = "us-east1"', variables)
        self.assertIn("!strcontains(var.boot_image, \"/family/\")", variables)

    def test_private_network_uses_nat_and_only_iap_ssh_ingress(self) -> None:
        networking = read("networking.tf")
        compute = read("compute.tf")
        self.assertIn("auto_create_subnetworks         = false", networking)
        self.assertIn("delete_default_routes_on_create = false", networking)
        self.assertIn("private_ip_google_access = true", networking)
        self.assertIn('nat_ip_allocate_option             = "AUTO_ONLY"', networking)
        self.assertIn('source_ranges           = ["35.235.240.0/20"]', networking)
        self.assertIn('aggregation_interval = "INTERVAL_10_MIN"', networking)
        self.assertIn("flow_sampling        = 0.5", networking)
        self.assertIn('metadata             = "EXCLUDE_ALL_METADATA"', networking)
        self.assertIn('ports    = ["22"]', networking)
        self.assertNotRegex(networking, r'ports\s*=\s*\["(?:5067|9090)"\]')
        self.assertNotIn("access_config {", compute)
        self.assertIn("# Intentionally no access_config block", compute)
        self.assertIn('name      = "${local.name_prefix}-deny-other-egress"', networking)

    def test_instance_is_hardened_and_uses_persistent_encrypted_storage(self) -> None:
        compute = read("compute.tf")
        variables = read("variables.tf")
        for contract in (
            "can_ip_forward      = false",
            "deletion_protection = var.deletion_protection",
            "enable_secure_boot          = true",
            "enable_vtpm                 = true",
            "enable_integrity_monitoring = true",
            'enable-oslogin            = "TRUE"',
            'block-project-ssh-keys    = "TRUE"',
            'type    = "pd-balanced"',
            'device_name = "coreview-runtime"',
        ):
            self.assertIn(contract, compute)
        self.assertIn('default     = "e2-small"', variables)
        self.assertIn("Google-managed encryption by default", compute)
        self.assertNotIn("access_config {", compute)

    def test_runtime_identity_is_keyless_and_repository_scoped_read_only(self) -> None:
        iam = read("iam.tf")
        self.assertIn('role       = "roles/artifactregistry.reader"', iam)
        self.assertIn("google_artifact_registry_repository.coreview", iam)
        self.assertIn("roles/iap.tunnelResourceAccessor", iam)
        self.assertIn("roles/compute.osLogin", iam)
        self.assertNotIn("google_service_account_key", iam)
        for forbidden in (
            "roles/editor",
            "roles/owner",
            "roles/artifactregistry.writer",
            "roles/artifactregistry.admin",
        ):
            self.assertNotIn(forbidden, iam)

    def test_project_apis_are_preserved_on_destroy(self) -> None:
        services = read("services.tf")
        for api in (
            "artifactregistry.googleapis.com",
            "compute.googleapis.com",
            "iam.googleapis.com",
            "iap.googleapis.com",
            "oslogin.googleapis.com",
        ):
            self.assertIn(f'"{api}"', services)
        self.assertIn("disable_on_destroy         = false", services)
        self.assertIn("disable_dependent_services = false", services)

    def test_immutable_image_resolution_matches_variable_contract(self) -> None:
        variables = read("variables.tf")
        deployment = read("DEPLOYMENT_RUNBOOK.md")
        self.assertIn("projects/debian-cloud/global/images/debian-12-", variables)
        self.assertIn("--format='value(name)'", deployment)
        self.assertIn("projects/debian-cloud/global/images/%s", deployment)
        self.assertNotIn("--format='value(selfLink)'", deployment)
        self.assertNotIn("/family/", read("terraform.tfvars.example"))

    def test_repository_is_private_and_immutable(self) -> None:
        artifact_registry = read("artifact_registry.tf")
        self.assertIn('format        = "DOCKER"', artifact_registry)
        self.assertIn('mode          = "STANDARD_REPOSITORY"', artifact_registry)
        self.assertIn("prevent_destroy = true", artifact_registry)
        self.assertIn("immutable_tags = true", artifact_registry)
        self.assertNotIn("allUsers", artifact_registry)
        self.assertNotIn("allAuthenticatedUsers", artifact_registry)

    def test_compose_preserves_loopback_and_container_hardening(self) -> None:
        compose = read("templates/compose.gcp.yaml.tftpl")
        for contract in (
            "COREVIEW_METRICS_PROVIDER: synthetic",
            "host_ip: 127.0.0.1",
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

    def test_bootstrap_is_secretless_checksum_verified_and_deferred(self) -> None:
        bootstrap = read("templates/startup.sh.tftpl")
        variables = read("variables.tf")
        for contract in (
            'readonly bootstrap_marker="$install_root/.bootstrap-${bootstrap_revision}-complete"',
            'if [[ -f "$bootstrap_marker" ]]',
            "sha256sum --check --status",
            "initialize-lab-secrets",
            "read -r -s",
            "openssl rand -hex 32",
            "chmod 0600",
            "ConditionPathExists=/opt/coreview/.env",
            "systemctl enable coreview-lab.service",
            "Metadata-Flavor: Google",
            "oauth2accesstoken",
            "docker --config \"$docker_config\" pull \"$image_uri\"",
            "mkfs.ext4",
            "/var/lib/docker",
            "RequiresMountsFor=/var/lib/docker",
            "After=var-lib-docker.mount",
            "defaults,nodev,nosuid",
            "https://deb.debian.org",
            "https://security.debian.org",
            'install -m 0600 /dev/null "$bootstrap_marker"',
        ):
            self.assertIn(contract, bootstrap)
        self.assertNotIn("systemctl enable --now coreview-lab.service", bootstrap)
        self.assertNotIn("nofail", bootstrap)
        self.assertLess(
            bootstrap.index("systemctl daemon-reload"),
            bootstrap.index("systemctl enable --now docker.service"),
        )
        self.assertIn('variable "bootstrap_revision"', variables)
        self.assertIn("2.40.3", variables)
        self.assertIn(
            "dba9d98e1ba5bfe11d88c99b9bd32fc4a0624a30fafe68eea34d61a3e42fd372",
            variables,
        )
        self.assertNotRegex(bootstrap, r"AIza[0-9A-Za-z_-]{30,}")
        self.assertNotIn("BEGIN PRIVATE KEY", bootstrap)

        rendered_size_upper_bound = len(bootstrap.encode("utf-8"))
        rendered_size_upper_bound += len(
            base64.b64encode(read("templates/compose.gcp.yaml.tftpl").encode("utf-8"))
        )
        rendered_size_upper_bound += len(
            base64.b64encode((REPO_ROOT / "docker/servers.synthetic.json").read_bytes())
        )
        self.assertLess(rendered_size_upper_bound, 256 * 1024)

        result = subprocess.run(
            ["bash", "-n", str(GCP_ROOT / "templates" / "startup.sh.tftpl")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backend_and_operator_inputs_remain_out_of_git(self) -> None:
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn('backend "gcs" {}', read("backend.tf"))
        self.assertIn("replace-with-approved-unique-state-bucket", read("backend.example.hcl"))
        for ignored in (
            ".terraform/",
            "*.tfstate",
            "*.tfvars",
            "backend.hcl",
            "*.tfplan",
            "*.plan",
            "plan.out",
        ):
            self.assertIn(ignored, gitignore)

    def test_documentation_marks_real_cloud_work_as_not_executed(self) -> None:
        phase = (REPO_ROOT / "docs" / "PHASE_1_1.md").read_text(encoding="utf-8")
        readme = read("README.md")
        deployment = read("DEPLOYMENT_RUNBOOK.md")
        destroy = read("DESTROY_RUNBOOK.md")
        for contract in (
            "Real GCP plan: Not executed",
            "Workload deployment: Not executed",
            "Terraform: Static validation passed",
        ):
            self.assertIn(contract, phase)
        self.assertIn("IAP TCP forwarding", readme)
        self.assertIn("OS Login", readme)
        self.assertIn("Approval GCP-1", deployment)
        self.assertIn("terraform -chdir=infra/gcp apply", deployment)
        self.assertNotIn("terraform apply -auto-approve", deployment)
        self.assertIn("deletion_protection", destroy)
        self.assertIn("Artifact Registry", destroy)


if __name__ == "__main__":
    unittest.main()
