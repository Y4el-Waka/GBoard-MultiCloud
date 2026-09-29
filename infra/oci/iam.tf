resource "oci_identity_dynamic_group" "runtime" {
  compartment_id = var.tenancy_ocid
  name           = "${replace(local.name_prefix, "-", "_")}_runtime"
  description    = "Exact CoreView lab instance allowed to obtain ephemeral Instance Principal credentials"
  matching_rule  = "ALL {instance.id = '${oci_core_instance.coreview.id}'}"
  freeform_tags  = local.common_tags
}

resource "oci_identity_policy" "runtime_ocir_pull" {
  compartment_id = var.compartment_ocid
  name           = "${local.name_prefix}-runtime-ocir-pull"
  description    = "Allow only the CoreView runtime dynamic group to read the dedicated OCIR repository"
  freeform_tags  = local.common_tags
  statements = [
    "Allow dynamic-group id ${oci_identity_dynamic_group.runtime.id} to read repos in compartment id ${var.compartment_ocid} where all {target.repo.name = '${var.container_repository_name}'}",
  ]
}
