# Matrix Environment

Configuration-as-code for rebuilding the same application environment on GCP, AWS or Azure,
**with the VM layout set in configuration**. Application data is out of scope (no backups or migration).

- OpenTofu provisions the cloud infrastructure.
- Ansible configures the OS and the applications (one role per application).
- Docker Compose runs the containerised apps; Ansible roles manage the GitLab Omnibus and Mattermost packages.
- One topology file per environment decides how many VMs exist and which apps run on each.
  See [`documentation/topology-model.md`](documentation/topology-model.md).

## Layout

```text
applications/catalog.yml                   what each app needs (versions, ports, deps, DBs, exposure)
environments/sizing.yml                    logical size -> machine type per cloud
environments/<provider>/<env>/topology.yml which VMs exist and which apps run on each
scripts/render_environment.py              validate + resolve -> generated/<provider>/<env>/
infrastructure/opentofu/modules/topology-input   provider-free spec reader (guards included)
infrastructure/opentofu/providers/{gcp,aws,azure}  cloud stacks driven by the spec
configuration/playbooks/site.yml           real deployment (plays target app groups)
configuration/playbooks/preview.yml        renders every host's config locally — no SSH, no changes
configuration/roles/                       one role per application
tests/                                     renderer tests
```

## Change the layout

Edit `nodes:` in a topology file. For example, to give SonarQube its own VM:

```yaml
  vm03: {hostname: ..., private_ip: 10.30.0.12, size: large, applications: [caddy, openproject, nextcloud, mattermost]}
  vm04: {hostname: ..., private_ip: 10.30.0.13, size: medium, applications: [sonarqube]}
```

Then, from the repository root on your control machine:

```bash
python3 scripts/render_environment.py environments/gcp/development   # validate + generate
MATRIX_PUBLIC_DOMAIN=example.com MATRIX_GCP_PROJECT=my-project python3 scripts/render_environment.py environments/gcp/reference  # real domain and project
cat generated/gcp/development/placement.md generated/gcp/development/connectivity.md

cd configuration
ansible-playbook -i ../generated/gcp/development/inventory.yml playbooks/preview.yml   # safe, local only
```

For an OpenTofu-managed environment (never the GCP reference environment):

```bash
tofu -chdir=infrastructure/opentofu/providers/gcp init
tofu -chdir=infrastructure/opentofu/providers/gcp workspace select -or-create gcp-development
tofu -chdir=infrastructure/opentofu/providers/gcp plan -var spec_file=$PWD/generated/gcp/development/tofu-input.json
```

`site.yml` refuses to change any host unless you run it with `-e matrix_allow_changes=true`.

## Status

| Part | State |
|---|---|
| Topology, catalog, renderer, validation | working locally; 79 tests (`python3 -m unittest discover -s tests`) |
| Configuration previews | PostgreSQL, GitLab HTTPS, Runner CA fragment, Caddy, SonarQube, and inventory-derived OpenProject/Nextcloud/Mattermost manifests |
| Ansible install/config tasks | All eight application roles plus `common` implemented; `ansible-playbook --syntax-check` passes for every environment (checked in CI); `common`, `docker`, `gitlab` and `gitlab_runner` are exercised on the local lab, the other roles have not been run on a VM |
| OpenTofu GCP/AWS/Azure stacks | written and formatted; **not yet `init`/`validate`d against real providers** |
| Admin access (AWS SSM, Azure Bastion), NAT/egress | not yet |
| Public sites (GCP reference) | OpenProject, Nextcloud and Mattermost live behind Caddy with Let's Encrypt since 23 Nov 2026 (configured by hand; the Caddy role renders the same Caddyfile but has not been run) |
| GitLab private HTTPS and Runner (GCP reference) | Private CA on VM01, Runner registered on VM02 since 24 Nov 2026 (configured by hand) |
| Local Hyper-V lab | single-VM lab built by `scripts/lab/build.sh`; see [`documentation/hyperv-local.md`](documentation/hyperv-local.md) |

`generated/` is git-ignored. Regenerate it whenever the topology changes.

The preview playbook is local-only. OpenProject and Nextcloud deployment roles generate Compose files but require Vault-supplied runtime values; OpenProject additionally requires a Caddy base image pinned by registry digest. Docker Engine/Compose must be preinstalled. Mattermost emits a JSON merge fragment and
must never replace the complete `config.json` (which contains the database DataSource).
GitLab's private HTTPS preview requires a separately provisioned certificate/key, trusted CA,
and private name resolution. The preview does not issue certificates or change DNS.


## Further documentation

- [GCP environment facts](documentation/gcp-environment.md)
- [DNS and exposure plan](documentation/dns-and-exposure.md)
- [Security baseline](documentation/security-baseline.md)
- [Runbook: expose an application](documentation/runbooks/expose-application.md)
- [Runbook: GitLab private HTTPS and Runner](documentation/runbooks/gitlab-private-https.md)
- [Developer access (design)](documentation/developer-access.md)

## Ansible implementation status

See [Stage 3 implementation status](documentation/STAGE3_IMPLEMENTATION.md) for implemented roles, required secret inputs, deployment guards, and remaining work.
