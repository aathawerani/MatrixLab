# Provider-free module: reads generated/<provider>/<environment>/tofu-input.json
# (written by scripts/render_environment.py) and shapes it for for_each.
# Every provider stack uses this, so node count and firewall flows always come
# from topology.yml and never from provider code.

variable "spec_file" {
  description = "Path to generated/<provider>/<environment>/tofu-input.json"
  type        = string
}

variable "expected_provider" {
  description = "gcp | aws | azure — guards against applying the wrong spec"
  type        = string
}

locals {
  spec   = jsondecode(file(var.spec_file))
  prefix = "matrix-${local.spec.environment}"

  # Cloud resource names: lowercase, hyphens only (GCP rejects underscores)
  nodes = {
    for key, n in local.spec.nodes : key => merge(n, {
      key   = key
      label = "${local.prefix}-${replace(key, "_", "-")}"
    })
  }
  rules = {
    for r in local.spec.rules : r.name => merge(r, {
      label = "${local.prefix}-${replace(r.name, "_", "-")}"
    })
  }
}

output "environment" { value = local.spec.environment }
output "network" { value = local.spec.network }
output "prefix" { value = local.prefix }
output "nodes" {
  value = local.nodes
  precondition {
    condition     = local.spec.provider == var.expected_provider
    error_message = "Spec is for '${local.spec.provider}', but this stack is '${var.expected_provider}'."
  }
  precondition {
    condition     = local.spec.managed_by_opentofu
    error_message = "This environment has managed_by_opentofu: false (e.g. the hand-built GCP reference). OpenTofu must not manage it."
  }
  precondition {
    condition     = alltrue([for n in values(local.spec.nodes) : n.machine_type != null])
    error_message = "Every node needs a size with a machine-type mapping in environments/sizing.yml."
  }
}
output "rules" { value = local.rules }
