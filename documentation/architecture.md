# Architecture

Placement is configuration, not code. See `topology-model.md`.

The current GCP layout is captured as `environments/gcp/reference/topology.yml`:
- vm01: GitLab
- vm02: PostgreSQL, GitLab Runner
- vm03: Caddy, OpenProject, Nextcloud, Mattermost, SonarQube

`environments/gcp/development/topology.yml` shows the optional VM04 for SonarQube.
SonarQube placement in the real environment is not final: measure capacity first.
Public exposure and firewall/DNS changes require approval.

GitLab is modeled with private HTTPS on TCP 443 in the reference and example topologies.
The GitLab server certificate, private CA trust, and hostname resolution are prerequisites;
they are not provisioned by the configuration-preview patch.
