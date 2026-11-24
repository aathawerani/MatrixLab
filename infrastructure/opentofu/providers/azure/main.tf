# Azure stack. Same contract: everything topology-dependent comes from the spec.
#
#   python3 scripts/render_environment.py environments/azure/development
#   tofu -chdir=infrastructure/opentofu/providers/azure workspace select -or-create azure-development
#   tofu -chdir=infrastructure/opentofu/providers/azure plan \
#        -var spec_file=$PWD/generated/azure/development/tofu-input.json \
#        -var admin_ssh_public_key_file=~/.ssh/id_ed25519.pub

terraform {
  required_version = ">= 1.8"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.0" } # confirm on first init
  }
}

variable "spec_file" { type = string }

variable "admin_username" {
  type    = string
  default = "matrixadmin"
}

variable "admin_ssh_public_key_file" {
  description = "Path to a PUBLIC key (never a private key)"
  type        = string
}

variable "admin_ssh_cidrs" {
  description = "Source CIDRs allowed to SSH, e.g. the AzureBastionSubnet range"
  type        = list(string)
  default     = []
}

module "topology" {
  source            = "../../modules/topology-input"
  spec_file         = var.spec_file
  expected_provider = "azure"
}

locals {
  net   = module.topology.network
  nodes = module.topology.nodes
  rules = module.topology.rules
}

provider "azurerm" {
  features {}
}

resource "azurerm_resource_group" "this" {
  name     = local.net.resource_group
  location = local.net.location
}

resource "azurerm_virtual_network" "this" {
  name                = "${module.topology.prefix}-vnet"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = [local.net.cidr]
}

resource "azurerm_subnet" "this" {
  name                 = "${module.topology.prefix}-subnet"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [local.net.cidr]
}

# One application security group per node; NSG rules reference ASGs, not IPs.
resource "azurerm_application_security_group" "node" {
  for_each            = local.nodes
  name                = each.value.label
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
}

resource "azurerm_network_security_group" "this" {
  name                = "${module.topology.prefix}-nsg"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
}

resource "azurerm_network_security_rule" "flow" {
  for_each                                   = local.rules
  name                                       = each.value.label
  resource_group_name                        = azurerm_resource_group.this.name
  network_security_group_name                = azurerm_network_security_group.this.name
  priority                                   = each.value.priority
  direction                                  = "Inbound"
  access                                     = "Allow"
  protocol                                   = "Tcp"
  source_port_range                          = "*"
  destination_port_range                     = tostring(each.value.port)
  source_application_security_group_ids      = [azurerm_application_security_group.node[each.value.from].id]
  destination_application_security_group_ids = [azurerm_application_security_group.node[each.value.to].id]
}

resource "azurerm_network_security_rule" "admin_ssh" {
  count                       = length(var.admin_ssh_cidrs) > 0 ? 1 : 0
  name                        = "${module.topology.prefix}-admin-ssh"
  resource_group_name         = azurerm_resource_group.this.name
  network_security_group_name = azurerm_network_security_group.this.name
  priority                    = 900
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefixes     = var.admin_ssh_cidrs
  destination_address_prefix  = "VirtualNetwork"
}

# Azure's built-in AllowVnetInBound (65000) would otherwise let every VM reach
# every port on every other VM, making the generated flows meaningless.
resource "azurerm_network_security_rule" "deny_vnet_inbound" {
  name                        = "${module.topology.prefix}-deny-vnet-inbound"
  resource_group_name         = azurerm_resource_group.this.name
  network_security_group_name = azurerm_network_security_group.this.name
  priority                    = 4000
  direction                   = "Inbound"
  access                      = "Deny"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "VirtualNetwork"
  destination_address_prefix  = "VirtualNetwork"
}

resource "azurerm_subnet_network_security_group_association" "this" {
  subnet_id                 = azurerm_subnet.this.id
  network_security_group_id = azurerm_network_security_group.this.id
}

resource "azurerm_network_interface" "node" {
  for_each            = local.nodes
  name                = "${each.value.label}-nic"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  ip_configuration {
    name                          = "private"
    subnet_id                     = azurerm_subnet.this.id
    private_ip_address_allocation = "Static"
    private_ip_address            = each.value.private_ip
  }
}

resource "azurerm_network_interface_application_security_group_association" "node" {
  for_each                      = local.nodes
  network_interface_id          = azurerm_network_interface.node[each.key].id
  application_security_group_id = azurerm_application_security_group.node[each.key].id
}

resource "azurerm_linux_virtual_machine" "node" {
  for_each              = local.nodes
  name                  = each.value.hostname
  computer_name         = each.value.hostname
  resource_group_name   = azurerm_resource_group.this.name
  location              = azurerm_resource_group.this.location
  size                  = each.value.machine_type
  admin_username        = var.admin_username
  network_interface_ids = [azurerm_network_interface.node[each.key].id]

  admin_ssh_key {
    username   = var.admin_username
    public_key = file(pathexpand(var.admin_ssh_public_key_file))
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = each.value.disk_gb
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  tags = { matrix_environment = module.topology.environment, matrix_node = each.key }
}

output "nodes" {
  value = { for k, v in azurerm_linux_virtual_machine.node : k => {
    hostname   = v.name
    private_ip = v.private_ip_address
    instance   = v.id
  } }
}
