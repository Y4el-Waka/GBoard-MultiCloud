resource "google_compute_network" "lab" {
  project                         = var.project_id
  name                            = "${local.name_prefix}-vpc"
  auto_create_subnetworks         = false
  delete_default_routes_on_create = false
  routing_mode                    = "REGIONAL"

  depends_on = [google_project_service.required["compute.googleapis.com"]]
}

resource "google_compute_subnetwork" "lab" {
  project                  = var.project_id
  name                     = "${local.name_prefix}-subnet"
  region                   = var.region
  network                  = google_compute_network.lab.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_10_MIN"
    flow_sampling        = 0.5
    metadata             = "EXCLUDE_ALL_METADATA"
    filter_expr          = "true"
  }
}

resource "google_compute_router" "lab" {
  project = var.project_id
  name    = "${local.name_prefix}-router"
  region  = var.region
  network = google_compute_network.lab.id
}

resource "google_compute_router_nat" "lab" {
  project                            = var.project_id
  name                               = "${local.name_prefix}-nat"
  region                             = var.region
  router                             = google_compute_router.lab.name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.lab.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# IAP TCP forwarding is not equivalent to AWS Session Manager: it terminates an
# SSH tunnel through Google's proxy fleet, so this narrowly scoped ingress is
# required. There are no rules for the application or simulator ports.
resource "google_compute_firewall" "iap_ssh" {
  project   = var.project_id
  name      = "${local.name_prefix}-allow-iap-ssh"
  network   = google_compute_network.lab.name
  direction = "INGRESS"
  priority  = 1000

  source_ranges           = ["35.235.240.0/20"]
  target_service_accounts = [google_service_account.runtime.email]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "egress_https" {
  project   = var.project_id
  name      = "${local.name_prefix}-allow-egress-https"
  network   = google_compute_network.lab.name
  direction = "EGRESS"
  priority  = 1000

  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.runtime.email]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
}

resource "google_compute_firewall" "egress_dns" {
  project   = var.project_id
  name      = "${local.name_prefix}-allow-egress-dns"
  network   = google_compute_network.lab.name
  direction = "EGRESS"
  priority  = 1010

  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.runtime.email]

  allow {
    protocol = "tcp"
    ports    = ["53"]
  }

  allow {
    protocol = "udp"
    ports    = ["53"]
  }
}

resource "google_compute_firewall" "egress_ntp" {
  project   = var.project_id
  name      = "${local.name_prefix}-allow-egress-ntp"
  network   = google_compute_network.lab.name
  direction = "EGRESS"
  priority  = 1020

  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.runtime.email]

  allow {
    protocol = "udp"
    ports    = ["123"]
  }
}

resource "google_compute_firewall" "egress_deny" {
  project   = var.project_id
  name      = "${local.name_prefix}-deny-other-egress"
  network   = google_compute_network.lab.name
  direction = "EGRESS"
  priority  = 65534

  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.runtime.email]

  deny {
    protocol = "all"
  }
}
