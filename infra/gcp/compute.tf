resource "google_compute_disk" "runtime" {
  project = var.project_id
  name    = "${local.name_prefix}-runtime"
  zone    = var.zone
  type    = "pd-balanced"
  size    = var.runtime_disk_size_gib
  labels  = local.common_labels

  # Persistent Disk uses Google-managed encryption by default. A future CMEK
  # decision must add explicit KMS lifecycle and recovery procedures.
  physical_block_size_bytes = 4096

  depends_on = [google_project_service.required["compute.googleapis.com"]]
}

resource "google_compute_instance" "coreview" {
  project             = var.project_id
  name                = "${local.name_prefix}-host"
  zone                = var.zone
  machine_type        = var.machine_type
  can_ip_forward      = false
  deletion_protection = var.deletion_protection
  enable_display      = false
  labels              = local.common_labels

  boot_disk {
    auto_delete = true
    mode        = "READ_WRITE"

    initialize_params {
      image = var.boot_image
      size  = var.boot_disk_size_gib
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.runtime.id
    device_name = "coreview-runtime"
    mode        = "READ_WRITE"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.lab.id
    # Intentionally no access_config block: the instance has no external IP.
  }

  service_account {
    email  = google_service_account.runtime.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  metadata = {
    enable-oslogin            = "TRUE"
    block-project-ssh-keys    = "TRUE"
    serial-port-enable        = "FALSE"
    google-logging-enabled    = "FALSE"
    google-monitoring-enabled = "FALSE"
  }
  metadata_startup_script = local.startup_script

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    provisioning_model  = "STANDARD"
  }

  allow_stopping_for_update = true

  depends_on = [
    google_compute_router_nat.lab,
    google_artifact_registry_repository_iam_member.runtime_reader,
  ]
}
