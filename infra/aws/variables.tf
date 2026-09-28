variable "aws_region" {
  description = "AWS Region for the laboratory. Phase 1.0A is constrained to us-east-1."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "Phase 1.0A supports only us-east-1."
  }
}

variable "allowed_account_ids" {
  description = "Explicit AWS account allow-list. Keep account identifiers in ignored tfvars, never in versioned source."
  type        = list(string)

  validation {
    condition = (
      length(var.allowed_account_ids) > 0 &&
      alltrue([for account_id in var.allowed_account_ids : can(regex("^[0-9]{12}$", account_id))])
    )
    error_message = "Provide at least one 12-digit authorized AWS account ID."
  }
}

variable "vpc_cidr" {
  description = "Approved, non-overlapping RFC1918 /16 supplied outside source control. The stack derives its first /24 subnet."
  type        = string

  validation {
    condition = try(
      endswith(var.vpc_cidr, "/16") &&
      cidrhost(var.vpc_cidr, 0) == split("/", var.vpc_cidr)[0] &&
      can(regex(
        "^(?:10\\.|172\\.(?:1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.)",
        split("/", var.vpc_cidr)[0],
      )),
      false,
    )
    error_message = "vpc_cidr must be a canonical RFC1918 IPv4 /16 CIDR."
  }
}

variable "project_name" {
  description = "Short project tag and resource-name prefix."
  type        = string
  default     = "coreview-cloud-lab"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,31}$", var.project_name))
    error_message = "project_name must be 3-32 lowercase letters, digits or hyphens."
  }
}

variable "environment" {
  description = "Logical environment tag."
  type        = string
  default     = "lab"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,15}$", var.environment))
    error_message = "environment must be 2-16 lowercase letters, digits or hyphens."
  }
}

variable "logical_owner" {
  description = "Non-personal logical ownership tag."
  type        = string
  default     = "coreview-lab"

  validation {
    condition     = trimspace(var.logical_owner) != ""
    error_message = "logical_owner cannot be empty."
  }
}

variable "additional_tags" {
  description = "Additional non-sensitive tags."
  type        = map(string)
  default     = {}
}

variable "instance_type" {
  description = "Initial EC2 sizing candidate for the measured synthetic workload."
  type        = string
  default     = "t3.small"
}

variable "root_volume_size_gib" {
  description = "Encrypted gp3 root volume size."
  type        = number
  default     = 20

  validation {
    condition     = var.root_volume_size_gib >= 20
    error_message = "The root volume must be at least 20 GiB."
  }
}

variable "ami_id_override" {
  description = "Optional reviewed AL2023 x86_64 AMI ID. Pin this in ignored tfvars for a repeatable deployment."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.ami_id_override == null || can(regex("^ami-[0-9a-f]+$", var.ami_id_override))
    error_message = "ami_id_override must be null or a valid AMI ID."
  }
}

variable "image_tag" {
  description = "Immutable ECR image tag selected by the operator, preferably the reviewed Git commit SHA."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.image_tag))
    error_message = "image_tag must satisfy the ECR tag syntax and cannot be empty."
  }
}

variable "docker_compose_version" {
  description = "Pinned Docker Compose plugin release installed by cloud-init."
  type        = string
  default     = "2.40.3"
}

variable "docker_compose_sha256" {
  description = "SHA-256 for the pinned linux-x86_64 Docker Compose binary."
  type        = string
  default     = "dba9d98e1ba5bfe11d88c99b9bd32fc4a0624a30fafe68eea34d61a3e42fd372"

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.docker_compose_sha256))
    error_message = "docker_compose_sha256 must contain exactly 64 lowercase hexadecimal characters."
  }
}
