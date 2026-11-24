# GitLab Omnibus role

Targets the observed GitLab CE 19.5.3 installation on Ubuntu 24.04, with an
external PostgreSQL database and private HTTPS.

## Required secret inputs

Supply these variables through Ansible Vault or an external secret provider. The GitLab database password is read from `matrix_postgresql_role_passwords.gitlab`, the same mapping used by the PostgreSQL role:

- `matrix_gitlab_tls_certificate` (PEM certificate, including intermediates if required)
- `matrix_gitlab_tls_private_key` (PEM private key)
- `matrix_gitlab_tls_ca_certificate` (PEM private CA certificate)

The role writes the server private key as root-only and the certificate as
root-readable. It adds a placement-derived `/etc/hosts` entry so GitLab's
private hostname resolves without internal DNS. Secret-bearing tasks suppress task output with `no_log`.

## Behavior

- Adds the official GitLab CE apt repository.
- Installs the exact catalog version (`matrix_versions.gitlab`).
- Installs the private certificate/key before package configuration.
- Loads `/etc/gitlab/gitlab.matrix.rb` from the existing `/etc/gitlab/gitlab.rb`
  using Omnibus `from_file`; it does not replace the main configuration file.
- Disables bundled PostgreSQL and configures the generated external PostgreSQL
  endpoint with `sslmode=require`.
- Configures NGINX for private HTTPS on the topology-generated addresses.
- Installs the private CA into the host trust store and checks the readiness
  endpoint using certificate validation.
- Does not expose port 80 or configure public DNS.

## Notes

The GitLab package's post-install hook may run `gitlab-ctl reconfigure`. The
managed configuration fragment and certificate files are prepared before
package installation so the package hook sees the intended external database
and HTTPS configuration.

The external PostgreSQL database and `gitlab` role must already exist. Enable
the PostgreSQL role's database provisioning with `matrix_manage_postgresql_databases=true`
and supply the `gitlab` role password in `matrix_postgresql_role_passwords.gitlab` via Vault. This is the single source of truth used by both roles. This role does not migrate data from an existing GitLab instance.
