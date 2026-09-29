locals {
  name_prefix = "${var.project_name}-${var.environment}"

  resource_group_name = var.create_resource_group ? azurerm_resource_group.lab[0].name : data.azurerm_resource_group.existing[0].name

  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Owner       = var.logical_owner
      ManagedBy   = "terraform"
      DataClass   = "synthetic-only"
    },
    var.additional_tags,
  )

  runtime_compose = templatefile("${path.module}/templates/compose.azure.yaml.tftpl", {})

  bootstrap_script = templatefile("${path.module}/templates/bootstrap.sh.tftpl", {
    bootstrap_revision     = var.bootstrap_revision
    compose_config_b64     = base64encode(local.runtime_compose)
    docker_compose_sha256  = var.docker_compose_sha256
    docker_compose_version = var.docker_compose_version
    docker_package_version = var.docker_package_version
    registry_host          = azurerm_container_registry.coreview.login_server
    servers_config_b64     = base64encode(file("${path.module}/../../docker/servers.synthetic.json"))
  })
}
