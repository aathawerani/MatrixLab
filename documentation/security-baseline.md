# Security baseline (standing rules)

These rules apply to every application in every environment. They were applied by hand to
the GCP reference environment on 23–24 November 2026. Most are application settings stored
in each application's database, so the Ansible roles cannot enforce them yet; follow
`runbooks/expose-application.md` after every fresh deployment.

## Accounts and sign-in

| Rule | OpenProject | Nextcloud | Mattermost | SonarQube | GitLab |
|---|---|---|---|---|---|
| Self-registration disabled | `self_registration = 0` | no registration app installed | `EnableOpenServer = false`; accounts only by invite link | n/a (private) | sign-up disabled |
| Default admin password changed before exposure | done | done (`ncadmin`) | first account created privately through the tunnel | done | `root` reset with `gitlab-rake` |
| Two-factor authentication enforced | TOTP, enforced for all users | `twofactorauth:enforce --on`, TOTP | MFA enabled and enforced | n/a (private) | enforced |

Never expose an application whose first account has not been created: on Mattermost (and
similar applications) the first account to register becomes system administrator.

Outgoing email is not configured on any application, so password-reset emails do not work.
Store administrator passwords and 2FA backup codes in a password manager.

GitLab also has the Web IDE single-origin fallback disabled (the Web IDE loads its assets from
`*.cdn.web-ide.gitlab-static.net` in the user's browser).

## Exposure

- Caddy on VM03 is the only public entry point (TCP 80/443). Applications listen on
  `127.0.0.1` only.
- Public sites use Let's Encrypt certificates (HTTP-01). HTTP redirects to HTTPS.
- GitLab, SonarQube, PostgreSQL and Redis are private and have no public DNS record.
- Never print runner or API tokens in shell output (`gitlab-runner list` does). Rotate any
  token that is exposed.
- Expect failed sign-in attempts in application logs: new certificates are published in
  Certificate Transparency logs and scanners probe new names within minutes.

## Packages

Application packages are held with `apt-mark hold` so routine OS updates cannot upgrade them.
Upgrading any of them is a planned change: read the release notes, back up configuration,
then update the version pin in `applications/catalog.yml`.

| VM | Held packages |
|---|---|
| VM01 | `gitlab-ce`, Docker packages |
| VM02 | `postgresql-17`, `postgresql-common`, `postgresql-client-17`, `postgresql-client-common`, `libpq5`, `gitlab-runner`, `gitlab-runner-helper-images`, Docker packages |
| VM03 | `mattermost`, `caddy`, `postgresql-client-17`, `postgresql-client-common`, `libpq5`, Docker packages |

Docker packages: `docker-ce`, `docker-ce-cli`, `docker-ce-rootless-extras`, `containerd.io`,
`docker-compose-plugin`, `docker-buildx-plugin`.

Install OS updates with `sudo apt-get upgrade --with-new-pkgs` (without `--with-new-pkgs`
apt keeps new kernels back). The VMs are stopped and started on a schedule, so a
pending kernel update is applied at the next start without a manual reboot.

The Ansible roles do not yet apply these holds.

## Containers

- No container may mount the Docker socket. OpenProject's `autoheal` service is disabled with
  `profiles: ["disabled"]` and `volumes: !reset []` in its Compose override.
- Remove one-off test containers (`docker run hello-world` leaves a stopped container with a
  random name).
