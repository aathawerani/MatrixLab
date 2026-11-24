# Mattermost role

Installs the observed Mattermost Debian package from the official Mattermost APT repository at the exact catalog version. APT validates package hashes against the repository's signed metadata; the observed package SHA256 is retained in the catalog as inventory evidence.

The role requires the **complete Mattermost config.json** as a Vault-protected mapping (`matrix_mattermost_config`). This is intentional: the existing file contains settings beyond the safe inventory subset, and the database DataSource is a secret. The role will not replace it with a partial fragment. The supplied mapping must include PostgreSQL `SqlSettings.DriverName` and `SqlSettings.DataSource`, the topology-matching `ServiceSettings.SiteURL` and `ListenAddress`, and local `FileSettings` path. The SiteURL, listener address, and database host are checked against generated topology values. No config values are logged.

The package is installed with service autostart suppressed; the protected configuration is installed before Mattermost is enabled and started. The native service runs as `mattermost:mattermost`, with its package-provided systemd unit. First startup against a freshly provisioned empty database may initialize Mattermost's schema. No existing data is migrated or restored.
