# GCP environment facts (AliTech reference environment)

Recorded facts about the existing, hand-built GCP environment. The reference topology
(`environments/gcp/reference/topology.yml`) is `managed_by_opentofu: false`; nothing in this
repository changes these VMs unless Ansible is run explicitly with `-e matrix_allow_changes=true`.

Recorded from read-only inventory of the running VMs and the changes made since.

## Naming conventions in this repository

- `alitech.example` stands in for the real public domain. The real domain is configured at
  deployment time and is not committed.
- `example-gcp-project` stands in for the real GCP project ID. Set `MATRIX_GCP_PROJECT` when
  rendering so the generated Ansible inventory uses the real project.
- Roles use `/srv/alitech` as the base directory for services, data and secrets. The existing
  hand-built VMs use a different base directory under `/srv`. Before any role is run against
  them, override the role path defaults (for example `matrix_openproject_root`) in a local,
  uncommitted variables file; otherwise the roles would create a second, empty deployment
  alongside the existing one.

## Project and network

| Item | Value |
|---|---|
| Project | `example-gcp-project` (placeholder; the real project ID is not committed) |
| Region / zone | `us-central1` / `us-central1-b` |
| VPC / subnet | `matrix-dev-vpc` / `matrix-dev-subnet` |
| Administration | SSH through Google IAP only; no public SSH |

## VMs

| VM | Hostname | Private IP (reserved) | Machine type | vCPU / RAM | Boot disk |
|---|---|---|---|---|---|
| VM01 | `matrix-dev-vm-01` | 10.20.0.2 | `n4-standard-4` | 4 / ~15 GiB | 200 GB |
| VM02 | `matrix-dev-vm-02` | 10.20.0.3 | `n4-standard-4` | 4 / ~15 GiB | 200 GB |
| VM03 | `matrix-dev-vm-03` | 10.20.0.4 | `n4-standard-4` | 4 / ~15 GiB | 300 GB |

All run Ubuntu 24.04.5 LTS. The VMs run on a scheduled start/stop policy and are off outside
working hours. On every boot Docker,
Caddy, Mattermost and PostgreSQL start automatically and containers with restart policy
`unless-stopped` come back.

Note: `environments/sizing.yml` maps logical sizes to `e2-*` machine types for new
OpenTofu-managed GCP environments. That mapping is provisional and does not describe the
reference VMs, which are `n4-standard-4`.

## Installed software (observed)

| VM | Software |
|---|---|
| VM01 | GitLab CE `19.5.3-ce.0` (bundled Redis, external PostgreSQL on VM02); Docker Engine 29.9.3 installed (no containers) |
| VM02 | PostgreSQL 17.11; GitLab Runner 19.5.3 (installed, not registered); Docker Engine 29.9.3; Docker Compose v5.7.0; container `gitlab-redis` (`redis:7.2-alpine`, consumer unverified) |
| VM03 | Docker Engine 29.9.3; Docker Compose v5.7.0; PostgreSQL client 17.11; Caddy 2.11.9; OpenProject; Nextcloud 35.0.2; SonarQube 26.11; Mattermost 12.0.2 |

Docker Engine and Compose are installed by hand. No role in this repository installs them yet;
the OpenProject, Nextcloud and SonarQube roles assume they are present.

## Firewall paths

| Source | Destination | Port | Purpose | Status |
|---|---|---|---|---|
| Internet | VM03 | TCP 80, 443 | Caddy public sites, Let's Encrypt HTTP-01 | open (verified 23 Nov 2026) |
| VM01 | VM02 | TCP 5432 | GitLab database | applied |
| VM03 | VM02 | TCP 5432 | Application databases | applied |
| VM02 | VM01 | TCP 443 | GitLab Runner → GitLab (HTTPS) | open and in use (Runner registered 24 Nov 2026) |
| VM02 | VM01 | TCP 80 | — | blocked; not needed (GitLab no longer listens on 80) |

VM01 and VM02 have no external IP. VM03 has one (not recorded here).

## Public exposure (live since 23 November 2026)

| Public hostname | Application | Upstream on VM03 |
|---|---|---|
| `projects.<domain>` | OpenProject | `127.0.0.1:8080` |
| `cloud.<domain>` | Nextcloud | `127.0.0.1:8081` |
| `chat.<domain>` | Mattermost | `127.0.0.1:8065` |
| `support.<domain>` | reserved for a future helpdesk (Zammad, planned on a separate VM04); not configured | — |
| `demo.<domain>` | demos under path prefixes; DNS record requested, not configured | — |

Caddy binds `10.20.0.4:80/443` and uses Let's Encrypt. The former internal
`*.matrix-dev.internal` Caddy sites were removed. Application settings changed for the public
names: OpenProject `OPENPROJECT_HOST__NAME` and `COLLABORATIVE_SERVER_URL`; Nextcloud
`trusted_domains`, `overwritehost`, `overwrite.cli.url`, `default_phone_region`,
`maintenance_window_start=12` (UTC), `serverid`; Mattermost `SiteURL`. See
`security-baseline.md` and `runbooks/expose-application.md`.

Configuration backups were kept next to each file with a `before-*` suffix
(`/etc/caddy/Caddyfile.before-public-*`, OpenProject `.env.before-public`, Mattermost
`config.before-public.json`, OpenProject `docker-compose.override.yml.before-autoheal-removal`).

## GitLab HTTPS and Runner (24 November 2026)

GitLab serves `https://gitlab.matrix-dev.internal` on `127.0.0.1:443` and `10.20.0.2:443` with a
certificate from a private, name-constrained CA on VM01 (an earlier decision the same day to stay
on HTTP was reversed because VM02 → VM01 TCP 80 is blocked while 443 is open). The Runner on VM02
is registered with the Docker executor (`alpine:3.21`, 2 CPUs, 4 GB, one job at a time) and
trusts the CA. Administrators reach GitLab through `scripts/client/gitlab-tunnel.zsh`.
See `runbooks/gitlab-private-https.md`.

Also observed: Docker Engine is installed on VM01 but unused; GitLab's NGINX status page listens
on `0.0.0.0:8060` (VM01 has no external IP; restrict to `127.0.0.1` when GitLab is managed by the
role).

## Pending updates (held, 24 November 2026)

| Package | Installed | Available | Plan |
|---|---|---|---|
| GitLab CE / Runner | 19.5.3 | 19.6.1 / 19.6.0 | upgrade together, planned |
| PostgreSQL server and client | 17.11 | 17.12 | planned minor upgrade (restarts the database) |
| Mattermost | 12.0.2 | 12.1.0 | planned |
| Docker Engine / Compose | 29.9.3 / 5.7.0 | 29.10.0 / 5.8.0 | planned (restarts all containers) |

## Open items

1. Developer access to GitLab (WireGuard on VM03 or IAP; pending network approval). See `developer-access.md`.
2. Developer accounts, group, protected branches and merge-request rules.
3. GitLab → Mattermost notifications; optional AI review (PR-Agent with Claude API).
4. Planned upgrades: GitLab and Runner 19.6, PostgreSQL 17.12, Mattermost 12.1, Docker 29.10.
5. `demo.<domain>`: DNS record, then Caddy path routes (renderer support not yet implemented).
6. Outgoing email (SMTP) for all applications; password-reset emails do not work today.
7. SonarQube placement (stays on VM03 until capacity is measured).
8. Apply package holds and the security baseline through Ansible; restrict GitLab's status page.
