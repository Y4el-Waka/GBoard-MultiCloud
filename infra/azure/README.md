# CoreView Cloud Lab on Azure

This directory contains the Phase 1.2A/1.2B frozen Azure blueprint. It targets one existing subscription and creates or adopts one Resource Group; it never creates a subscription.

Status:

- architecture implemented;
- offline Terraform validation passed;
- no Azure authentication used;
- no real plan or deployment executed.

## Architecture

```text
Zorin laptop
  az login + Bastion authorization
       |
       | az network bastion tunnel -> VM:22
       | ssh -L 5067:localhost:5067 and -L 9090:localhost:9090
       v
Azure Bastion Standard (dedicated subnet, public control endpoint)
       |
       | private VNet / TCP 22 only
       v
Private Standard_B2s VM -- system identity -- AcrPull --> private ACR
       |
       +-- /var/lib/docker on protected Standard SSD runtime disk
       |
       +-- simulator :9090 (VM loopback only)
       +-- collector (simulator network namespace)
       +-- app :5067 (VM loopback only)
       |
       +-- HTTPS/DNS/NTP egress --> NAT Gateway --> Internet
```

No rule exposes SSH, 5067 or 9090 directly to the Internet. Bastion Standard is used because Developer and Basic do not support Azure CLI/native-client connections. See [ADR-020](../../docs/adr/ADR-020-azure-private-single-vm-lab.md).

## File map

| File | Responsibility |
|---|---|
| `versions.tf`, `.terraform.lock.hcl` | exact Terraform/provider versions |
| `providers.tf` | subscription selection and allow-list guard |
| `resource_group.tf` | managed or pre-existing RG ownership model |
| `networking.tf` | VNet, private subnet, NSG, NAT and Bastion Standard |
| `acr.tf`, `iam.tf` | private ACR and registry-scoped VM pull identity |
| `compute.tf` | hardened private VM and protected runtime disk |
| `templates/` | secretless bootstrap and Compose runtime topology |
| `backend.tf`, `backend.example.hcl` | external Blob backend contract |
| `COST_ESTIMATE.md` | dated public-price estimate |
| `STATE_BACKEND.md` | backend bootstrap/recovery procedure |
| `DEPLOYMENT_RUNBOOK.md` | future approval-gated deployment |
| `DESTROY_RUNBOOK.md` | teardown and residual-resource procedure |

## Required future inputs

Copy `terraform.tfvars.example` to ignored `terraform.tfvars` only for an authorized plan. Required inputs include:

- subscription UUID and an allow-list containing it;
- RG ownership choice;
- approved, non-overlapping RFC1918 VNet/workload/Bastion CIDRs;
- operator SSH **public** key;
- exact Canonical Ubuntu image version;
- globally unique ACR name;
- exact `docker.io` apt package version available in the pinned image.

No private key, password, token, ACR credential or application secret belongs in Terraform.
The full Git SHA and verified registry manifest digest are supplied only during controlled post-provision publication and guest initialization; they do not enter Terraform state or VM `custom_data`.

## Offline validation

```bash
terraform -chdir=infra/azure fmt -check -recursive
terraform -chdir=infra/azure init -backend=false -input=false
terraform -chdir=infra/azure validate
.venv/bin/python -m unittest tests.test_azure_terraform -v
```

These checks do not prove subscription permissions, resource-provider registration, quota, region/SKU availability, Azure Policy compliance, image availability or runtime behavior.

## Runtime safety contract

- `COREVIEW_METRICS_PROVIDER=synthetic`; the real PRTG provider is not selected.
- `app` and `collector` share `simulator` networking.
- host bindings remain `127.0.0.1`.
- containers are non-root, read-only, capability-free and `no-new-privileges`.
- the runtime image is digest-qualified.
- the VM identity receives only registry-scoped `AcrPull`.
- bootstrap installs and enables Docker but does not enable/start CoreView until `/opt/coreview/.env` exists.
- `/opt/coreview/bin/initialize-lab-secrets sha256:<approved-digest>` validates and binds the image to this ACR, creates secrets interactively with mode `0600`, and then starts the service.
- `/opt/coreview/bin/update-runtime-image sha256:<approved-digest>` performs an atomic guest-only image update with automatic configuration rollback on failure.

The registry endpoint remains Internet-reachable in the Basic SKU, but requires Microsoft Entra/RBAC authentication. A network-private registry would require a separately costed Premium/Private Link/DNS design.
