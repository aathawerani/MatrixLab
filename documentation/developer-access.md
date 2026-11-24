# Developer access to GitLab (design, not implemented)

Status: **designed, pending network approval** (24 November 2026).

## Requirements

- Developers use Git from VS Code, Claude Code, the CLI and similar tools.
- Peer review uses GitLab merge requests, so developers also need the web interface/API.
- Developers get **no login on any VM**; GitLab stays off the public internet.

## Network route

Preferred: **WireGuard VPN on VM03** (the only VM with a public IP).

- Inbound UDP 51820 to VM03; VM03 forwards only to VM01 TCP 22 (Git over SSH) and 443 (web/API).
- Per-developer key pair; revocation by removing the peer. Split tunnel: only
  `10.20.0.0/24` goes through the VPN.
- VM03 masquerades forwarded traffic, so GCP IP forwarding stays disabled.
- Developers use `https://gitlab.matrix-dev.internal` and normal clone URLs, with
  `10.20.0.2 gitlab.matrix-dev.internal` in their hosts file and the private CA trusted.

Alternative: **IAP TCP forwarding**. `roles/iap.tunnelResourceAccessor` on VM01 only, with an
IAM condition `destination.port` in {22, 443}; no OS Login, Compute Admin, Editor or Owner
roles; "Block project-wide SSH keys" on VM01; a firewall rule from `35.235.240.0/20` to VM01
TCP 443. Git over SSH uses `ProxyCommand gcloud compute start-iap-tunnel ... --listen-on-stdin`;
the web interface needs a tunnel on a local port such as 8443.

Either route gives network access to GitLab only. VM logins require separate OS Login roles or
SSH keys on the VM, which developers do not get.

## Accounts and permissions

- Sign-up is disabled; an administrator creates each account and sets a temporary password
  (changed at first sign-in). Two-factor is enforced.
- Developers get the **Developer** role in one group. Only administrators hold Maintainer/Owner.
- `main` is protected: developers push feature branches; only Maintainers merge.
- Restrict Public visibility (*Admin → Settings → General → Visibility*).

## Review workflow (planned)

- Merge requests with "Pipelines must succeed" and "All threads must be resolved".
  Approvals are optional in GitLab Free; the team rule is at least one approval before merge.
- GitLab → Mattermost notifications (instance-level integration) for merge requests, comments
  and pipeline results. Requires GitLab on VM01 to reach the Mattermost webhook URL.
- AI review (optional): PR-Agent as a CI job on the existing Runner, using the team's Claude
  API access. Sends merge-request diffs to the AI provider; API keys are stored as masked CI
  variables. The job container must trust the private CA.
