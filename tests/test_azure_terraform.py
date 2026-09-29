from __future__ import annotations

import base64
import re
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AZURE_ROOT = REPO_ROOT / "infra" / "azure"


def read(relative_name: str) -> str:
    return (AZURE_ROOT / relative_name).read_text(encoding="utf-8")


class AzureTerraformPreparationTests(unittest.TestCase):
    def test_configuration_is_split_and_versions_are_exact(self) -> None:
        required = {
            "acr.tf",
            "backend.tf",
            "compute.tf",
            "iam.tf",
            "locals.tf",
            "networking.tf",
            "outputs.tf",
            "providers.tf",
            "resource_group.tf",
            "variables.tf",
            "versions.tf",
        }
        self.assertTrue(all((AZURE_ROOT / name).is_file() for name in required))

        versions = read("versions.tf")
        self.assertIn('required_version = "= 1.16.4"', versions)
        self.assertIn('version = "= 5.7.0"', versions)

    def test_scope_contains_only_reviewed_azure_resource_types(self) -> None:
        terraform = "\n".join(
            path.read_text(encoding="utf-8") for path in AZURE_ROOT.glob("*.tf")
        )
        resources = set(re.findall(r'resource\s+"(azurerm_[^"]+)"', terraform))
        self.assertEqual(
            resources,
            {
                "azurerm_bastion_host",
                "azurerm_container_registry",
                "azurerm_linux_virtual_machine",
                "azurerm_managed_disk",
                "azurerm_nat_gateway",
                "azurerm_nat_gateway_public_ip_association",
                "azurerm_network_interface",
                "azurerm_network_interface_security_group_association",
                "azurerm_network_security_group",
                "azurerm_public_ip",
                "azurerm_resource_group",
                "azurerm_role_assignment",
                "azurerm_subnet",
                "azurerm_subnet_nat_gateway_association",
                "azurerm_virtual_machine_data_disk_attachment",
                "azurerm_virtual_network",
            },
        )
        for forbidden in (
            "azurerm_kubernetes_cluster",
            "azurerm_lb",
            "azurerm_application_gateway",
            "azurerm_firewall",
            "azurerm_postgresql",
            "azurerm_redis",
            "azurerm_key_vault_secret",
        ):
            self.assertNotIn(f'resource "{forbidden}', terraform)

    def test_subscription_guard_and_required_inputs_are_explicit(self) -> None:
        providers = read("providers.tf")
        variables = read("variables.tf")
        self.assertIn("subscription_id = var.subscription_id", providers)
        self.assertIn('resource_provider_registrations = "none"', providers)
        self.assertIn(
            "contains(var.allowed_subscription_ids, var.subscription_id)", variables
        )
        for variable in (
            "subscription_id",
            "allowed_subscription_ids",
            "resource_group_name",
            "vnet_cidr",
            "workload_subnet_cidr",
            "bastion_subnet_cidr",
            "admin_ssh_public_key",
            "source_image",
            "acr_name",
            "docker_package_version",
        ):
            block = re.search(
                rf'variable "{variable}" \{{(?P<body>.*?)\n\}}',
                variables,
                re.DOTALL,
            )
            self.assertIsNotNone(block, variable)
            self.assertNotIn("default", block.group("body"), variable)

    def test_resource_group_ownership_modes_and_guards_are_explicit(self) -> None:
        resource_group = read("resource_group.tf")
        locals_text = read("locals.tf")
        self.assertIn("count = var.create_resource_group ? 1 : 0", resource_group)
        self.assertIn("count = var.create_resource_group ? 0 : 1", resource_group)
        self.assertIn("prevent_destroy = true", resource_group)
        self.assertIn("var.create_resource_group ? azurerm_resource_group.lab[0].name", locals_text)

    def test_vm_is_private_and_ingress_is_bastion_only(self) -> None:
        networking = read("networking.tf")
        compute = read("compute.tf")
        self.assertIn("default_outbound_access_enabled = false", networking)
        self.assertIn('name                 = "AzureBastionSubnet"', networking)
        self.assertIn('destination_port_range     = "22"', networking)
        self.assertIn("source_address_prefix      = var.bastion_subnet_cidr", networking)
        self.assertIn('name                       = "DenyOtherInbound"', networking)
        self.assertNotRegex(networking, r'destination_port_range\s*=\s*"(?:5067|9090)"')
        self.assertNotIn("public_ip_address_id", compute)
        self.assertIn("azurerm_nat_gateway", networking)
        self.assertIn('name                       = "DenyOtherOutbound"', networking)

    def test_bastion_standard_supports_native_tunnel(self) -> None:
        networking = read("networking.tf")
        self.assertIn('sku                 = "Standard"', networking)
        self.assertIn("scale_units         = 2", networking)
        self.assertIn("tunneling_enabled   = true", networking)
        self.assertIn("copy_paste_enabled  = false", networking)
        self.assertIn("file_copy_enabled   = false", networking)

        adr = (REPO_ROOT / "docs" / "adr" / "ADR-020-azure-private-single-vm-lab.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Developer and Basic cannot support this CLI/native-client flow", adr)
        self.assertIn("Premium supports it", adr)

    def test_compute_storage_and_identity_are_hardened(self) -> None:
        compute = read("compute.tf")
        iam = read("iam.tf")
        for contract in (
            "disable_password_authentication = true",
            "secure_boot_enabled             = true",
            "vtpm_enabled                    = true",
            "ip_forwarding_enabled          = false",
            'storage_account_type = "StandardSSD_LRS"',
            "prevent_destroy = true",
            'type = "SystemAssigned"',
            "boot_diagnostics {}",
        ):
            self.assertIn(contract, compute)
        self.assertIn('role_definition_name = "AcrPull"', iam)
        self.assertIn("scope                = azurerm_container_registry.coreview.id", iam)
        for forbidden in ("Contributor", "Owner", "AcrPush"):
            self.assertNotIn(forbidden, iam)

    def test_registry_has_no_admin_or_anonymous_access(self) -> None:
        acr = read("acr.tf")
        self.assertIn('sku                           = "Basic"', acr)
        self.assertIn("admin_enabled                 = false", acr)
        self.assertIn("anonymous_pull_enabled        = false", acr)
        self.assertIn("prevent_destroy = true", acr)
        self.assertNotIn("admin_password", acr)

    def test_image_digest_is_runtime_only_and_host_dependencies_are_pinned(self) -> None:
        variables = read("variables.tf")
        locals_text = read("locals.tf")
        outputs = read("outputs.tf")
        compose = read("templates/compose.azure.yaml.tftpl")
        bootstrap = read("templates/bootstrap.sh.tftpl")
        self.assertIn('var.source_image.version != "latest"', variables)
        self.assertNotIn('variable "image_digest"', variables)
        self.assertNotIn('variable "image_tag"', variables)
        self.assertNotIn("image_digest", locals_text)
        self.assertNotIn("image_uri", locals_text)
        self.assertIn('output "image_repository"', outputs)
        self.assertIn("$${COREVIEW_IMAGE:?", compose)
        self.assertIn("COREVIEW_IMAGE must reference the expected ACR repository", bootstrap)
        self.assertIn("^sha256:[0-9a-f]{64}$", bootstrap)
        self.assertIn('default     = "2.40.3"', variables)
        self.assertIn(
            "dba9d98e1ba5bfe11d88c99b9bd32fc4a0624a30fafe68eea34d61a3e42fd372",
            variables,
        )

    def test_compose_preserves_loopback_shared_namespace_and_hardening(self) -> None:
        compose = read("templates/compose.azure.yaml.tftpl")
        for contract in (
            "COREVIEW_METRICS_PROVIDER: synthetic",
            "host_ip: 127.0.0.1",
            "$${COREVIEW_IMAGE:?Set COREVIEW_IMAGE to an approved ACR digest URI}",
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

    def test_bootstrap_is_idempotent_secretless_and_fail_closed(self) -> None:
        bootstrap = read("templates/bootstrap.sh.tftpl")
        for contract in (
            'readonly bootstrap_marker="$install_root/.bootstrap-${bootstrap_revision}-complete"',
            'if [[ -f "$bootstrap_marker" ]]',
            'readonly runtime_disk="/dev/disk/azure/scsi1/lun0"',
            "systemctl mask docker.service docker.socket",
            "wipefs --noheadings",
            "Runtime disk filesystem is not ext4",
            "systemctl unmask docker.service docker.socket",
            "COREVIEW_IMAGE=%s",
            "update-runtime-image",
            "Image update failed; the previous runtime configuration was restored.",
            "cp --preserve=mode,ownership",
            '"docker.io=${docker_package_version}"',
            "sha256sum --check --status",
            "RequiresMountsFor=/var/lib/docker",
            "defaults,nodev,nosuid",
            "initialize-lab-secrets",
            "read -r -s",
            "openssl rand -hex 32",
            "chmod 0600",
            "ConditionPathExists=/opt/coreview/.env",
            "systemctl enable coreview-lab.service",
            "Metadata: true",
            "/oauth2/exchange",
            "docker --config \"$docker_config\" pull \"$image_uri\"",
            'install -m 0600 /dev/null "$bootstrap_marker"',
        ):
            self.assertIn(contract, bootstrap)
        self.assertNotIn("systemctl enable --now coreview-lab.service", bootstrap)
        self.assertNotIn("nofail", bootstrap)
        self.assertNotIn("BEGIN PRIVATE KEY", bootstrap)
        self.assertNotIn("client_secret", bootstrap.lower())
        self.assertNotRegex(bootstrap.lower(), r'(?:password|passphrase)\s*=\s*["\'][^$]')

        rendered_size_upper_bound = len(bootstrap.encode("utf-8"))
        rendered_size_upper_bound += len(
            base64.b64encode(read("templates/compose.azure.yaml.tftpl").encode("utf-8"))
        )
        rendered_size_upper_bound += len(
            base64.b64encode((REPO_ROOT / "docker/servers.synthetic.json").read_bytes())
        )
        self.assertLess(rendered_size_upper_bound, 64 * 1024)

        result = subprocess.run(
            ["bash", "-n", str(AZURE_ROOT / "templates" / "bootstrap.sh.tftpl")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backend_is_external_entra_authenticated_and_ignored(self) -> None:
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn('backend "azurerm" {}', read("backend.tf"))
        backend_example = read("backend.example.hcl")
        backend_runbook = read("STATE_BACKEND.md")
        self.assertIn("use_azuread_auth     = true", backend_example)
        self.assertIn("use_cli              = true", backend_example)
        self.assertNotIn("access_key", backend_example)
        self.assertIn("az storage container-rm create", backend_runbook)
        self.assertNotIn("az storage container create", backend_runbook)
        for ignored in (
            ".terraform/",
            "*.tfstate",
            "*.tfvars",
            "backend.hcl",
            "*.tfplan",
        ):
            self.assertIn(ignored, gitignore)

    def test_documentation_is_honest_about_cloud_validation(self) -> None:
        phase = (REPO_ROOT / "docs" / "PHASE_1_2.md").read_text(encoding="utf-8")
        deployment = read("DEPLOYMENT_RUNBOOK.md")
        destroy = read("DESTROY_RUNBOOK.md")
        for contract in (
            "Real Azure plan: **Not executed**",
            "Workload deployment: **Not executed**",
            "Azure credentials/subscription used: **No**",
        ):
            self.assertIn(contract, phase)
        self.assertIn("Approval AZ-1", deployment)
        self.assertIn("az network bastion tunnel", deployment)
        self.assertIn("-L 5067:127.0.0.1:5067", deployment)
        self.assertIn("-L 9090:127.0.0.1:9090", deployment)
        self.assertIn("terraform -chdir=infra/azure apply", deployment)
        self.assertNotIn("terraform apply -auto-approve", deployment)
        self.assertIn("prevent_destroy", destroy)
        self.assertIn("Residual-cost audit", destroy)


if __name__ == "__main__":
    unittest.main()
