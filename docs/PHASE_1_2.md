# Phase 1.2 — Azure architecture and Terraform

## Status

- Architecture: **Implemented**
- Terraform: **Static validation passed**
- Real Azure plan: **Not executed**
- Workload deployment: **Not executed**
- Azure credentials/subscription used: **No**

Phase 1.2A/1.2B adds an independent Azure blueprint without changing AWS, GCP or CoreView application/runtime behavior.

## Selected architecture

CoreView runs as the existing three-container synthetic lab on one private Azure Linux VM. `simulator`, `collector` and `app` preserve the measured Compose resource limits and shared simulator network namespace. The simulator and dashboard bind only to VM loopback.

```text
Operator on Zorin
  |
  | Microsoft Entra -> Azure Bastion Standard native TCP tunnel
  | SSH key -> Linux guest -> local forwards 5067 and 9090
  v
Azure VNet (eastus2)
  +-- AzureBastionSubnet (/26 or larger)
  |     +-- Bastion Standard + Standard public IP
  |
  +-- private workload subnet (implicit outbound disabled)
        +-- NSG: inbound SSH only from Bastion subnet
        +-- NAT Gateway + Standard public IP: explicit egress
        +-- Standard_B2s Ubuntu Gen2 VM (no public IP)
              +-- system managed identity -> AcrPull on one ACR
              +-- 32 GiB encrypted OS disk
              +-- 32 GiB protected encrypted runtime disk
              +-- Docker Compose synthetic stack by image digest

External to workload stack:
  Azure Blob state backend (Entra auth, versioning, soft delete)
```

## Resource inventory (static)

When `create_resource_group=true`, the stack declares:

1. Resource Group;
2. VNet;
3. workload subnet;
4. `AzureBastionSubnet`;
5. workload NSG and NIC association;
6. NAT public IP, NAT Gateway, IP association and subnet association;
7. Bastion public IP and Bastion Standard host;
8. VM NIC;
9. private Linux VM with system-assigned identity and managed OS disk;
10. separate runtime managed disk and attachment;
11. Azure Container Registry Basic;
12. registry-scoped `AcrPull` role assignment.

The Blob backend is deliberately not in this inventory. When an existing Resource Group is selected, Terraform reads it as data and does not own its lifecycle.

## Security and architecture matrix

| Area | Implemented decision | Limitation / future validation |
|---|---|---|
| Subscription safety | required UUID plus hard variable-validation allow-list guard | account/tenant permissions untested |
| Provider registration | AzureRM automatic registration disabled | required namespaces must be verified/registered separately |
| Inbound | no VM public IP; only TCP 22 from Bastion subnet; explicit deny overrides Azure default VNet ingress | real effective NSG routes not inspected |
| Dashboard/simulator | Compose binds 5067/9090 to loopback | browser smoke not run in Azure |
| Admin access | Bastion Standard native tunnel, then SSH local forward | Bastion/RBAC/CLI workflow not executed |
| Egress | private subnet plus explicit NAT; allow HTTPS, DNS, NTP and IMDS only | endpoint-specific firewall/Private Link not implemented |
| Compute | `Standard_B2s`, Trusted Launch, exact Ubuntu version input | SKU/image availability and quota untested |
| Storage | Standard SSD, Azure platform-managed encryption, separate protected runtime disk | CMK and backups out of scope |
| Identity | system managed identity; no key | role propagation and token exchange untested |
| Registry | ACR Basic, admin/anonymous disabled, guest-validated digest pull outside Terraform custom_data | public endpoint; registry-level rather than repository-level pull |
| Secrets | interactive post-provision initialization, root `0600` file | no Key Vault integration in this lab |
| State | external Blob, Entra auth, versioning/soft delete/firewall | backend not created or tested |
| Diagnostics | managed boot diagnostics and bounded local Docker logs | no Log Analytics/central retention |
| Destruction | `prevent_destroy` on RG/VM/runtime disk/ACR | guards require an explicit reviewed change for full teardown |

## Azure access decision

Microsoft's current SKU matrix says:

| Bastion SKU | Cost model | Browser | Native client/CLI | Private-only | Fit |
|---|---|---|---|---|---|
| Developer | free shared | yes | **no** | no | economical browser administration only; cannot support required Zorin tunnel |
| Basic | paid dedicated | yes | **no** | no | dedicated browser access, still no native tunnel |
| Standard | paid dedicated | yes | **yes** | no | **selected** for `az network bastion tunnel` |
| Premium | paid dedicated | yes | yes | yes | stronger compliance features, excessive for synthetic lab |

The main design is substantially more expensive than AWS Session Manager or GCP IAP because dedicated Bastion Standard dominates monthly cost. A cheaper direct-public-IP SSH option is documented but rejected as the default because it exposes a management endpoint.

Sources:

