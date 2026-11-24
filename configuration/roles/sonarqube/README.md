# SonarQube role

Deploys the inventory-pinned SonarQube Community image with external PostgreSQL and four named data volumes. The database password is supplied through Ansible Vault or an approved external secret provider. The role does not create databases, migrate data, restore data, or perform upgrades.

The host kernel requirements (`vm.max_map_count=524288` and `fs.file-max=131072`) are applied persistently through `/etc/sysctl.d/99-matrix-sonarqube.conf` and immediately to the running kernel. These are host-level changes and require the deployment approval gate.

The application listener uses only topology-generated addresses. In the current GCP reference topology it is loopback-only. Docker Engine and Compose plugin must already be installed. Image is pinned to the observed registry digest. This role is for clean recreation, not in-place migration or upgrade.
