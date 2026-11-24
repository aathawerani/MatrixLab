# Runbook: GitLab private HTTPS and Runner registration

Performed on the GCP reference environment on 24 November 2026. GitLab stays private (no
public DNS); its certificate comes from a private, name-constrained CA on VM01.

## 1. CA and server certificate (VM01)

```bash
sudo bash scripts/private-ca/issue-gitlab-certificate.sh gitlab.matrix-dev.internal 10.20.0.2 matrix-dev.internal 10.20.0.0/255.255.0.0
```

- CA: `/etc/gitlab/private-ca/` (mode 0700, root). `ca.key` never leaves VM01; anyone holding it
  can issue certificates for `*.matrix-dev.internal`.
- Name constraints limit the CA to `matrix-dev.internal` names and `10.20.0.0/16` addresses.
- Server certificate: `/etc/gitlab/ssl/gitlab.matrix-dev.internal.{crt,key}`, valid 825 days.
  Re-run the script before expiry, then `gitlab-ctl reconfigure`.
- The CA certificate is copied to `/etc/gitlab/trusted-certs/` for GitLab's own tools.
- Record the CA's SHA-256 fingerprint; clients compare against it before trusting the CA.

## 2. Switch GitLab to HTTPS (VM01)

1. `cp -p /etc/gitlab/gitlab.rb /etc/gitlab/gitlab.rb.before-https`
2. Add `10.20.0.2 gitlab.matrix-dev.internal` to `/etc/hosts`.
3. In `gitlab.rb`: `external_url "https://gitlab.matrix-dev.internal"` and

   ```ruby
   nginx['listen_addresses'] = ["127.0.0.1", "10.20.0.2"]
   nginx['listen_port'] = 443
   nginx['listen_https'] = true
   nginx['ssl_certificate'] = "/etc/gitlab/ssl/gitlab.matrix-dev.internal.crt"
   nginx['ssl_certificate_key'] = "/etc/gitlab/ssl/gitlab.matrix-dev.internal.key"
   nginx['redirect_http_to_https'] = false
   ```

   These match the GitLab role's `gitlab.matrix.rb.j2`. On the reference VM they were added to
   `gitlab.rb` directly; the role writes them to `/etc/gitlab/gitlab.matrix.rb` via `from_file`.
4. `gitlab-ctl reconfigure` (about a minute of downtime). GitLab then listens on 443 only.
5. Check: `curl --cacert /etc/gitlab/trusted-certs/matrix-dev-private-ca.crt https://gitlab.matrix-dev.internal/users/sign_in` returns 200.

## 3. Administrator access from a Mac

1. Copy the CA certificate (`gcloud compute scp ... :/etc/gitlab/trusted-certs/matrix-dev-private-ca.crt`)
   and compare its SHA-256 fingerprint with VM01's.
2. `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain <file>`
3. `127.0.0.1 gitlab.matrix-dev.internal` in `/etc/hosts`.
4. `scripts/client/gitlab-tunnel.zsh`: IAP tunnel to SSH plus `sudo ssh -L 443:...`. A plain
   `gcloud compute ssh -- -L 443:...` fails because macOS does not let a normal user bind port 443.

## 4. Security baseline in GitLab

Reset `root` with `gitlab-rake "gitlab:password:reset[root]"` (hidden prompt), then in the
Admin area: sign-up disabled, two-factor enforced, Web IDE single-origin fallback disabled.

## 5. Runner (VM02)

1. Install the CA as `/etc/gitlab-runner/certs/gitlab.matrix-dev.internal.crt` (check the
   fingerprint) and add `10.20.0.2 gitlab.matrix-dev.internal` to `/etc/hosts`.
2. Create an instance runner in *Admin → CI/CD → Runners* (tags and "run untagged" are set there).
3. Register with the token entered at a hidden prompt:

   ```bash
   read -rsp 'Runner token: ' CI_SERVER_TOKEN; echo; export CI_SERVER_TOKEN
   sudo --preserve-env=CI_SERVER_TOKEN gitlab-runner register --non-interactive \
     --url https://gitlab.matrix-dev.internal \
     --tls-ca-file /etc/gitlab-runner/certs/gitlab.matrix-dev.internal.crt \
     --name matrix-dev-vm-02 --executor docker --docker-image alpine:3.21 \
     --docker-cpus 2 --docker-memory 4g
   unset CI_SERVER_TOKEN
   ```

4. Check with `gitlab-runner verify`. **Do not run `gitlab-runner list`; it prints the
   authentication token.** If a token is exposed, rotate it with
   `gitlab-runner reset-token --name matrix-dev-vm-02`.

CPU and memory limits keep CI jobs from starving PostgreSQL on the same VM; `concurrent = 1`.
