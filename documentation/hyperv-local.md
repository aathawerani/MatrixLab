# Local Hyper-V lab

Status: **implemented, first automated build pending** (27 November 2026).

A disposable single-VM environment on a Windows 11 Pro laptop (8 cores, about 32 GB RAM). It
exists to test the Ansible roles, GitLab upgrades and the GitLab operating policy before they
reach a real environment.

| | |
|---|---|
| Topology | `environments/hyperv/lab/topology.yml` |
| What runs | GitLab CE with its bundled database, GitLab Runner and Docker, on one VM |
| How it is built | `bash scripts/lab/build.sh`, run on the VM itself |
| Differences from the cloud reference | one VM instead of three; bundled database instead of a separate PostgreSQL VM; its own private CA; Ansible connects locally instead of over SSH |

## What is automated and what is not

| Step | How |
|---|---|
| Hyper-V switch, NAT, VM creation, Ubuntu installation | By hand (below). PowerShell provisioning and Ubuntu Autoinstall are designed, not implemented. |
| Root disk extension, Docker, GitLab, private CA and certificate, Runner, package holds | `scripts/lab/build.sh` (Ansible roles) |
| GitLab `root` password, creating the Runner token | Operator actions; secrets are typed at hidden prompts |
| Trusting the lab CA on the Windows host | By hand (below) |

## Host setup (done once, by hand)

- Hyper-V enabled; VM files under `D:\MatrixLab\`.
- Internal switch `MatrixLab-Internal`; host adapter `10.250.10.1/24`.
- Windows NAT `MatrixLab-NAT` for `10.250.10.0/24`. VMs are not exposed to the physical network.
- VM `matrix-vm01`, `10.250.10.11`: Generation 2, 2 vCPU, dynamic memory 4 to 8 GB, 80 GB disk,
  Ubuntu Server 24.04 with OpenSSH, static address, DNS `1.1.1.1` and `8.8.8.8`.
- A checkpoint of the freshly installed VM, exported as a reusable base image:
  `Export-VMSnapshot -VMName <vm> -Name <checkpoint> -Path D:\MatrixLab\Backups\ubuntu-24.04-base`.

## Build

On the VM:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/<owner>/<repository>.git ~/matrixlab
cd ~/matrixlab && bash scripts/lab/build.sh
```

The script installs Ansible, renders the topology and applies the site playbook to the machine
it runs on. It refuses to run if the machine's hostname is not the one in the topology. It is
safe to re-run; a second run should report no changes.

Then:

1. Set the `root` password: `sudo gitlab-rake "gitlab:password:reset[root]"`.
2. Trust the lab CA on the Windows host (next section) and sign in.
3. Create an instance runner in *Admin > CI/CD > Runners*, then on the VM run
   `sudo matrix-register-runner` and paste the token at the hidden prompt.

## Reaching GitLab from the Windows host

The host is on the lab network, so no tunnel is needed.

1. Copy the CA certificate (public part only):
   `scp <user>@10.250.10.11:/etc/gitlab/trusted-certs/matrix-dev-private-ca.crt $env:USERPROFILE\matrixlab-ca.crt`
2. Compare its SHA-256 fingerprint with the one printed on the VM by
   `openssl x509 -in /etc/gitlab/trusted-certs/matrix-dev-private-ca.crt -noout -fingerprint -sha256`.
3. `Import-Certificate -FilePath $env:USERPROFILE\matrixlab-ca.crt -CertStoreLocation Cert:\LocalMachine\Root`
4. Add `10.250.10.11 gitlab.matrixlab.internal` to `C:\Windows\System32\drivers\etc\hosts`.
   Check first that the file ends with a line break; appending to a file that does not joins the
   new entry onto the last existing line and breaks both.

Windows tools that use the system TLS library (`curl.exe`, Git configured for the Windows
certificate store) refuse a private CA because it publishes no revocation list. Use
`curl.exe --ssl-no-revoke`, and `git config --global http.schannelCheckRevoke false` for Git over
HTTPS. Browsers and Git over SSH are not affected.

Rebuilding the VM creates a new CA: remove the old certificate from the Windows trust store and
repeat the steps.

## Creating another VM from the base image

A copied VM is an identical twin. After `Import-VM -Copy -GenerateNewId`, change:

| Identity | Command on the new VM |
|---|---|
| Hostname | `sudo hostnamectl set-hostname <name>` |
| IP address | edit `/etc/netplan/*.yaml`, then `sudo netplan apply` |
| Machine ID | `sudo rm /etc/machine-id && sudo systemd-machine-id-setup` |
| SSH host keys | `sudo rm /etc/ssh/ssh_host_* && sudo dpkg-reconfigure openssh-server` |

The root disk is extended by the `common` role (`extend_root_lvm: true` in the topology).

## Not done

- Hyper-V VM provisioning from the topology (PowerShell) and Ubuntu Autoinstall.
- Multi-VM local topologies with Ansible over SSH.
- DNS behaviour behind VPN clients on the host was not investigated; the lab uses public resolvers.
