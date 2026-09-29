resource "oci_core_volume" "runtime" {
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_ocid
  display_name        = "${local.name_prefix}-runtime"
  size_in_gbs         = var.runtime_volume_size_gib
  vpus_per_gb         = 10
  freeform_tags       = local.common_tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_core_instance" "coreview" {
  availability_domain  = var.availability_domain
  compartment_id       = var.compartment_ocid
  display_name         = "${local.name_prefix}-host"
  shape                = var.shape
  preserve_boot_volume = false
  freeform_tags        = local.common_tags

  shape_config {
    ocpus         = var.shape_ocpus
    memory_in_gbs = var.shape_memory_gib
  }

  create_vnic_details {
    assign_public_ip = false
    display_name     = "${local.name_prefix}-primary"
    hostname_label   = "coreviewlab"
    nsg_ids          = [oci_core_network_security_group.workload.id]
    subnet_id        = oci_core_subnet.workload.id
  }

  source_details {
    source_type             = "image"
    source_id               = var.image_ocid
    boot_volume_size_in_gbs = var.boot_volume_size_gib
    boot_volume_vpus_per_gb = 10
  }

  metadata = {
    ssh_authorized_keys = trimspace(var.admin_ssh_public_key)
    user_data           = base64encode(local.bootstrap_script)
  }

  instance_options {
    are_legacy_imds_endpoints_disabled = true
  }

  is_pv_encryption_in_transit_enabled = true

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [
    oci_core_network_security_group_security_rule.https_out,
    oci_core_route_table.workload,
  ]
}

resource "oci_core_volume_attachment" "runtime" {
  attachment_type = "paravirtualized"
  instance_id     = oci_core_instance.coreview.id
  volume_id       = oci_core_volume.runtime.id
  device          = "/dev/oracleoci/oraclevdb"
  display_name    = "${local.name_prefix}-runtime-attachment"
  is_read_only    = false
  is_shareable    = false
}
