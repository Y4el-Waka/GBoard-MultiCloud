resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "coreview-lab-runtime"
  display_name = "CoreView synthetic lab runtime"
  description  = "Keyless runtime identity; pull-only access to the dedicated Artifact Registry repository."

  depends_on = [google_project_service.required["iam.googleapis.com"]]
}

resource "google_artifact_registry_repository_iam_member" "runtime_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.coreview.location
  repository = google_artifact_registry_repository.coreview.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

# Human IAP/OS Login access is intentionally not granted by this workload
# stack. The project administrator must bind roles/iap.tunnelResourceAccessor,
# roles/compute.osLogin (or osAdminLogin) and, when required,
# roles/iam.serviceAccountUser to explicitly approved operators.
