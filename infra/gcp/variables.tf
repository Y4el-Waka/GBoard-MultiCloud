variable "project_id" {
  description = "Existing GCP project that may host the lab. The project is never created by this stack."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a syntactically valid GCP project ID."
  }
}

variable "allowed_project_ids" {
  description = "Explicit project allow-list kept in ignored tfvars. This is the GCP project guard."
  type        = list(string)

  validation {
    condition = (
      length(var.allowed_project_ids) > 0 &&
      alltrue([for project_id in var.allowed_project_ids : can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", project_id))]) &&
      contains(var.allowed_project_ids, var.project_id)
    )
    error_message = "Provide syntactically valid project IDs and include project_id in the explicit allow-list."
  }
}

variable "region" {
  description = "GCP region for the first architecture proposal."
  type        = string
  default     = "us-east1"

  validation {
    condition     = var.region == "us-east1"
    error_message = "Phase 1.1A is costed and documented only for us-east1."
  }
}

variable "zone" {
  description = "Compute zone inside the selected region."
  type        = string
  default     = "us-east1-b"

  validation {
    condition     = startswith(var.zone, "${var.region}-")
    error_message = "zone must belong to region."
  }
}

variable "subnet_cidr" {
  description = "Approved non-overlapping RFC1918 subnet CIDR, supplied outside source control."
  type        = string

  validation {
    condition = try(
      cidrhost(var.subnet_cidr, 0) == split("/", var.subnet_cidr)[0] &&
      tonumber(split("/", var.subnet_cidr)[1]) >= 24 &&
      tonumber(split("/", var.subnet_cidr)[1]) <= 28 &&
      can(regex(
        "^(?:10\\.|172\\.(?:1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.)",
        split("/", var.subnet_cidr)[0],
      )),
      false,
    )
    error_message = "subnet_cidr must be a canonical RFC1918 IPv4 /24 through /28 CIDR."
  }
}

variable "project_name" {
  description = "Short project label and resource-name prefix."
  type        = string
  default     = "coreview-cloud-lab"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,29}$", var.project_name))
    error_message = "project_name must contain 3-30 lowercase letters, digits or hyphens."
  }
}

variable "environment" {
  description = "Logical environment label."
  type        = string
  default     = "lab"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,15}$", var.environment))
    error_message = "environment must contain 2-16 lowercase letters, digits or hyphens."
  }
}

variable "logical_owner" {
  description = "Non-personal logical ownership label."
  type        = string
  default     = "coreview-lab"

  validation {
    condition     = trimspace(var.logical_owner) != ""
    error_message = "logical_owner cannot be empty."
  }
}

variable "additional_labels" {
  description = "Additional non-sensitive GCP labels."
  type        = map(string)
  default     = {}
}

variable "machine_type" {
  description = "Initial Compute Engine size for the measured synthetic workload."
  type        = string
  default     = "e2-small"

  validation {
    condition     = var.machine_type == "e2-small"
    error_message = "Phase 1.1A has evaluated only e2-small. Re-evaluate capacity and cost before changing it."
  }
}

variable "boot_image" {
  description = "Reviewed canonical immutable Debian 12 x86_64 image reference; image-family references are rejected."
  type        = string

  validation {
    condition = (
      can(regex("^projects/debian-cloud/global/images/debian-12-[a-z0-9-]+$", var.boot_image)) &&
      !strcontains(var.boot_image, "/family/")
    )
    error_message = "boot_image must be a pinned canonical Debian 12 image reference, not an image family."
  }
}

variable "boot_disk_size_gib" {
  description = "Google-managed encrypted pd-balanced boot disk size."
  type        = number
  default     = 20

  validation {
    condition     = var.boot_disk_size_gib >= 20
    error_message = "The boot disk must be at least 20 GiB."
  }
}

variable "runtime_disk_size_gib" {
  description = "Google-managed encrypted pd-balanced disk for Docker volumes and runtime data."
  type        = number
  default     = 20

  validation {
    condition     = var.runtime_disk_size_gib >= 20
    error_message = "The runtime disk must be at least 20 GiB."
  }
}

variable "deletion_protection" {
  description = "Protect the VM from accidental Terraform deletion until an approved teardown disables it."
  type        = bool
  default     = true
}

variable "image_tag" {
  description = "Immutable Artifact Registry tag, preferably the reviewed full Git commit SHA."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.image_tag))
    error_message = "image_tag must satisfy the Docker tag syntax and cannot be empty."
  }
}

variable "bootstrap_revision" {
  description = "Versioned one-time host bootstrap marker. Increment only after reviewing a startup-template change."
  type        = string
  default     = "1"

  validation {
    condition     = can(regex("^[1-9][0-9]{0,5}$", var.bootstrap_revision))
    error_message = "bootstrap_revision must be a positive decimal revision."
  }
}

variable "docker_compose_version" {
  description = "Pinned Docker Compose plugin release installed during bootstrap."
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