- <https://learn.microsoft.com/azure/bastion/bastion-sku-comparison>
- <https://learn.microsoft.com/azure/bastion/native-client>
- <https://learn.microsoft.com/azure/networking/design-guide/developer-admin-access>

## Runtime and bootstrap flow

1. Terraform creates infrastructure only after a future reviewed plan/apply.
2. cloud-init executes the secretless bootstrap.
3. Bootstrap installs an exact `docker.io` package and checksum-verified Compose binary.
4. The attached runtime disk is formatted only when blank, mounted at `/var/lib/docker`, and made mandatory for Docker through systemd.
5. The Compose file and sanitized 13-server synthetic configuration are installed.
6. `coreview-lab.service` is enabled but cannot start without `/opt/coreview/.env`.
7. A publisher pushes the exact CI-validated image and records its ACR digest.
8. An operator reaches the guest through Bastion and initializes secrets while supplying the approved `sha256` manifest digest; the initializer binds it to the expected ACR repository.
9. The VM identity exchanges an IMDS token for an ACR refresh token, validates the runtime image reference again, pulls the digest-qualified image and starts Compose.
10. `update-runtime-image` changes the digest atomically with configuration rollback and a controlled service restart, not a Terraform `custom_data` change or VM replacement.
11. Smoke tests use SSH local forwards for 5067 and diagnostic 9090 through the Bastion native tunnel.

An empty registry therefore does not create a boot loop or start CoreView prematurely.

## Identity separation

- Terraform operator: infrastructure/RBAC rights needed for the reviewed stack, plus backend data access.
- ACR publisher: registry push only; no VM administration.
- VM: system identity and `AcrPull` only on this registry.
- Administrative operator: Bastion tunnel authorization, read visibility for target resources, and guest SSH private key.
- Backend/recovery owner: state bootstrap, version recovery and final cleanup.

No permanent Service Principal key, registry password or application credential is versioned or passed in Terraform.

## AWS/GCP/Azure differences

| Capability | AWS blueprint | GCP blueprint | Azure blueprint |
|---|---|---|---|
| Private admin | SSM Session Manager | IAP TCP + OS Login | Bastion Standard native tunnel + SSH key |
| Inbound to VM | none through SG | SSH from IAP proxy range | SSH from Bastion subnet only |
| Explicit egress | public IPv4 route in initial lab | Cloud NAT | NAT Gateway on private subnet |
| Runtime identity | EC2 role | Compute service account | system-assigned managed identity |
| Registry | ECR | Artifact Registry | ACR Basic |
| Disk | encrypted gp3 | encrypted pd-balanced | encrypted Standard SSD LRS |
| State | existing S3 backend | proposed GCS backend | proposed Azure Blob backend |

These mechanisms provide analogous outcomes, not identical security boundaries. Bastion is a deployed hourly service; IAP and SSM use different control/data paths and authorization models.

## Cost summary

The dated estimate in `infra/azure/COST_ESTIMATE.md` is approximately:

- 24 hours: USD 9.64;
- one continuous week: USD 67.33;
- 730-hour month: USD 292.48.

Transfer, taxes, support, retained resources and optional logging are variable. Bastion, NAT, public IPs, disks, ACR and the backend can continue billing when the VM is stopped. Budgets are alerts, not hard limits.

## Validation matrix

| Evidence | Phase 1.2B status |
|---|---|
| Terraform format | passed |
| Provider initialization without backend | passed with AzureRM 5.7.0 |
| Terraform validate | passed |
| Azure structural/security unit tests | 13 passed |
| Shell template syntax | passed |
| Docker Compose contract | rendered and config validation passed; not run on Azure |
| Offline IaC scanner | Trivy embedded rules: 0 findings; online checks bundle unavailable by design |
| Existing Python/JavaScript regression | 315 Python and 18 JavaScript passed |
| Real Azure authentication | not performed |
| Real Terraform plan | not executed |
| Resource creation/workload smoke | not executed |

This table is updated only with commands actually run.

## Scope explicitly excluded

- portfolio website or frontend changes;
- application, collector, simulator, database or Metrics Engine changes;
- public dashboard, Load Balancer, Application Gateway or AKS;
- Key Vault, Private Link, Azure Firewall, Log Analytics and centralized backup;
- PostgreSQL, Redis or multichannel persistence;
- real PRTG connectivity;
- Azure deployment or credential request;
- OCI implementation.

## Requirements before a real deployment

1. authorized subscription and tenant plus allow-list values;
2. resource-provider registration and quota checks;
3. non-overlapping CIDR approval;
4. exact image and Docker package resolution;
5. globally unique ACR name;
6. external Blob backend bootstrap and access test;
7. Azure Policy/RBAC/Bastion entitlement review;
8. current cost estimate and budget alerts;
9. reviewed real Terraform plan;
10. exact CI image artifact, digest and publication approval;
11. Azure smoke, restart/persistence and destroy rehearsal.
