# Future Azure deployment runbook

**Not executed in Phase 1.2A or 1.2B.** Every mutating section requires explicit approval and an authorized subscription. Do not paste identifiers, tokens or private keys into tickets, logs or this repository.

## Roles separated by responsibility

| Principal | Minimum intended scope |
|---|---|
| Backend bootstrap owner | create/configure the dedicated state RG/Storage Account and assign state access |
| Terraform operator | create listed lab resources and RBAC assignment only in the approved RG/subscription; Blob data contributor on the state container |
| Image publisher | ACR push on the CoreView registry; no VM administration |
| VM managed identity | `AcrPull` on the CoreView registry only |
| Administrative operator | read Bastion/VM/NIC, use Bastion native tunnel, and possess the matching SSH private key |
| Recovery operator | approved access to state versions and protected residual resources |

No service-principal secret, ACR password or access key is part of this design. For future automation prefer workload identity federation, not a client secret.

## Approval AZ-1: identity and subscription guard

Future read-only preflight:

```bash
az login
az account show --query '{subscription:id,tenant:tenantId,state:state}' --output json
az account set --subscription AUTHORIZED_SUBSCRIPTION_UUID
az provider show --namespace Microsoft.Compute --query registrationState --output tsv
az provider show --namespace Microsoft.Network --query registrationState --output tsv
az provider show --namespace Microsoft.ContainerRegistry --query registrationState --output tsv
az provider show --namespace Microsoft.Storage --query registrationState --output tsv
```

Provider registration is not performed implicitly by Terraform. Registering a provider is subscription-wide and needs separate approval if one is absent.

Create ignored `infra/azure/terraform.tfvars` from the example. Verify the subscription is in `allowed_subscription_ids` and that no ID is copied into Git.

## Approval AZ-2: network, quota and costs

1. Confirm VNet/workload/Bastion CIDRs do not overlap existing VNets, VPNs or on-premises ranges.
2. Verify `Standard_B2s`, Bastion Standard, NAT Gateway, Standard public IP and ACR Basic availability/quota in `eastus2`.
3. Review [COST_ESTIMATE.md](COST_ESTIMATE.md) and configure budget alerts separately.
4. Confirm the workload subnet is private and the planned NSG has no inbound rule for 5067 or 9090.

## Approval AZ-3: resolve immutable host inputs

List exact Canonical image versions without using `latest`:

```bash
az vm image list \
  --location eastus2 \
  --publisher Canonical \
  --offer 0001-com-ubuntu-server-jammy \
  --sku 22_04-lts-gen2 \
  --all \
  --query 'sort_by([], &version)[-10:].{version:version,urn:urn}' \
  --output table
```

Record a reviewed exact version in ignored tfvars. Resolve the exact `docker.io` apt version from the same Ubuntu release repositories in a controlled ephemeral environment, verify availability, and set `docker_package_version`. Do not substitute a floating package expression.

Generate a dedicated operator key locally if needed:

```bash
ssh-keygen -t ed25519 -f "$HOME/.ssh/coreview-azure-lab" -C coreview-azure-lab
```

Only the `.pub` content goes into ignored tfvars. The private key remains local with restrictive permissions.

## Approval AZ-4: backend and plan

Bootstrap the state backend using [STATE_BACKEND.md](STATE_BACKEND.md), then:

```bash
terraform -chdir=infra/azure init -reconfigure -backend-config=backend.hcl
terraform -chdir=infra/azure fmt -check -recursive
terraform -chdir=infra/azure validate
terraform -chdir=infra/azure plan -input=false -out=/tmp/coreview-azure-reviewed.tfplan
terraform -chdir=infra/azure show -no-color /tmp/coreview-azure-reviewed.tfplan
```

Review the complete plan: subscription, RG ownership, zero VM public IP, only Bastion-to-VM SSH, private subnet, explicit NAT, disk encryption, identity scope, ACR admin disabled, exact image version and no secrets/custom data leakage. The plan is sensitive and must not be committed.

## Approval AZ-5: infrastructure apply

Only after approving the exact saved plan:

```bash
terraform -chdir=infra/azure apply /tmp/coreview-azure-reviewed.tfplan
```

Bootstrap installs the host prerequisites and stops. `coreview-lab.service` is enabled but gated by `ConditionPathExists=/opt/coreview/.env`; no image pull or CoreView process begins yet.

## Approval AZ-6: publish the exact CI image

