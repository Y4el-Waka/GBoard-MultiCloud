output "resource_group_name" {
  description = "Resource Group containing the lab."
  value       = local.resource_group_name
}

output "virtual_network_id" {
  description = "CoreView lab VNet ID."
  value       = azurerm_virtual_network.lab.id
}

output "vm_id" {
  description = "Private CoreView VM resource ID used by Bastion commands."
  value       = azurerm_linux_virtual_machine.coreview.id
}

output "vm_name" {
  description = "Private CoreView VM name."
  value       = azurerm_linux_virtual_machine.coreview.name
}

output "vm_private_ip" {
  description = "Private VM address; not an Internet endpoint."
  value       = azurerm_network_interface.coreview.private_ip_address
}

output "bastion_name" {
  description = "Azure Bastion Standard host name."
  value       = azurerm_bastion_host.lab.name
}

output "acr_login_server" {
  description = "Private registry login server."
  value       = azurerm_container_registry.coreview.login_server
}

output "image_repository" {
  description = "ACR repository prefix. The approved digest is supplied later through the guest-only runtime initializer."
  value       = "${azurerm_container_registry.coreview.login_server}/coreview"
}
