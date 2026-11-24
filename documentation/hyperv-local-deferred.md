# Local Hyper-V target — deferred

Status: **deferred (20 November 2026).** Work continues on the GCP environment.

A local Hyper-V lab on a Windows 11 Pro laptop (8 cores, ~32 GB RAM) was
started as a disposable test target for the Ansible roles. It was paused because it was
taking too long. Nothing in this repository depends on it.

## What was built by hand (not automated, not in this repository)

- Hyper-V enabled; VM storage under `D:\MatrixLab\`.
- Internal switch `MatrixLab-Internal`; host adapter `10.250.10.1/24`.
- Windows NAT `MatrixLab-NAT` for `10.250.10.0/24`.
- VM01 `matrix-vm01` (`10.250.10.11`): Generation 2, 2 vCPU, dynamic memory 4–8 GB,
  80 GB VHDX, Ubuntu Server 24.04.5, static Netplan address, SSH working.
- Planned but not created: VM02 `.12`, VM03 `.13`, VM04 `.14`.

## Design intended if resumed

- Hyper-V VMs provisioned with PowerShell; no third-party OpenTofu Hyper-V provider.
- A `hyperv` provider and `environments/hyperv/<env>/topology.yml` in the renderer, with
  `managed_by_opentofu: false` and direct SSH connection variables.
- Same Ansible roles as the cloud environments.
- Ubuntu Autoinstall rather than cloning, so machine IDs and SSH host keys stay unique.
- DNS behaviour behind VPN clients on the host was not resolved and is out of scope.
