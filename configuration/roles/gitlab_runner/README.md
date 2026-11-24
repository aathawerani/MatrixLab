# GitLab Runner role

Targets GitLab Runner 19.5.3 on Ubuntu 24.04.

## Behavior

- Adds the official GitLab Runner apt repository.
- Installs the exact catalog version (`matrix_versions.gitlab_runner`).
- Installs the GitLab private CA at
  `/etc/gitlab-runner/certs/gitlab.matrix-dev.internal.crt` (hostname and URL are
  generated from topology).
- GitLab Runner automatically loads a hostname-specific certificate from its
  `certs` directory when connecting to GitLab over HTTPS.
- Preserves `/etc/gitlab-runner/config.toml`; it does not replace registration
  data or tokens.
- Enables and starts the systemd service.

## Deliberately not performed

`gitlab-runner register` is not run. Registration requires a one-time token
and an explicit operator action. The role installs and prepares trust but
does not create a runner registration or assume executor type, tags, or
privileged mode.

## Registration used on the reference environment

See `documentation/runbooks/gitlab-private-https.md`: Docker executor, default image
`alpine:3.21`, `--docker-cpus 2 --docker-memory 4g`, `concurrent = 1`, token entered at a hidden
prompt and passed through `CI_SERVER_TOKEN`. The values are recorded in the topology under
`app_settings.gitlab_runner.registration`. Never run `gitlab-runner list` in shared output: it
prints the authentication token.
