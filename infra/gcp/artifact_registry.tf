resource "google_artifact_registry_repository" "coreview" {
  project       = var.project_id
  location      = var.region
  repository_id = local.repository_id
  description   = "Private immutable images for the synthetic CoreView lab"
  format        = "DOCKER"
  mode          = "STANDARD_REPOSITORY"
  labels        = local.common_labels

  docker_config {
    immutable_tags = true
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required["artifactregistry.googleapis.com"]]
}
