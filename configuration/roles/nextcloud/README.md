# Nextcloud role

Deploys the observed Nextcloud Apache image and cron sidecar with external PostgreSQL and a private Redis service. It creates fresh host directories and a named Redis data volume; it does not copy, migrate, restore, or back up existing Nextcloud files or database contents.

Provide `matrix_nextcloud_runtime_env` through Ansible Vault or an approved external secret provider. It must include first-install administrator credentials and the PostgreSQL password. Database host, port, name, user and Redis endpoint are generated from topology and override corresponding values in the supplied dictionary. Nextcloud and Redis images are pinned to the observed registry digests emitted by the topology renderer.

Docker Engine and the Compose plugin must already be installed. The application port is bound only to generated loopback/private addresses; Redis is not published on the host. This is a clean-install role, not a migration or upgrade workflow.
