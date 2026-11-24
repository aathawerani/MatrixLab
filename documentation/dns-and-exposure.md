# DNS and exposure plan for alitech.example

`alitech.example` is a placeholder for the real public domain, which is not committed.

Public DNS records (created 23 November 2026, all pointing to VM03's external IP):
- `projects.alitech.example` — OpenProject (live)
- `cloud.alitech.example` — Nextcloud (live)
- `chat.alitech.example` — Mattermost (live)
- `support.alitech.example` — reserved for a future helpdesk; Caddy does not serve it yet
- `demo.alitech.example` — single demo hostname (record requested; see below)

The committed topology writes these as `projects.{public_domain}` etc. Render with the real
domain by setting `MATRIX_PUBLIC_DOMAIN` (see README).

GitLab is private and served over HTTPS with a private CA (since 24 November 2026; see
`runbooks/gitlab-private-https.md`). Private services use private IPs. GitLab uses
`gitlab.matrix-dev.internal`; that name must resolve to the GitLab private IP and its
certificate must include the same name. Internal DNS is not mandatory: a managed hosts-file
entry on the VMs and developer clients is an alternative. No DNS or hosts-file changes are
made by this repository patch. Other private services can continue using generated private-IP
endpoints: Container Registry, SonarQube, PostgreSQL, Redis, Qdrant/Prometheus if deployed.

Do not create DNS records or expose services until approved. Caddy should be the only public entry point. Demo workloads must be isolated.

## Demo hostnames — wildcard not available (20 November 2026)

The DNS administrator cannot create `*.demo.alitech.example`. Two alternatives are under
consideration; neither is configured yet.

### Option A — one hostname, one path per demo

`demo.alitech.example/esb`, `demo.alitech.example/wallet`, ... One DNS record and one Let's Encrypt
certificate (HTTP-01). Caddy routes each path prefix to that demo's container, for example:

```caddyfile
demo.alitech.example {
    handle_path /esb/* {
        reverse_proxy 127.0.0.1:9101
    }
    handle_path /wallet/* {
        reverse_proxy 127.0.0.1:9102
    }
    handle {
        respond "Not found" 404
    }
}
```

Constraints:
- Each demo application must support running under a sub-path (base path / context path).
  Apps that emit absolute links such as `/login` or `/static/app.js` break otherwise.
- All demos share one browser origin: cookies, localStorage and service workers are not
  isolated between demos. Demos must not rely on origin isolation for security.
- WebSocket and redirect handling must be checked for each demo.

### Option B — one ordinary DNS record per demo

`esb.demo.alitech.example`, `wallet.demo.alitech.example`, ... (or `esb-demo.alitech.example`). These are
ordinary, non-wildcard records, created one at a time when a demo is approved. Caddy obtains a
certificate per hostname automatically. Apps run unchanged at `/` and each demo gets its own
browser origin.

### Decision (20 November 2026)

**Option A is chosen:** all demos are served from the single hostname `demo.alitech.example`, one
path prefix per demo (`/esb`, `/wallet`, ...). Adding a demo requires no DNS change, only a
new Caddy route. Only one record, `demo.alitech.example`, is requested from the DNS administrator,
together with the approval for public access to VM03.

Consequences accepted:
- A demo that cannot run under a sub-path is a separate problem for that demo; it is not
  solved by changing the DNS scheme.
- Demos share one browser origin, so isolation between demos relies on the server side
  (separate containers, networks, databases and no shared credentials), not on the browser.

Not yet implemented: the topology `routes:` model maps whole hostnames to applications. Path
routes for demos need a renderer and Caddy-role change in a later increment.
