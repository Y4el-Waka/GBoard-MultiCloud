terraform {
  # The Storage Account and private blob container are bootstrapped separately.
  # Backend coordinates and Microsoft Entra authentication stay in ignored
  # backend.hcl/operator environment, never in this repository.
  backend "azurerm" {}
}
