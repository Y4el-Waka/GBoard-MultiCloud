locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Owner       = var.logical_owner
      ManagedBy   = "terraform"
      DataClass   = "synthetic-only"
    },
    var.additional_freeform_tags,
  )

  ocir_host        = "${var.ocir_region_key}.ocir.io"
  image_repository = "${local.ocir_host}/${oci_artifacts_container_repository.coreview.namespace}/${var.container_repository_name}"

  runtime_compose = templatefile("${path.module}/templates/compose.oci.yaml.tftpl", {})

  bootstrap_script = templatefile("${path.module}/templates/bootstrap.sh.tftpl", {
    bootstrap_revision        = var.bootstrap_revision
    base_package_nevras       = join(" ", var.base_package_nevras)
    compose_config_b64        = base64encode(local.runtime_compose)
    containerd_version        = var.containerd_version
    docker_ce_cli_version     = var.docker_ce_cli_version
    docker_ce_version         = var.docker_ce_version
    docker_compose_sha256     = var.docker_compose_sha256
    docker_compose_version    = var.docker_compose_version
    docker_repo_file_sha256   = var.docker_repo_file_sha256
    go_package_version        = var.go_package_version
    image_repository          = local.image_repository
    ocir_helper_binary_sha256 = var.ocir_helper_binary_sha256
    ocir_helper_source_commit = var.ocir_helper_source_commit
    ocir_helper_source_sha256 = var.ocir_helper_source_sha256
    registry_host             = local.ocir_host
    servers_config_b64        = base64encode(file("${path.module}/../../docker/servers.synthetic.json"))
  })
}
