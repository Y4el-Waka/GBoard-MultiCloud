output "region" {
  description = "Configured GCP region."
  value       = var.region
}

output "zone" {
  description = "Configured Compute Engine zone."
  value       = var.zone
}

output "network_name" {
  description = "Dedicated VPC name."
  value       = google_compute_network.lab.name
  sensitive   = true
}

output "subnetwork_name" {
  description = "Dedicated subnetwork name."
  value       = google_compute_subnetwork.lab.name
  sensitive   = true
}

output "instance_name" {
  description = "CoreView Compute Engine instance name."
  value       = google_compute_instance.coreview.name
  sensitive   = true
}

output "artifact_repository_name" {
  description = "Artifact Registry repository name (not credentials)."
  value       = google_artifact_registry_repository.coreview.name
  sensitive   = true
}

output "image_reference" {
  description = "Immutable image reference expected by the host. The repository is initially empty."
  value       = local.image_uri
  sensitive   = true
}

output "iap_ssh_command" {
  description = "Operator template for an authenticated OS Login shell through IAP."
  value       = "gcloud compute ssh ${google_compute_instance.coreview.name} --project <authorized-project> --zone ${var.zone} --tunnel-through-iap"
  sensitive   = true
}

output "iap_dashboard_tunnel_command" {
  description = "Operator template for forwarding the loopback-only dashboard inside the SSH-over-IAP tunnel."
  value       = "gcloud compute ssh ${google_compute_instance.coreview.name} --project <authorized-project> --zone ${var.zone} --tunnel-through-iap -- -N -L 5067:127.0.0.1:5067"
  sensitive   = true
}
