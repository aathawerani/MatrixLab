# OpenProject role

Deploys the inventory-observed OpenProject Compose topology with external PostgreSQL. It does not create, migrate, restore, or back up database contents. It excludes the observed no-op `db` helper, legacy backup/upgrade utilities, and `autoheal` (which mounted the Docker socket read-write).

The VM03 proxy build context has been captured in `files/proxy/` from the observed Dockerfile and Caddyfile template. The proxy routes `/hocuspocus*` to `hocuspocus:1234` and all other requests to `web:8080`; the default `APP_HOST=web` targets the observed `web` service on the shared frontend network; override `matrix_openproject_proxy_app_host` if the service name changes.

Provide `matrix_openproject_runtime_env` and `matrix_openproject_hocuspocus_env` through Ansible Vault or an approved secret provider. Runtime keys are checked against `defaults/main.yml`. Provide `matrix_openproject_proxy_base_image` as a Caddy image pinned by registry digest (for example `caddy@sha256:<verified-digest>`); no mutable `caddy:2` default is used. The proxy build uses `APP_HOST=web`. The OpenProject, Hocuspocus and Memcached images are pinned to observed registry digests emitted by the topology renderer.

Docker Engine and the Compose plugin must already be installed. The role validates Compose before starting services and publishes only generated listener addresses. Compose configuration is mode `0600` because it contains secrets. The seeder is a one-shot first-install task. This role is for clean recreation, not in-place upgrade or data-preserving reconfiguration.
