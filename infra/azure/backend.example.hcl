# Copy to ignored backend.hcl only after the external backend is approved.
resource_group_name  = "replace-with-backend-resource-group"
storage_account_name = "replacewithuniquestateaccount"
container_name       = "tfstate"
key                  = "coreview-cloud-lab/azure/lab/terraform.tfstate"
use_azuread_auth     = true
use_cli              = true
