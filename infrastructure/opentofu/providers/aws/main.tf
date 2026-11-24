# AWS stack. Same contract as GCP: everything topology-dependent comes from the spec.
#
#   python3 scripts/render_environment.py environments/aws/development
#   tofu -chdir=infrastructure/opentofu/providers/aws workspace select -or-create aws-development
#   tofu -chdir=infrastructure/opentofu/providers/aws plan \
#        -var spec_file=$PWD/generated/aws/development/tofu-input.json

terraform {
  required_version = ">= 1.8"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" } # confirm on first init
  }
}

variable "spec_file" { type = string }

variable "admin_ssh_cidrs" {
  description = "Private CIDRs allowed to SSH (bastion/VPN). Leave empty when using SSM Session Manager."
  type        = list(string)
  default     = []
}

module "topology" {
  source            = "../../modules/topology-input"
  spec_file         = var.spec_file
  expected_provider = "aws"
}

locals {
  net   = module.topology.network
  nodes = module.topology.nodes
  rules = module.topology.rules
  tags  = { matrix_environment = module.topology.environment }
}

provider "aws" {
  region = local.net.region
  default_tags { tags = local.tags }
}

resource "aws_vpc" "this" {
  cidr_block           = local.net.cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${module.topology.prefix}-vpc" }
}

resource "aws_subnet" "this" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.net.cidr
  availability_zone       = local.net.availability_zone
  map_public_ip_on_launch = false
  tags                    = { Name = "${module.topology.prefix}-subnet" }
}

# One security group per node; flows reference SGs, not IPs.
resource "aws_security_group" "node" {
  for_each    = local.nodes
  name        = each.value.label
  description = "matrix node ${each.key}: ${join(", ", each.value.applications)}"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_egress_rule" "all" {
  for_each          = local.nodes
  security_group_id = aws_security_group.node[each.key].id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "flow" {
  for_each                     = local.rules
  security_group_id            = aws_security_group.node[each.value.to].id
  referenced_security_group_id = aws_security_group.node[each.value.from].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  description                  = each.key
}

resource "aws_vpc_security_group_ingress_rule" "admin_ssh" {
  for_each          = { for pair in setproduct(keys(local.nodes), var.admin_ssh_cidrs) : "${pair[0]}-${pair[1]}" => pair }
  security_group_id = aws_security_group.node[each.value[0]].id
  cidr_ipv4         = each.value[1]
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

resource "aws_instance" "node" {
  for_each                    = local.nodes
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = each.value.machine_type
  subnet_id                   = aws_subnet.this.id
  private_ip                  = each.value.private_ip
  vpc_security_group_ids      = [aws_security_group.node[each.key].id]
  associate_public_ip_address = false

  root_block_device {
    volume_size = each.value.disk_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options { http_tokens = "required" }

  tags = { Name = each.value.hostname, matrix_node = each.key }

  lifecycle { ignore_changes = [ami] } # don't replace VMs when a newer AMI appears
}

output "nodes" {
  value = { for k, i in aws_instance.node : k => {
    hostname   = i.tags["Name"]
    private_ip = i.private_ip
    instance   = i.id
  } }
}
