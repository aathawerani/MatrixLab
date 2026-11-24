# PostgreSQL role

This role currently targets the observed Ubuntu 24.04 / PostgreSQL 17 deployment.

## Safety and behavior

- Requires `matrix_allow_changes=true`.
- Installs PostgreSQL 17 from PGDG at the observed package version by default.
- Writes placement-derived settings and a separate generated HBA include.
- Adds `include_if_exists` to the active `pg_hba.conf`; the generated HBA file is not left inert.
- Uses the Ubuntu `ssl-cert` snake-oil certificate paths observed in the reference deployment. This provides encryption but not authenticated server identity. A private-PKI PostgreSQL certificate should replace it before treating database TLS as strongly authenticated.
- Does not migrate or restore database contents.
- Does not create application roles/databases unless `matrix_manage_postgresql_databases=true`.

## Database provisioning

Install the collection first:

```bash
ansible-galaxy collection install -r requirements.yml
```

Provide `matrix_postgresql_role_passwords` through Ansible Vault or an external secret provider. The same `gitlab` entry is consumed by the GitLab role, avoiding two independently configured GitLab database passwords. The mapping keys must match the generated PostgreSQL role names. Never put passwords in the catalog or topology.

Then explicitly opt in:

```bash
ansible-playbook -i ../generated/gcp/<environment>/inventory.yml \
  playbooks/site.yml \
  -e matrix_allow_changes=true \
  -e matrix_manage_postgresql_databases=true \
  --ask-vault-pass
```

Do not run this against the existing reference VMs. The command above is documentation for a newly provisioned, disposable target.
