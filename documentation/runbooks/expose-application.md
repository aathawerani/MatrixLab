# Runbook: expose an application publicly through Caddy

Used for OpenProject, Nextcloud and Mattermost on 23 November 2026. Commands run on VM03
unless stated. `<app-host>` is the public hostname, e.g. `projects.<domain>`.

## 0. Prerequisites

1. The DNS A record for `<app-host>` points to VM03's external IP. Check from outside:
   `dig +short <app-host>`.
2. Inbound TCP 80 and 443 are open to VM03. From outside, `nc -vz -w 5 <external-ip> 443`
   must say "Connection refused" (open, nothing listening) or succeed, not time out.
3. The application listens on `127.0.0.1` only.

## 1. Secure the application while it is still private

Use an IAP tunnel to reach it, or the application's CLI:

- Change default administrator passwords. Read new passwords from a hidden prompt and pipe
  them into the container; never put a password on the command line.
- Disable self-registration.
- Enable and, where supported, enforce two-factor authentication.
- Mattermost: create the first account through the tunnel before exposure (the first
  account becomes system administrator).

See `documentation/security-baseline.md`.

## 2. Add the Caddy site

1. Back up: `sudo cp -p /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-<change>`.
2. Write the new Caddyfile. The site must bind VM03's private address (GCP delivers
   external traffic there), must not use `tls internal`, and needs an explicit `http://`
   redirect site because the global option is `auto_https disable_redirects`.
3. Validate, then reload only if valid:
   `sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && sudo systemctl reload caddy`
4. Confirm `certificate obtained successfully` in `journalctl -u caddy`.

The Caddy role renders the same file from the topology (`routes`, `caddy.*`).

## 3. Point the application at its public name

| Application | Settings |
|---|---|
| OpenProject | `.env`: `OPENPROJECT_HOST__NAME=<app-host>`, `OPENPROJECT_HTTPS=true`, `COLLABORATIVE_SERVER_URL=wss://<app-host>/hocuspocus`; then `docker compose up -d` |
| Nextcloud | `occ config:system:set trusted_domains 1 --value=<app-host>`; `overwritehost`; `overwrite.cli.url=https://<app-host>`; keep `overwriteprotocol=https` and `trusted_proxies` (Docker bridge gateway) |
| Mattermost | System Console → Web Server → Site URL `https://<app-host>` (after the Caddy site is live) |

Back up `.env` or `config.json` first (`cp -p`, keeps mode 0600).

## 4. Verify

- `curl -sI http://<app-host>` returns `301` to HTTPS.
- `curl -s -o /dev/null -w '%{http_code}' https://<app-host>/` returns `200` or `302`.
- Sign in from a browser; the 2FA prompt appears.

## Undo

Restore the backup file and reload Caddy, or restore the application's `.env`/`config.json`
backup and restart it.
