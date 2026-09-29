provider "oci" {
  region              = var.region
  tenancy_ocid        = var.tenancy_ocid
  auth                = var.provider_auth
  config_file_profile = var.config_file_profile
}
