data "oci_core_services" "regional" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

resource "oci_core_vcn" "lab" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${local.name_prefix}-vcn"
  dns_label      = "coreviewlab"
  freeform_tags  = local.common_tags
}

resource "oci_core_nat_gateway" "lab" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${local.name_prefix}-nat"
  block_traffic  = false
  freeform_tags  = local.common_tags
}

resource "oci_core_service_gateway" "lab" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${local.name_prefix}-service-gateway"
  freeform_tags  = local.common_tags

  services {
    service_id = data.oci_core_services.regional.services[0].id
  }
}

resource "oci_core_route_table" "workload" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${local.name_prefix}-workload-routes"
  freeform_tags  = local.common_tags

  route_rules {
    network_entity_id = oci_core_service_gateway.lab.id
    destination       = data.oci_core_services.regional.services[0].cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    description       = "Regional OCI services without traversing the Internet"
  }

  route_rules {
    network_entity_id = oci_core_nat_gateway.lab.id
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    description       = "Explicit outbound-only Internet path"
  }
}

resource "oci_core_route_table" "bastion" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${local.name_prefix}-bastion-routes"
  freeform_tags  = local.common_tags
}

# Explicit security lists avoid inheriting OCI's default SSH ingress rule.
resource "oci_core_security_list" "workload" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${local.name_prefix}-workload-sl"
  freeform_tags  = local.common_tags
}

resource "oci_core_security_list" "bastion" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${local.name_prefix}-bastion-sl"
  freeform_tags  = local.common_tags

  egress_security_rules {
    protocol    = "6"
    destination = var.workload_subnet_cidr
    description = "Bastion private endpoint may reach SSH on the workload subnet"

    tcp_options {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_subnet" "workload" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.lab.id
  cidr_block                 = var.workload_subnet_cidr
  display_name               = "${local.name_prefix}-workload"
  dns_label                  = "workload"
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.workload.id
  security_list_ids          = [oci_core_security_list.workload.id]
  freeform_tags              = local.common_tags
}

resource "oci_core_subnet" "bastion" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.lab.id
  cidr_block                 = var.bastion_subnet_cidr
  display_name               = "${local.name_prefix}-bastion"
  dns_label                  = "bastion"
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.bastion.id
  security_list_ids          = [oci_core_security_list.bastion.id]
  freeform_tags              = local.common_tags
}

resource "oci_core_network_security_group" "workload" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${local.name_prefix}-workload-nsg"
  freeform_tags  = local.common_tags
}

resource "oci_core_network_security_group_security_rule" "ssh_from_bastion" {
  network_security_group_id = oci_core_network_security_group.workload.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "${oci_bastion_bastion.lab.private_endpoint_ip_address}/32"
  source_type               = "CIDR_BLOCK"
  description               = "Only this OCI Bastion private endpoint may reach SSH"

  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_network_security_group_security_rule" "https_out" {
  network_security_group_id = oci_core_network_security_group.workload.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  description               = "HTTPS for package bootstrap and external registries"

  tcp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}

resource "oci_core_network_security_group_security_rule" "dns_udp_out" {
  network_security_group_id = oci_core_network_security_group.workload.id
  direction                 = "EGRESS"
  protocol                  = "17"
  destination               = "169.254.169.254/32"
  destination_type          = "CIDR_BLOCK"
  description               = "VCN resolver over UDP"

  udp_options {
    destination_port_range {
      min = 53
      max = 53
    }
  }
}

resource "oci_core_network_security_group_security_rule" "dns_tcp_out" {
  network_security_group_id = oci_core_network_security_group.workload.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "169.254.169.254/32"
  destination_type          = "CIDR_BLOCK"
  description               = "VCN resolver over TCP"

  tcp_options {
    destination_port_range {
      min = 53
      max = 53
    }
  }
}

resource "oci_core_network_security_group_security_rule" "ntp_out" {
  network_security_group_id = oci_core_network_security_group.workload.id
  direction                 = "EGRESS"
  protocol                  = "17"
  destination               = "169.254.169.254/32"
  destination_type          = "CIDR_BLOCK"
  description               = "OCI time synchronization service"

  udp_options {
    destination_port_range {
      min = 123
      max = 123
    }
  }
}

resource "oci_core_network_security_group_security_rule" "imds_out" {
  network_security_group_id = oci_core_network_security_group.workload.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "169.254.169.254/32"
  destination_type          = "CIDR_BLOCK"
  description               = "OCI IMDSv2 and Instance Principal token exchange"

  tcp_options {
    destination_port_range {
      min = 80
      max = 80
    }
  }
}
