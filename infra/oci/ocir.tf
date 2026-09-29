resource "oci_artifacts_container_repository" "coreview" {
  compartment_id = var.compartment_ocid
  display_name   = var.container_repository_name
  is_immutable   = true
  is_public      = false
  freeform_tags  = local.common_tags

  lifecycle {
    prevent_destroy = true
  }
}
