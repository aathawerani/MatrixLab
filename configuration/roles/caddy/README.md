# Caddy role

Installs the pinned Caddy package from the official Caddy Cloudsmith stable APT repository and generates its Caddyfile from the topology renderer's `matrix_caddy` settings (`caddy` in host vars). The role validates the candidate Caddyfile before reloading the service.

Supported `caddy` settings in `topology.yml`:

| Key | Meaning |
|---|---|
| `bind` | Listener address: `127.0.0.1` for internal-only sites, or the node's private IP for public sites |
| `tls` | `internal` (Caddy's private CA), `acme` (Let's Encrypt; no `tls` directive is written), or any other value written verbatim as `tls <value>` |
| `auto_https` | Global `auto_https` option |
| `redirect_http` | Write an explicit `http://` site per route that redirects to HTTPS |
| `hsts_apps`, `hsts_max_age` | Applications whose site sends `Strict-Transport-Security` |

The GCP reference topology uses public sites: `bind` = VM03's private IP, `tls: acme`, explicit HTTP redirects. ACME certificates and account keys are stored in `/var/lib/caddy/.local/share/caddy`; internal-CA state lives in the same directory. The role does not configure DNS or firewall rules; public DNS records and inbound TCP 80/443 must exist before ACME issuance can succeed.
