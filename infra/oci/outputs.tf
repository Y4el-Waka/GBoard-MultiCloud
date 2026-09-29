output "region" {
  description = "OCI region selected for the lab."
  value       = var.region
}

output "vcn_id" {
  description = "CoreView lab VCN OCID."
  value       = oci_core_vcn.lab.id
  sensitive   = true
}

output "instance_id" {
  description = "Private CoreView instance OCID used when creating reviewed Bastion sessions."
  value       = oci_core_instance.coreview.id
  sensitive   = true
}

output "instance_private_ip" {
  description = "Private instance address; never an Internet endpoint."
  value       = oci_core_instance.coreview.private_ip
  sensitive   = true
}

output "bastion_id" {
  description = "OCI Bastion OCID used to create short-lived sessions outside Terraform."
  value       = oci_bastion_bastion.lab.id
  sensitive   = true
}

output "container_repository_name" {
  description = "Private immutable OCIR repository name."
  value       = oci_artifacts_container_repository.coreview.display_name
}

output "image_repository" {
  description = "OCIR repository prefix. Runtime initialization adds the separately approved digest."
  value       = local.image_repository
  sensitive   = true
}

output "runtime_volume_id" {
  description = "Protected persistent runtime block volume OCID."
  value       = oci_core_volume.runtime.id
  sensitive   = true
}
