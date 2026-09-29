variable "tenancy_ocid" {
  description = "Existing OCI tenancy allowed to host the lab. The tenancy is never created by this stack."
  type        = string

  validation {
    condition = (
      can(regex("^ocid1\\.tenancy\\.oc1\\.\\.[A-Za-z0-9]+$", var.tenancy_ocid)) &&
      contains(var.allowed_tenancy_ocids, var.tenancy_ocid)
    )
    error_message = "tenancy_ocid must be a syntactically valid OCID present in allowed_tenancy_ocids."
  }
}

variable "allowed_tenancy_ocids" {
  description = "Mandatory tenancy allow-list stored only in ignored tfvars."
  type        = list(string)

  validation {
    condition = (
      length(var.allowed_tenancy_ocids) > 0 &&
      alltrue([
        for ocid in var.allowed_tenancy_ocids :
        can(regex("^ocid1\\.tenancy\\.oc1\\.\\.[A-Za-z0-9]+$", ocid))
      ])
    )
    error_message = "allowed_tenancy_ocids must contain at least one syntactically valid tenancy OCID."
  }
}

variable "compartment_ocid" {
  description = "Existing compartment allowed to contain the lab. The compartment is never created by this stack."
  type        = string

  validation {
    condition = (
      can(regex("^ocid1\\.compartment\\.oc1\\.\\.[A-Za-z0-9]+$", var.compartment_ocid)) &&
      contains(var.allowed_compartment_ocids, var.compartment_ocid)
    )
    error_message = "compartment_ocid must be a syntactically valid OCID present in allowed_compartment_ocids."
  }
}

variable "allowed_compartment_ocids" {
  description = "Mandatory compartment allow-list stored only in ignored tfvars."
  type        = list(string)

  validation {
    condition = (
      length(var.allowed_compartment_ocids) > 0 &&
      alltrue([
        for ocid in var.allowed_compartment_ocids :
        can(regex("^ocid1\\.compartment\\.oc1\\.\\.[A-Za-z0-9]+$", ocid))
      ])
    )
    error_message = "allowed_compartment_ocids must contain at least one syntactically valid compartment OCID."
  }
}

variable "region" {
  description = "OCI region evaluated and costed for this blueprint."
  type        = string
  default     = "us-ashburn-1"

  validation {
    condition     = var.region == "us-ashburn-1"
    error_message = "Phase 1.3A is implemented and costed only for us-ashburn-1."
  }
}

variable "ocir_region_key" {
  description = "OCIR region key paired with region."
  type        = string
  default     = "iad"

  validation {
    condition     = var.region != "us-ashburn-1" || var.ocir_region_key == "iad"
    error_message = "us-ashburn-1 uses the iad OCIR region key."
  }
}

variable "provider_auth" {
  description = "Short-lived operator authentication mode. API keys are intentionally not represented as Terraform variables."
  type        = string
  default     = "SecurityToken"

  validation {
    condition     = contains(["SecurityToken", "InstancePrincipal", "ResourcePrincipal"], var.provider_auth)
    error_message = "Use a short-lived supported OCI provider authentication mode."
  }
}

variable "config_file_profile" {
  description = "OCI CLI profile name. Credentials and token files remain outside this repository."
  type        = string
  default     = "COREVIEW_LAB"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]{1,64}$", var.config_file_profile))
    error_message = "config_file_profile must be a simple OCI profile name."
  }
}

variable "availability_domain" {
  description = "Exact availability-domain name selected during a future authorized preflight."
  type        = string

  validation {
    condition     = length(trimspace(var.availability_domain)) >= 3
    error_message = "availability_domain must be resolved explicitly."
  }
}

