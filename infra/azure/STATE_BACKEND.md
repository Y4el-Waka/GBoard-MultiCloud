# Azure Blob Terraform state backend

The backend is intentionally outside the workload stack. Nothing in Phase 1.2A or 1.2B creates it. The commands below are a future, separately approved bootstrap procedure and use the operator's Microsoft Entra identity; no account key or client secret is written to disk.

## Security properties

- dedicated Resource Group and globally unique Storage Account;
- Standard LRS `StorageV2` account;
- HTTPS-only, minimum TLS 1.2 and shared-key access disabled;
- public blob/container access disabled;
- Storage firewall default deny, with only the approved operator/runner source prefix admitted;
- private container named `tfstate`;
- Microsoft-managed encryption at rest;
- blob versioning, 30-day soft delete and 7-day container soft delete;
- Terraform state locking through the Azure Blob lease mechanism;
- Microsoft Entra data-plane authorization (`use_azuread_auth=true` and `use_cli=true`).

The current state version does not expire automatically. Old versions must be reviewed before applying any lifecycle deletion policy.

## Separate bootstrap (future, mutating)

Set values in the shell without recording them in history. The account name must be globally unique and contain only lowercase letters and digits.

```bash
export ARM_SUBSCRIPTION_ID='AUTHORIZED_SUBSCRIPTION_UUID'
export TFSTATE_RG='APPROVED_BACKEND_RESOURCE_GROUP'
export TFSTATE_ACCOUNT='GLOBALLY_UNIQUE_STORAGE_ACCOUNT'
export TFSTATE_CONTAINER='tfstate'
export OPERATOR_SOURCE_PREFIX='APPROVED_PUBLIC_SOURCE_PREFIX'

az account set --subscription "$ARM_SUBSCRIPTION_ID"
az group create --name "$TFSTATE_RG" --location eastus2 \
  --tags Project=coreview-cloud-lab Environment=lab DataClass=terraform-state

az storage account create \
  --name "$TFSTATE_ACCOUNT" \
  --resource-group "$TFSTATE_RG" \
  --location eastus2 \
  --sku Standard_LRS \
  --kind StorageV2 \
  --https-only true \
  --min-tls-version TLS1_2 \
  --allow-blob-public-access false \
  --allow-shared-key-access false \
  --public-network-access Enabled \
  --default-action Deny \
  --tags Project=coreview-cloud-lab Environment=lab DataClass=terraform-state

az storage account network-rule add \
  --resource-group "$TFSTATE_RG" \
  --account-name "$TFSTATE_ACCOUNT" \
  --ip-address "$OPERATOR_SOURCE_PREFIX"

az storage container-rm create \
  --name "$TFSTATE_CONTAINER" \
  --storage-account "$TFSTATE_ACCOUNT" \
  --resource-group "$TFSTATE_RG" \
  --public-access off

az storage account blob-service-properties update \
  --resource-group "$TFSTATE_RG" \
  --account-name "$TFSTATE_ACCOUNT" \
  --enable-versioning true \
  --enable-delete-retention true \
  --delete-retention-days 30 \
  --enable-container-delete-retention true \
  --container-delete-retention-days 7
```

The `container-rm` command deliberately creates the container through Azure Resource Manager, so the bootstrap operator does not need a temporary Storage data-plane role merely to create it. Grant the normal Terraform operator `Storage Blob Data Contributor` only at the resulting `tfstate` container scope. Backend bootstrap/ownership permissions remain separate. Confirm the exact container resource ID with `az storage account show` and `az storage container-rm show`; do not paste subscription IDs into versioned files.

## Backend configuration

Copy `backend.example.hcl` to ignored `backend.hcl` and substitute only non-secret coordinates:

```hcl
resource_group_name  = "APPROVED_BACKEND_RESOURCE_GROUP"
storage_account_name = "GLOBALLY_UNIQUE_STORAGE_ACCOUNT"
container_name       = "tfstate"
key                  = "coreview-cloud-lab/azure/lab/terraform.tfstate"
use_azuread_auth     = true
use_cli              = true
```

Initialize only after an approval gate:

```bash
terraform -chdir=infra/azure init -reconfigure -backend-config=backend.hcl
```

HashiCorp documents Microsoft Entra authentication as recommended and Azure Blob native locking/consistency at <https://developer.hashicorp.com/terraform/language/backend/azurerm>.

## Verification and recovery

Read-only checks:

```bash
az storage account show --name "$TFSTATE_ACCOUNT" --resource-group "$TFSTATE_RG"
az storage account network-rule list --account-name "$TFSTATE_ACCOUNT" --resource-group "$TFSTATE_RG"
az storage blob list --account-name "$TFSTATE_ACCOUNT" --container-name tfstate --auth-mode login --include v --output table
```

For recovery, stop all Terraform writers, identify a known-good blob version, copy the current state to an external encrypted recovery location, then promote/download the selected version using Entra-authenticated Blob operations. Run `terraform state pull`, `terraform plan -refresh-only`, and a normal reviewed plan before any apply. Never edit state manually.

## Independent destruction

`terraform destroy` for the workload does not delete this backend. After the workload is gone and retention approval exists:

1. inventory all current and previous state versions;
2. export a final encrypted recovery copy;
3. remove operator/automation role assignments;
4. delete the container and observe soft-delete retention;
5. delete the Storage Account and backend Resource Group only with separate approval;
6. verify no retained account, private endpoint, diagnostic setting or lock remains billable.

Backend storage and operation costs are tiny for one state file, but the account and retained versions persist until independently removed.
