resource "azurerm_network_interface" "coreview" {
  name                           = "${local.name_prefix}-nic"
  location                       = var.location
  resource_group_name            = local.resource_group_name
  accelerated_networking_enabled = false
  ip_forwarding_enabled          = false
  tags                           = local.common_tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.workload.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_network_interface_security_group_association" "coreview" {
  network_interface_id      = azurerm_network_interface.coreview.id
  network_security_group_id = azurerm_network_security_group.workload.id
}

resource "azurerm_managed_disk" "runtime" {
  name                 = "${local.name_prefix}-runtime"
  location             = var.location
  resource_group_name  = local.resource_group_name
  storage_account_type = "StandardSSD_LRS"
  create_option        = "Empty"
  disk_size_gb         = var.runtime_disk_size_gib
  tags                 = local.common_tags

  # Azure Managed Disks use server-side encryption with platform-managed keys
  # by default. A future CMK design must include Key Vault recovery ownership.
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_linux_virtual_machine" "coreview" {
  name                            = "${local.name_prefix}-host"
  computer_name                   = "coreviewlab"
  location                        = var.location
  resource_group_name             = local.resource_group_name
  size                            = var.vm_size
  admin_username                  = var.admin_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.coreview.id]
  secure_boot_enabled             = true
  vtpm_enabled                    = true
  encryption_at_host_enabled      = false
  provision_vm_agent              = true
  allow_extension_operations      = false
  custom_data                     = base64encode(local.bootstrap_script)
  tags                            = local.common_tags

  admin_ssh_key {
    username   = var.admin_username
    public_key = trimspace(var.admin_ssh_public_key)
  }

  identity {
    type = "SystemAssigned"
  }

  os_disk {
    name                 = "${local.name_prefix}-os"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = var.boot_disk_size_gib
  }

  source_image_reference {
    publisher = var.source_image.publisher
    offer     = var.source_image.offer
    sku       = var.source_image.sku
    version   = var.source_image.version
  }

  boot_diagnostics {}

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [
    azurerm_subnet_nat_gateway_association.workload,
    azurerm_network_interface_security_group_association.coreview,
  ]
}

resource "azurerm_virtual_machine_data_disk_attachment" "runtime" {
  managed_disk_id    = azurerm_managed_disk.runtime.id
  virtual_machine_id = azurerm_linux_virtual_machine.coreview.id
  lun                = 0
  caching            = "ReadWrite"
}
