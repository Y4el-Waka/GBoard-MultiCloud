# Runtime pull is the VM identity's only Azure data-plane permission. The
# registry remains in RBAC-only mode for this first lab, so AcrPull is scoped to
# this registry and grants no subscription-wide rights.
resource "azurerm_role_assignment" "runtime_acr_pull" {
  scope                = azurerm_container_registry.coreview.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_linux_virtual_machine.coreview.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}