variable "project_name" {
  description = "Short project label used in names and tags."
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

variable "additional_freeform_tags" {
  description = "Additional non-sensitive OCI free-form tags."
  type        = map(string)
  default     = {}
}

variable "vcn_cidr" {
  description = "Approved canonical non-overlapping RFC1918 VCN CIDR."
  type        = string

  validation {
    condition = try(
      cidrhost(var.vcn_cidr, 0) == split("/", var.vcn_cidr)[0] &&
      tonumber(split("/", var.vcn_cidr)[1]) >= 16 &&
      tonumber(split("/", var.vcn_cidr)[1]) <= 24 &&
      can(regex("^(?:10\\.|172\\.(?:1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.)", split("/", var.vcn_cidr)[0])),
      false,
    )
    error_message = "vcn_cidr must be a canonical RFC1918 IPv4 /16 through /24 CIDR."
  }
}

variable "workload_subnet_cidr" {
  description = "Approved workload subnet contained by vcn_cidr."
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
  description = "Dedicated private subnet for the OCI Bastion private endpoint."
  type        = string

  validation {
    condition = try(
      cidrhost(var.bastion_subnet_cidr, 0) == split("/", var.bastion_subnet_cidr)[0] &&
      tonumber(split("/", var.bastion_subnet_cidr)[1]) >= 24 &&
      tonumber(split("/", var.bastion_subnet_cidr)[1]) <= 28,
      false,
    )
    error_message = "bastion_subnet_cidr must be a canonical IPv4 /24 through /28 CIDR."
  }

}

variable "bastion_client_cidrs" {
  description = "Reviewed operator source CIDRs allowed to create connections to Bastion sessions. Never use 0.0.0.0/0."
  type        = list(string)

  validation {
    condition = (
      length(var.bastion_client_cidrs) > 0 &&
      alltrue([
        for cidr in var.bastion_client_cidrs :
        can(cidrhost(cidr, 0)) && cidr != "0.0.0.0/0" && try(tonumber(split("/", cidr)[1]) >= 24, false)
      ])
    )
    error_message = "bastion_client_cidrs must contain narrow IPv4 /24-/32 operator prefixes and cannot contain 0.0.0.0/0."
  }
}

variable "bastion_max_session_ttl_seconds" {
  description = "Maximum OCI Bastion session TTL; Oracle supports 30-180 minutes."
  type        = number
  default     = 3600

  validation {
    condition     = var.bastion_max_session_ttl_seconds >= 1800 && var.bastion_max_session_ttl_seconds <= 10800
    error_message = "Bastion TTL must be between 1800 and 10800 seconds."
  }
}

variable "shape" {
  description = "x86_64 flexible shape selected for compatibility with the CI linux/amd64 image."
  type        = string
  default     = "VM.Standard.E4.Flex"

  validation {
    condition     = var.shape == "VM.Standard.E4.Flex"
    error_message = "Phase 1.3A implements only VM.Standard.E4.Flex. ARM requires an independently validated image chain."
  }
}

variable "shape_ocpus" {
  description = "OCPUs assigned to the flexible x86 shape."
  type        = number
  default     = 1

  validation {
    condition     = var.shape_ocpus == 1
    error_message = "Phase 1.3A is sized and costed for exactly 1 OCPU."
  }
}

variable "shape_memory_gib" {
  description = "Memory assigned to the flexible x86 shape."
  type        = number
  default     = 8

  validation {
    condition     = var.shape_memory_gib == 8
    error_message = "Phase 1.3A is sized and costed for exactly 8 GiB."
  }
}

variable "image_ocid" {
  description = "Exact region-specific Oracle Linux 9 x86_64 platform image OCID resolved during authorized preflight."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.image\\.oc1\\..+$", var.image_ocid))
    error_message = "image_ocid must be an exact OCI image OCID, never a display-name lookup."
  }
}

variable "admin_ssh_public_key" {
  description = "Operator public key only. Private keys never enter Terraform."
  type        = string

  validation {
    condition     = can(regex("^(ssh-ed25519|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]+(?: .*)?$", trimspace(var.admin_ssh_public_key)))
    error_message = "Use a reviewed Ed25519 or ECDSA P-256 public key."
  }
}

