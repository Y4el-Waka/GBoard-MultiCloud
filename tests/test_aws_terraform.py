from __future__ import annotations

import base64
import re
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AWS_ROOT = REPO_ROOT / "infra" / "aws"


def read(relative_name: str) -> str:
    return (AWS_ROOT / relative_name).read_text(encoding="utf-8")


class AWSTerraformPreparationTests(unittest.TestCase):
    def test_configuration_is_split_and_versions_are_exact(self) -> None:
        required = {
            ".terraform.lock.hcl",
            "backend.tf",
            "compute.tf",
            "ecr.tf",
            "iam.tf",
            "locals.tf",
            "networking.tf",
            "outputs.tf",
            "providers.tf",
            "variables.tf",
            "versions.tf",
        }
        self.assertTrue(all((AWS_ROOT / name).is_file() for name in required))

        versions = read("versions.tf")
        self.assertIn('required_version = "= 1.16.4"', versions)
        self.assertIn('version = "= 6.61.0"', versions)
        lock = read(".terraform.lock.hcl")
        self.assertIn('version     = "6.61.0"', lock)
        self.assertIn('constraints = "6.61.0"', lock)

    def test_scope_contains_only_the_expected_aws_resource_families(self) -> None:
        terraform = "\n".join(path.read_text(encoding="utf-8") for path in AWS_ROOT.glob("*.tf"))
        resources = set(re.findall(r'resource\s+"(aws_[^"]+)"', terraform))
        allowed = {
            "aws_ecr_repository",
            "aws_iam_instance_profile",
            "aws_iam_role",
            "aws_iam_role_policy",
            "aws_iam_role_policy_attachment",
            "aws_instance",
            "aws_internet_gateway",
            "aws_route",
            "aws_route_table",
            "aws_route_table_association",
            "aws_security_group",
            "aws_subnet",
            "aws_vpc",
            "aws_vpc_security_group_egress_rule",
        }
        self.assertEqual(resources, allowed)
        for forbidden in (
            "aws_eip",
            "aws_lb",
            "aws_nat_gateway",
            "aws_db_instance",
            "aws_ecs_cluster",
            "aws_eks_cluster",
            "aws_elasticache_cluster",
        ):
            self.assertNotIn(f'resource "{forbidden}"', terraform)

    def test_network_has_no_inbound_or_public_application_rule(self) -> None:
        networking = read("networking.tf")
        compute = read("compute.tf")
        self.assertIn("ingress = []", networking)
        self.assertNotIn('resource "aws_vpc_security_group_ingress_rule"', networking)
        self.assertNotRegex(networking, r"from_port\s*=\s*(?:22|5067|9090)")
        self.assertIn('destination_cidr_block = "0.0.0.0/0"', networking)
        self.assertIn("associate_public_ip_address = true", compute)
        self.assertIn("map_public_ip_on_launch = false", networking)

    def test_instance_hardening_storage_and_size_match_decision(self) -> None:
        compute = read("compute.tf")
        variables = read("variables.tf")
        self.assertIn('default     = "t3.small"', variables)
        self.assertIn("cpu_credits = \"standard\"", compute)
        self.assertIn('volume_type           = "gp3"', compute)
        self.assertIn("volume_size           = var.root_volume_size_gib", compute)
        self.assertIn("encrypted             = true", compute)
        self.assertIn("delete_on_termination = true", compute)
        self.assertIn('http_tokens                 = "required"', compute)
        self.assertIn("http_put_response_hop_limit = 1", compute)
        self.assertNotIn("key_name", compute)

    def test_iam_is_ssm_core_and_repository_scoped_pull_only(self) -> None:
        iam = read("iam.tf")
        self.assertIn("AmazonSSMManagedInstanceCore", iam)
        for action in (
            "ecr:GetAuthorizationToken",
            "ecr:BatchCheckLayerAvailability",
            "ecr:BatchGetImage",
            "ecr:GetDownloadUrlForLayer",
        ):
            self.assertIn(action, iam)
        for forbidden in ("ecr:PutImage", "ecr:DeleteRepository", "ecr:DeleteRepositoryPolicy"):
            self.assertNotIn(forbidden, iam)
        self.assertIn("resources = [aws_ecr_repository.coreview.arn]", iam)

    def test_ecr_retains_images_and_uses_immutable_scanned_encrypted_storage(self) -> None:
        ecr = read("ecr.tf")
        self.assertIn('image_tag_mutability = "IMMUTABLE"', ecr)
        self.assertIn("force_delete         = false", ecr)
        self.assertIn("scan_on_push = true", ecr)
        self.assertIn('encryption_type = "AES256"', ecr)

    def test_aws_compose_preserves_synthetic_loopback_security_contract(self) -> None:
        compose = read("templates/compose.aws.yaml.tftpl")
        for contract in (
            "COREVIEW_METRICS_PROVIDER: synthetic",
            "host_ip: 127.0.0.1",
            "read_only: true",
            "no-new-privileges:true",
            "cap_drop:",
            "network_mode: service:simulator",
            "coreview-runtime:/runtime",
            "coreview-uploads:/app/static/uploads",
        ):
            self.assertIn(contract, compose)
        self.assertEqual(compose.count("network_mode: service:simulator"), 2)
        self.assertNotIn("COREVIEW_METRICS_PROVIDER: prtg", compose)
        self.assertNotIn("build:", compose)

    def test_bootstrap_verifies_compose_and_defers_secret_entry_to_ssm(self) -> None:
        bootstrap = read("templates/user_data.sh.tftpl")
        variables = read("variables.tf")
        self.assertIn("sha256sum --check --status", bootstrap)
        self.assertIn("initialize-lab-secrets", bootstrap)
        self.assertIn("read -r -s", bootstrap)
        self.assertIn("^[A-Za-z0-9._-]+$", bootstrap)
        self.assertIn("openssl rand -hex 32", bootstrap)
        self.assertIn("chmod 0600", bootstrap)
        self.assertIn("ConditionPathExists=/opt/coreview/.env", bootstrap)
        self.assertIn("systemctl enable coreview-lab.service", bootstrap)
        self.assertNotIn("systemctl enable --now coreview-lab.service", bootstrap)
        self.assertIn("2.40.3", variables)
        self.assertIn("dba9d98e1ba5bfe11d88c99b9bd32fc4a0624a30fafe68eea34d61a3e42fd372", variables)

        rendered_size_upper_bound = len(bootstrap.encode("utf-8"))
        rendered_size_upper_bound += len(
            base64.b64encode(read("templates/compose.aws.yaml.tftpl").encode("utf-8"))
        )
        rendered_size_upper_bound += len(
            base64.b64encode((REPO_ROOT / "docker/servers.synthetic.json").read_bytes())
        )
        self.assertLess(rendered_size_upper_bound, 16 * 1024)

    def test_state_operator_inputs_and_plans_are_ignored(self) -> None:
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        backend = read("backend.tf")
        backend_example = read("backend.example.hcl")
        for ignored in (".terraform/", "*.tfstate", "*.tfvars", "backend.hcl", "*.tfplan"):
            self.assertIn(ignored, gitignore)
        self.assertIn('backend "s3" {}', backend)
        self.assertIn("use_lockfile = true", backend_example)
        self.assertIn("encrypt      = true", backend_example)

    def test_required_inputs_prevent_implicit_account_network_or_image(self) -> None:
        variables = read("variables.tf")
        providers = read("providers.tf")
        for variable in ("allowed_account_ids", "vpc_cidr", "image_tag"):
            block = re.search(
                rf'variable "{variable}" \{{(?P<body>.*?)\n\}}',
                variables,
                re.DOTALL,
            )
            self.assertIsNotNone(block, variable)
            self.assertNotIn("default", block.group("body"), variable)
        self.assertIn("allowed_account_ids = var.allowed_account_ids", providers)
        self.assertIn("cidrsubnet(var.vpc_cidr, 8, 0)", read("networking.tf"))

    def test_frontend_and_application_runtime_files_are_untouched_by_terraform(self) -> None:
        changed = subprocess.run(
            ["git", "diff", "--name-only", "--", "app.py", "monitor.py", "templates", "static"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(changed, "")


if __name__ == "__main__":
    unittest.main()
