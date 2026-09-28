locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Owner       = var.logical_owner
      ManagedBy   = "Terraform"
      DataClass   = "synthetic-only"
    },
    var.additional_tags,
  )

  resolved_ami_id = var.ami_id_override != null ? var.ami_id_override : data.aws_ssm_parameter.al2023[0].value

  runtime_compose = templatefile("${path.module}/templates/compose.aws.yaml.tftpl", {
    image_uri = "${aws_ecr_repository.coreview.repository_url}:${var.image_tag}"
  })

  instance_user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    aws_region             = var.aws_region
    compose_config_b64     = base64encode(local.runtime_compose)
    docker_compose_sha256  = var.docker_compose_sha256
    docker_compose_version = var.docker_compose_version
    ecr_registry           = split("/", aws_ecr_repository.coreview.repository_url)[0]
    servers_config_b64     = base64encode(file("${path.module}/../../docker/servers.synthetic.json"))
  })
}