variable "boot_volume_size_gib" {
  description = "Encrypted boot volume size; OCI custom boot volumes require at least 50 GiB and at least the image default."
  type        = number
  default     = 50

  validation {
    condition     = var.boot_volume_size_gib >= 50
    error_message = "boot_volume_size_gib must be at least 50 GiB and rechecked against the pinned image default."
  }
}

variable "runtime_volume_size_gib" {
  description = "Separate encrypted runtime block volume for Docker and CoreView data."
  type        = number
  default     = 50

  validation {
    condition     = var.runtime_volume_size_gib >= 50
    error_message = "OCI block volumes must be at least 50 GiB."
  }
}

variable "container_repository_name" {
  description = "Private immutable OCIR repository dedicated to CoreView."
  type        = string
  default     = "coreview"

  validation {
    condition     = var.container_repository_name == "coreview"
    error_message = "The Phase 1.3A IAM policy is intentionally scoped to the coreview repository."
  }
}

variable "bootstrap_revision" {
  description = "Versioned bootstrap marker; increment only after review."
  type        = string
  default     = "1"

  validation {
    condition     = can(regex("^[1-9][0-9]{0,5}$", var.bootstrap_revision))
    error_message = "bootstrap_revision must be a positive decimal revision."
  }
}

variable "base_package_nevras" {
  description = "Exact Oracle Linux package NEVRAs required by bootstrap, including ca-certificates, curl, OpenSSL, Python, util-linux, e2fsprogs, tar and gzip."
  type        = list(string)

  validation {
    condition = (
      length(var.base_package_nevras) >= 8 &&
      alltrue([
        for package in var.base_package_nevras :
        can(regex("^[A-Za-z0-9+_.:~-]+$", package)) && strcontains(package, "-")
      ])
    )
    error_message = "base_package_nevras must contain at least eight exact shell-safe package NEVRAs."
  }
}

variable "docker_repo_file_sha256" {
  description = "Reviewed SHA-256 of Docker's CentOS repository definition downloaded by bootstrap."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.docker_repo_file_sha256))
    error_message = "docker_repo_file_sha256 must be 64 lowercase hexadecimal characters."
  }
}

variable "docker_ce_version" {
  description = "Exact Docker CE package version resolved for the pinned Oracle Linux image."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9+_.:~-]+$", var.docker_ce_version))
    error_message = "docker_ce_version must be an exact shell-safe package version."
  }
}

variable "docker_ce_cli_version" {
  description = "Exact Docker CLI package version resolved for the pinned Oracle Linux image."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9+_.:~-]+$", var.docker_ce_cli_version))
    error_message = "docker_ce_cli_version must be an exact shell-safe package version."
  }
}

variable "containerd_version" {
  description = "Exact containerd.io package version resolved for the pinned Oracle Linux image."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9+_.:~-]+$", var.containerd_version))
    error_message = "containerd_version must be an exact shell-safe package version."
  }
}

variable "go_package_version" {
  description = "Exact temporary Go build dependency version."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9+_.:~-]+$", var.go_package_version))
    error_message = "go_package_version must be an exact shell-safe package version."
  }
}

variable "ocir_helper_source_commit" {
  description = "Reviewed 40-character commit of the helper source endorsed by Oracle's credential-helper tutorial."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.ocir_helper_source_commit))
    error_message = "ocir_helper_source_commit must be a full lowercase Git SHA."
  }
}

variable "ocir_helper_source_sha256" {
  description = "SHA-256 of the pinned helper source archive."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.ocir_helper_source_sha256))
    error_message = "ocir_helper_source_sha256 must be 64 lowercase hexadecimal characters."
  }
}

variable "ocir_helper_binary_sha256" {
  description = "Expected reproducible linux/amd64 helper binary SHA-256 from the reviewed build."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.ocir_helper_binary_sha256))
    error_message = "ocir_helper_binary_sha256 must be 64 lowercase hexadecimal characters."
  }
}

variable "docker_compose_version" {
  description = "Pinned Docker Compose plugin release."
  type        = string
  default     = "2.40.3"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.docker_compose_version))
    error_message = "docker_compose_version must be an exact semantic version."
  }
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
