# Stage 3 implementation status

This increment starts the real Ansible implementation. It does not run Ansible
and does not change any existing GCP machine.

## Implemented in this increment

### Common role

- Requires `matrix_allow_changes=true`.
- Supports Ubuntu 24.04 or later.
- Refreshes apt metadata and installs baseline tools.
- Does not run a blanket package upgrade.
- Applies generated kernel settings only when the topology requires them.

### PostgreSQL role

- Requires explicit deployment approval.
- Installs PostgreSQL 17 from PGDG using the observed package version by default.
- Writes placement-derived PostgreSQL settings.
- Writes generated application HBA rules to a dedicated file.
- Activates those rules using PostgreSQL's `include_if_exists` pg_hba.conf directive.
- Restarts the PostgreSQL 17 cluster when configuration changes.
- Waits for the generated private listener to accept connections.
- Creates application roles and databases only when
  `matrix_manage_postgresql_databases=true` and passwords are supplied via
  Ansible Vault or an external secret provider.
- Uses `no_log` for role creation tasks.

The PostgreSQL role currently uses Ubuntu's `ssl-cert` snake-oil certificate
paths to reproduce the observed reference configuration. This encrypts the
connection but does not authenticate the PostgreSQL server identity. Replace
this with a private-PKI certificate before requiring verified server identity.

## Dependencies

Install the Ansible collection:

```bash
cd configuration
ansible-galaxy collection install -r requirements.yml
```

The role uses `community.postgresql` for optional role/database creation.

## Preview before deployment

Generate the reference inventory and inspect the preview:

```bash
python3 scripts/render_environment.py environments/gcp/reference
cd configuration
ansible-playbook -i ../generated/gcp/reference/inventory.yml playbooks/preview.yml
```

The preview playbook uses local connections and does not SSH to VMs.

## Deployment guard

The real playbook has a global approval gate and the common role has its own
approval assertion. A real deployment requires:

```bash
ansible-playbook -i ../generated/<provider>/<environment>/inventory.yml \
  playbooks/site.yml -e matrix_allow_changes=true
```

Do not use the existing GCP reference topology as a deployment target. It is
an inventory/reference for clean recreation, not a managed OpenTofu target.

### GitLab Omnibus

- Adds the official GitLab CE package repository and installs the catalog-pinned version.
- Installs server certificate, private key and private CA before package configuration.
- Preserves `/etc/gitlab/gitlab.rb` and loads the managed fragment with `from_file`.
- Configures external PostgreSQL and private HTTPS on topology-generated addresses.
- Runs `gitlab-ctl reconfigure` and checks the readiness endpoint with certificate validation.
- Requires database password, server certificate, private key and CA through Vault or an external provider.

### GitLab Runner

- Adds the official Runner package repository and installs the catalog-pinned version.
- Installs the private CA as a hostname-specific certificate in `/etc/gitlab-runner/certs/`.
- Preserves `/etc/gitlab-runner/config.toml`.
- Enables and starts the service.
- Does not register a runner or assume an executor, tags, or privileged mode.

## Increment 3 — OpenProject and Nextcloud Compose roles

Implemented Ansible deployment roles for OpenProject and Nextcloud. Both use topology-derived external PostgreSQL endpoints, explicit approval gates, secret-bearing protected Compose files (`0600`), generated listener bindings, and clean-install semantics. Container images are pinned to registry digests recorded in the catalog.

OpenProject's observed Caddy proxy build context is captured under `configuration/roles/openproject/files/proxy/`. The proxy forwards `/hocuspocus*` to the Hocuspocus service and other requests to the `web` service. Its Caddy base image must be supplied as an immutable registry-digest reference (`matrix_openproject_proxy_base_image`) before deployment. The role deliberately excludes the no-op database helper, legacy backup/upgrade helpers, and Docker-socket-mounted autoheal container.

Nextcloud uses its observed Apache image and Redis image digests, external PostgreSQL, host-persisted `html` and `data` directories, and a private named Redis volume. Runtime credentials are supplied through Vault or an approved secret provider.

These roles have not been executed by Ansible or Docker Compose in this development environment. The deployment approval gate remains mandatory. No existing VM or cloud resource was changed.


## Remaining implementation and validation

All roles (common, PostgreSQL, GitLab, Runner, OpenProject, Nextcloud, Mattermost, SonarQube, Caddy) are implemented but none has been exercised against a VM, and `ansible-playbook --syntax-check` has not yet been run. No existing service should be changed until roles are validated in a disposable target environment. OpenProject deployment additionally requires a verified immutable Caddy base-image digest and Vault-supplied runtime values.


## SonarQube role

The SonarQube role deploys the inventory-pinned Community image using its observed registry digest, connects to the topology-generated external PostgreSQL endpoint, and provisions the four observed named data volumes (data, extensions, logs, temp). Its JDBC password is supplied through Ansible Vault or an approved secret provider and is never included in the preview manifest. The role persists and immediately applies the observed kernel requirements `vm.max_map_count=524288` and `fs.file-max=131072`. It binds only the generated listener addresses and requires the global and role-level `matrix_allow_changes=true` approval gates. It does not create or migrate database contents and does not upgrade the application.


## Caddy role

The Caddy role installs the pinned package from the official Caddy Cloudsmith stable APT repository and renders the Caddyfile from topology-generated routes, listener binding, and TLS settings. It validates the generated configuration before reload. Since 23 November 2026 the GCP reference topology describes public sites for OpenProject, Nextcloud and Mattermost: Let's Encrypt certificates (`tls: acme`), binding on VM03's private IP, and explicit HTTP-to-HTTPS redirects. The role does not create DNS records or firewall rules. Caddy's certificate and CA state is kept under `/var/lib/caddy`.


## Mattermost role

The Mattermost role installs the exact observed Debian package version `12.0.2-0` from the official Mattermost APT repository (`deb.packages.mattermost.com`, Ubuntu Noble). APT validates the package against the repository's signed package metadata; the observed `.deb` SHA256 is recorded in the catalog for inventory/audit purposes. Package installation suppresses service autostart until configuration is installed.

The role requires the complete Mattermost `config.json` as a Vault-protected mapping (`matrix_mattermost_config`). This is deliberate: only a safe subset of the existing configuration was inventoried, and the PostgreSQL `SqlSettings.DataSource` is sensitive. The role refuses a partial configuration fragment. It installs the complete protected configuration as `mattermost:mattermost`, mode `0600`, then enables and starts the package-provided systemd unit. The service runs as `mattermost:mattermost`; the observed unit has `LimitNOFILE=49152`. The first startup against a newly provisioned empty external database may initialize Mattermost's schema. No database content or file data is migrated or restored.
