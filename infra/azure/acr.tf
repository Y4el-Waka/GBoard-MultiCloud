resource "azurerm_container_registry" "coreview" {
  name                          = var.acr_name
  resource_group_name           = local.resource_group_name
  location                      = var.location
  sku                           = "Basic"
  admin_enabled                 = false
  anonymous_pull_enabled        = false
  public_network_access_enabled = true
  zone_redundancy_enabled       = false
  tags                          = local.common_tags

  lifecycle {
    prevent_destroy = true
  }
}
