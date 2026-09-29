resource "oci_bastion_bastion" "lab" {
  bastion_type                 = "STANDARD"
  compartment_id               = var.compartment_ocid
  target_subnet_id             = oci_core_subnet.bastion.id
  name                         = "${local.name_prefix}-bastion"
  client_cidr_block_allow_list = var.bastion_client_cidrs
  max_session_ttl_in_seconds   = var.bastion_max_session_ttl_seconds
  freeform_tags                = local.common_tags
}
