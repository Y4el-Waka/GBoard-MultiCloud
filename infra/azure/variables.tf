variable "subscription_id" {
  description = "Existing Azure subscription allowed to host the lab. The subscription is never created by this stack."
  type        = string

  validation {
    condition = (
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", var.subscription_id)) &&
      contains(var.allowed_subscription_ids, var.subscription_id)
    )
    error_message = "subscription_id must be a valid UUID present in allowed_subscription_ids."
  }
}

variable "allowed_subscription_ids" {
  description = "Mandatory explicit subscription allow-list kept only in ignored tfvars."
  type        = list(string)

  validation {
    condition = (
      length(var.allowed_subscription_ids) > 0 &&
      alltrue([
        for subscription_id in var.allowed_subscription_ids :
        can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", subscription_id))
      ])
    )
    error_message = "allowed_subscription_ids must contain at least one syntactically valid subscription UUID."
  }
}

variable "location" {
  description = "Azure region evaluated for this blueprint."
  type        = string
  default     = "eastus2"

  validation {
    condition     = var.location == "eastus2"
    error_message = "Phase 1.2A is costed and documented only for eastus2."
  }
}

variable "resource_group_name" {
  description = "Resource Group to create or adopt after an authorized preflight."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._()-]{1,90}$", var.resource_group_name))
    error_message = "resource_group_name must satisfy Azure Resource Group naming rules."
  }
}

variable "create_resource_group" {
  description = "Create the Resource Group when true; otherwise use a pre-existing Resource Group without taking ownership of its lifecycle."
  type        = bool
  default     = true
}

variable "project_name" {
  description = "Short project label used for names and tags."
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
  description = "Non-personal logical ownership tag."
  type        = string
  default     = "coreview-lab"

  validation {
    condition     = trimspace(var.logical_owner) != ""
    error_message = "logical_owner cannot be empty."
  }
}

variable "additional_tags" {
  description = "Additional non-sensitive Azure tags."
  type        = map(string)
  default     = {}
}

variable "vnet_cidr" {
  description = "Approved canonical non-overlapping RFC1918 VNet CIDR, supplied outside source control."
  type        = string

  validation {
    condition = try(
      cidrhost(var.vnet_cidr, 0) == split("/", var.vnet_cidr)[0] &&
      tonumber(split("/", var.vnet_cidr)[1]) >= 16 &&
      tonumber(split("/", var.vnet_cidr)[1]) <= 24 &&
      can(regex("^(?:10\\.|172\\.(?:1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.)", split("/", var.vnet_cidr)[0])),
      false,
    )
    error_message = "vnet_cidr must be a canonical RFC1918 IPv4 /16 through /24 CIDR."
  }
}

variable "workload_subnet_cidr" {
  description = "Approved workload subnet contained by vnet_cidr."
  type        = string

  validation {
    condition = try(
      cidrhost(var.workload_subnet_cidr, 0) == split("/", var.workload_subnet_cidr)[0] &&
      tonumber(split("/", var.workload_subnet_cidr)[1]) >= 24 &&
      tonumber(split("/", var.workload_subnet_cidr)[1]) <= 28,
      false,
    )
    error_message = "workload_subnet_cidr must be a canonical IPv4 /24 through /28 CIDR."
  }
}

variable "bastion_subnet_cidr" {
  description = "Approved AzureBastionSubnet CIDR contained by vnet_cidr; dedicated Bastion requires /26 or larger."
  type        = string

  validation {
    condition = try(
      cidrhost(var.bastion_subnet_cidr, 0) == split("/", var.bastion_subnet_cidr)[0] &&
      tonumber(split("/", var.bastion_subnet_cidr)[1]) <= 26,
      false,
    )
    error_message = "bastion_subnet_cidr must be a canonical IPv4 CIDR with /26 or more addresses."
  }
}

variable "vm_size" {
  description = "Initial Linux VM size for the measured synthetic workload."
  type        = string
  default     = "Standard_B2s"

  validation {
    condition     = var.vm_size == "Standard_B2s"
    error_message = "Phase 1.2A has evaluated only Standard_B2s. Re-evaluate capacity and cost before changing it."
  }
}

variable "admin_username" {
  description = "Local Linux administrator name; authentication uses a separately managed SSH key, never a password."
  type        = string
  default     = "coreviewadmin"

  validation {
    condition     = can(regex("^[a-z_][a-z0-9_-]{0,30}$", var.admin_username))
    error_message = "admin_username must be a valid Linux account name."
  }
}

variable "admin_ssh_public_key" {
  description = "Operator public key only. Keep it in ignored tfvars; private keys never enter Terraform."
  type        = string

  validation {
    condition     = can(regex("^(ssh-ed25519|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]+(?: .*)?$", trimspace(var.admin_ssh_public_key)))
    error_message = "Use a reviewed Ed25519 or ECDSA P-256 SSH public key."
  }
}

variable "source_image" {
  description = "Reviewed immutable Canonical Ubuntu 22.04 Gen2 image reference. Version must be exact and cannot be latest."
  type = object({
    publisher = string
    offer     = string
    sku       = string
    version   = string
  })

  validation {
    condition = (
      var.source_image.publisher == "Canonical" &&
      var.source_image.offer == "0001-com-ubuntu-server-jammy" &&
      var.source_image.sku == "22_04-lts-gen2" &&
      can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.source_image.version)) &&
      var.source_image.version != "latest"
    )
    error_message = "source_image must be an exact Canonical Ubuntu 22.04 LTS Gen2 version, never latest."
  }
}

variable "boot_disk_size_gib" {
  description = "Platform-managed encrypted Standard SSD boot disk size."
  type        = number
  default     = 32

  validation {
    condition     = var.boot_disk_size_gib >= 32
    error_message = "The boot disk must be at least 32 GiB."
  }
}

variable "runtime_disk_size_gib" {
  description = "Platform-managed encrypted Standard SSD disk for Docker and CoreView runtime data."
  type        = number
  default     = 32

  validation {
    condition     = var.runtime_disk_size_gib >= 32
    error_message = "The runtime disk must be at least 32 GiB."
  }
}

variable "acr_name" {
  description = "Globally unique lowercase ACR name chosen during an authorized preflight."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{5,50}$", var.acr_name))
    error_message = "acr_name must contain 5-50 lowercase alphanumeric characters."
  }
}

variable "bootstrap_revision" {
  description = "Versioned one-time bootstrap marker; increment only after reviewing a template change."
  type        = string
  default     = "1"

  validation {
    condition     = can(regex("^[1-9][0-9]{0,5}$", var.bootstrap_revision))
    error_message = "bootstrap_revision must be a positive decimal revision."
  }
}

variable "docker_package_version" {
  description = "Exact docker.io package version resolved from the pinned Ubuntu image during authorized preflight."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9.+:~_-]+$", var.docker_package_version))
    error_message = "docker_package_version must be an exact apt package version."
  }
}

variable "docker_compose_version" {
  description = "Pinned Docker Compose plugin release."
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