Load the private CI artifact, verify its archive checksum and Image ID against evidence from the same run, then authenticate with the human publisher identity:

```bash
az acr login --name APPROVED_ACR_NAME
docker tag coreview-cloud-lab:0.5c APPROVED_ACR_LOGIN_SERVER/coreview:FULL_40_CHARACTER_GIT_SHA
docker push APPROVED_ACR_LOGIN_SERVER/coreview:FULL_40_CHARACTER_GIT_SHA
az acr repository show \
  --name APPROVED_ACR_NAME \
  --image coreview:FULL_40_CHARACTER_GIT_SHA \
  --query digest --output tsv
```

The digest is passed later to the guest initializer and does not enter Terraform, state or `custom_data`; publishing a new approved digest therefore does not replace the VM. Preserve the digest as deployment evidence and do not start CoreView until it is available.

## Administrative access from Zorin OS

Prerequisites: current Azure CLI with the Bastion extension/command support, an authorized Entra session, Reader visibility for Bastion/VM/NIC, Bastion tunnel permission, and the local SSH private key.

Terminal 1 creates a native Bastion tunnel to SSH:

```bash
az network bastion tunnel \
  --name APPROVED_BASTION_NAME \
  --resource-group APPROVED_RESOURCE_GROUP \
  --target-resource-id APPROVED_VM_RESOURCE_ID \
  --resource-port 22 \
  --port 22022
```

Terminal 2 authenticates to the guest and forwards the loopback dashboard:

```bash
ssh -i "$HOME/.ssh/coreview-azure-lab" \
  -p 22022 \
  -N \
  -L 5067:127.0.0.1:5067 \
  -L 9090:127.0.0.1:9090 \
  coreviewadmin@127.0.0.1
```

Open `http://127.0.0.1:5067` locally. Port 9090 is forwarded only for explicit synthetic PRTG diagnostics; it remains bound to VM loopback and is not an Azure public listener. The Azure CLI login authorizes use of Bastion; the SSH key separately authenticates the Linux account. Developer and Basic Bastion do not support this native-client procedure.

## Approval AZ-7: initialize secrets and start

In an interactive administrative SSH session through Bastion:

```bash
sudo /opt/coreview/bin/initialize-lab-secrets sha256:APPROVED_64_HEX_MANIFEST_DIGEST
sudo systemctl status coreview-lab.service --no-pager
sudo docker compose --env-file /opt/coreview/.env --file /opt/coreview/compose.yaml ps
```

The script prompts without echo, generates `APP_SECRET_KEY` locally, writes `/opt/coreview/.env` as root mode `0600`, and starts the systemd service. It refuses to overwrite an existing secrets file.

## Synthetic smoke

Through the local tunnel, verify authentication, dashboard, 13 logical servers, 1000 sensors, zero collector errors, metadata v2, Ping/CPU/Memory/Traffic/SFP history, pause/resume and legacy fallback. Confirm:

```bash
sudo docker inspect coreview-cloud-lab-simulator-1 --format '{{.Config.Image}}'
sudo ss -lnt
sudo journalctl -u coreview-lab.service --no-pager --lines 200
```

The image reference must contain the approved digest; host listeners 5067/9090 must be loopback-only. Sanitize all evidence before preservation.

## Controlled image update without VM replacement

After publishing and verifying a newer CI image digest, update only the guest runtime configuration:

```bash
sudo /opt/coreview/bin/update-runtime-image sha256:APPROVED_NEW_64_HEX_MANIFEST_DIGEST
```

The updater accepts only the expected ACR repository, atomically replaces only `COREVIEW_IMAGE`, restarts the Compose service and restores the previous root-owned environment if the new pull/start fails. It does not modify Terraform, `custom_data`, the VM or application secrets. Preserve both old and new digests as deployment evidence.

## Stop and restart

Workload only:

```bash
sudo systemctl stop coreview-lab.service
sudo systemctl start coreview-lab.service
```

VM deallocation stops VM compute charges but leaves Bastion, NAT, public IPs, disks, ACR and state billable:

```bash
az vm deallocate --resource-group APPROVED_RESOURCE_GROUP --name APPROVED_VM_NAME
az vm start --resource-group APPROVED_RESOURCE_GROUP --name APPROVED_VM_NAME
```

Use [DESTROY_RUNBOOK.md](DESTROY_RUNBOOK.md) for full teardown.
