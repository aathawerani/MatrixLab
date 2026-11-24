# GCP stack. Node count, machine types, private IPs and firewall flows all come
# from the rendered spec; this file only says how GCP builds each thing.
#
#   python3 scripts/render_environment.py environments/gcp/development
#   tofu -chdir=infrastructure/opentofu/providers/gcp workspace select -or-create gcp-development
#   tofu -chdir=infrastructure/opentofu/providers/gcp plan \
#        -var spec_file=$PWD/generated/gcp/development/tofu-input.json

terraform {
  required_version = ">= 1.8"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" } # confirm on first init
  }
}

variable "spec_file" { type = string }

variable "create_network" {
  description = "false = place VMs in an existing VPC/subnet named in the topology"
  type        = bool
  default     = true
}

variable "image" {
  type    = string
  default = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
}

module "topology" {
  source            = "../../modules/topology-input"
  spec_file         = var.spec_file
  expected_provider = "gcp"
}

locals {
  net   = module.topology.network
  nodes = module.topology.nodes
  rules = module.topology.rules
}

provider "google" {
  project = local.net.project
  region  = local.net.region
  zone    = local.net.zone
}

resource "google_compute_network" "this" {
  count                   = var.create_network ? 1 : 0
  name                    = local.net.vpc
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "this" {
  count                    = var.create_network ? 1 : 0
  name                     = local.net.subnet
  network                  = google_compute_network.this[0].id
  ip_cidr_range            = local.net.cidr
  private_ip_google_access = true
}

data "google_compute_subnetwork" "existing" {
  count = var.create_network ? 0 : 1
  name  = local.net.subnet
}

locals {
  network_self_link = var.create_network ? google_compute_network.this[0].self_link : data.google_compute_subnetwork.existing[0].network
  subnet_self_link  = var.create_network ? google_compute_subnetwork.this[0].self_link : data.google_compute_subnetwork.existing[0].self_link
}

# for_each keyed by the stable logical node key: adding vm04 adds one VM and
# leaves vm01..vm03 untouched. Renaming a key would replace that VM — use a
# `moved` block if you ever rename.
resource "google_compute_instance" "node" {
  for_each     = local.nodes
  name         = each.value.hostname
  machine_type = each.value.machine_type
  tags         = [each.value.label]
  labels       = { matrix_environment = module.topology.environment, matrix_node = replace(each.key, "_", "-") }

  boot_disk {
    initialize_params {
      image = var.image
      size  = each.value.disk_gb
    }
  }

  network_interface {
    subnetwork = local.subnet_self_link
    network_ip = each.value.private_ip
    # no access_config block -> no public IP
  }

  metadata = { enable-oslogin = "TRUE" }

  shielded_instance_config {
    enable_secure_boot = true
  }
}

# Node-to-node flows derived from application dependencies (connectivity.md).
resource "google_compute_firewall" "flow" {
  for_each    = local.rules
  name        = each.value.label
  network     = local.network_self_link
  direction   = "INGRESS"
  priority    = 1000
  source_tags = [local.nodes[each.value.from].label]
  target_tags = [local.nodes[each.value.to].label]
  allow {
    protocol = "tcp"
    ports    = [tostring(each.value.port)]
  }
}

# SSH only through Identity-Aware Proxy (Google's IAP TCP-forwarding range).
resource "google_compute_firewall" "iap_ssh" {
  name          = "${module.topology.prefix}-iap-ssh"
  network       = local.network_self_link
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = [for n in values(local.nodes) : n.label]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

output "nodes" {
  value = { for k, i in google_compute_instance.node : k => {
    hostname   = i.name
    private_ip = i.network_interface[0].network_ip
    instance   = i.id
  } }
}
