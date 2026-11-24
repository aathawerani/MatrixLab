# Topology model — how placement is configured

**Requirement (12 Nov 2026):** you can change the layout when rebuilding the environment
(move SonarQube to another VM, spread the apps over four VMs instead of three, and so on)
by editing configuration only. No Ansible role or OpenTofu module gets rewritten.

## Three files, three concerns

| File | Says | Never says |
|---|---|---|
| `applications/catalog.yml` | what each app is: version, ports, dependencies, database, exposure, host kernel settings | which VM it runs on |
| `environments/<provider>/<env>/topology.yml` | which nodes exist, their private IPs and sizes, which apps each node runs, Caddy routes | how to install anything |
| `environments/sizing.yml` | logical size → machine type for each cloud | anything about apps |

## What happens when you change placement

```
topology.yml + catalog.yml + sizing.yml
        │
        ▼  scripts/render_environment.py      (validates, resolves; touches nothing remote)
generated/<provider>/<env>/
  ├─ tofu-input.json   ──► OpenTofu: for_each over nodes and flows
  ├─ inventory.yml     ──► Ansible: one group per node AND one per application
  ├─ host_vars/*.yml   ──► per-host endpoints, bind addresses, pg_hba, Caddy upstreams, sysctls
  ├─ connectivity.md   ──► firewall flows to review or hand to the network administrator
  └─ placement.md      ──► what runs where, deploy order, warnings
```

Moving SonarQube from `vm03` to a new `vm04` changes these things, and only these:

- OpenTofu adds one VM plus one flow, `vm04 → vm02 tcp/5432`. Nodes are keyed by their stable logical name (`vm01`…), so the existing VMs are left alone.
- PostgreSQL's `pg_hba` rule for `sonarqube_app` moves from `vm03`'s IP to `vm04`'s IP.
- `vm.max_map_count` / `fs.file-max` move with SonarQube to `vm04`.
- The `sonarqube` play in `site.yml` now targets `vm04`, because it targets the `sonarqube` group and never a VM name.

`tests/test_render.py` checks each of these points.

## Things placement changes that are easy to miss

1. **Bind addresses.** Today every VM03 app listens on `127.0.0.1` because Caddy is on the same host. Move Caddy (or the app) to another node and that address stops working. The renderer gives each listener `127.0.0.1` and adds the node's private IP only when some consumer is on another node. Set `app_settings.<app>.listen_private: true` for apps that people reach directly (GitLab now, SonarQube once its access route is decided).
2. **Endpoints are resolved from the consumer's side.** A co-located consumer gets `127.0.0.1`; a remote one gets the provider's private IP. No DNS is needed, which fits your preference for no internal DNS.
3. **Firewall rules are written in terms of node identity, not IPs:** GCP network tags, AWS security groups, Azure application security groups. Azure's built-in `AllowVnetInBound` rule would otherwise allow all traffic between VMs, so the Azure stack adds an explicit VNet deny at priority 4000.
4. **The reference environment is never managed by OpenTofu** (`managed_by_opentofu: false`). These VMs were built by hand. For this environment the renderer still produces the inventory and the flow report, and the OpenTofu module refuses this spec.

## Public hostnames and the placeholder domain

Hostnames in `routes:` and string values such as `app_settings.mattermost.site_url` may contain
`{public_domain}`. The committed topology sets `public_domain: alitech.example`; set
`MATRIX_PUBLIC_DOMAIN` when rendering to use the real domain. The real domain then appears only
in the git-ignored `generated/` directory.

`caddy:` options (`bind`, `tls`, `redirect_http`, `hsts_apps`) are described in
`configuration/roles/caddy/README.md`. `tls: acme` on a loopback-only `bind` is rejected.

## Validation (errors stop the run)

- An application placed twice, or an unknown application.
- A dependency that is not placed anywhere.
- A Caddy route to a `private` app (for example `sonar.alitech.example`), or routes defined without Caddy placed.
- Two apps on one node wanting the same port (for example GitLab and Caddy both on 443).
- Conflicting sysctl values on one node.
- Duplicate IPs or hostnames, an IP outside the CIDR, or an IP the cloud reserves (GCP: first 2 and last 2; AWS/Azure: first 4 and last 1).
- A size with no machine-type mapping, for OpenTofu-managed environments.

Advisories are warnings only. For example, SonarQube sharing a node with Caddy or PostgreSQL produces a warning. Leave SonarQube where it is until capacity has been measured.

## Scope limits (deliberate)

- **Clean environments.** Topology changes are meant for building a fresh environment. On an environment that already exists, moving an app with Ansible installs it on the new node but does **not** remove it from the old one. Decommissioning is a separate, explicit step, and moving populated data is data migration, which is out of scope.
- **One instance per app.** There is no HA or replicas. The validator rejects duplicate placement.
- **Static private IPs live in `topology.yml`.** This keeps the pipeline to a single pass: OpenTofu and Ansible both know every IP before anything exists.

## Open items this model exposes

- `connectivity.md` for the reference environment lists **vm03 → vm02 tcp/5432** (OpenProject, Nextcloud, Mattermost and SonarQube to PostgreSQL). This rule is in place.
- The OpenProject image tag is not pinned yet (`version: null`). Capture it from VM03.
- Runner → GitLab uses HTTPS on 443 with a private CA (see `runbooks/gitlab-private-https.md`).
- Admin access and egress are provider-specific and not generated yet: GCP IAP is in place; AWS SSM, Azure Bastion and NAT/egress for package installs still need adding.
