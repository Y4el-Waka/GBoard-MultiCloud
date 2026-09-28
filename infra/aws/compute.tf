data "aws_ssm_parameter" "al2023" {
  count = var.ami_id_override == null ? 1 : 0
  name  = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "coreview" {
  ami                         = local.resolved_ami_id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.lab.id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.instance.id]
  iam_instance_profile        = aws_iam_instance_profile.instance.name
  user_data                   = local.instance_user_data
  user_data_replace_on_change = true

  monitoring              = false
  disable_api_termination = false
  ebs_optimized           = true

  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gib
    encrypted             = true
    delete_on_termination = true

    tags = merge(local.common_tags, {
      Name = "${local.name_prefix}-root"
    })
  }

  tags = {
    Name = "${local.name_prefix}-host"
  }

  lifecycle {
    precondition {
      condition     = var.instance_type == "t3.small"
      error_message = "Phase 1.0A has only evaluated t3.small. Re-evaluate cost and capacity before changing it."
    }
  }
}
