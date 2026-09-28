locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_labels = merge(
    {
      project     = var.project_name
      environment = var.environment
      owner       = var.logical_owner
      managed_by  = "terraform"
      data_class  = "synthetic-only"
    },
    var.additional_labels,
  )

  repository_id = "${local.name_prefix}-app"
  image_uri     = "${var.region}-docker.pkg.dev/${var.project_id}/${local.repository_id}/coreview:${var.image_tag}"

  runtime_compose = templatefile("${path.module}/templates/compose.gcp.yaml.tftpl", {
    image_uri = local.image_uri
  })

  startup_script = templatefile("${path.module}/templates/startup.sh.tftpl", {
    bootstrap_revision     = var.bootstrap_revision
    compose_config_b64     = base64encode(local.runtime_compose)
    docker_compose_sha256  = var.docker_compose_sha256
    docker_compose_version = var.docker_compose_version
    registry_host          = "${var.region}-docker.pkg.dev"
    runtime_disk_device    = "coreview-runtime"
    image_uri              = local.image_uri
    servers_config_b64     = base64encode(file("${path.module}/../../docker/servers.synthetic.json"))
  })
}
