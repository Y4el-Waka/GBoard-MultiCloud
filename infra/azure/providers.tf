provider "azurerm" {
  subscription_id = var.subscription_id

  # Provider registration is an explicit preflight responsibility. Terraform
  # must not mutate subscription-wide provider registration as a side effect.
  resource_provider_registrations = "none"

  features {
    resource_group {
      prevent_deletion_if_contains_resources = true
    }
  }
}
