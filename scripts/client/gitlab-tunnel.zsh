# Administrator access to the private GitLab web interface through IAP (macOS, zsh).
# Prerequisites: gcloud authenticated; the private CA trusted in the System keychain;
# "127.0.0.1 gitlab.matrix-dev.internal" in /etc/hosts.
# Source this file from ~/.zshrc and set GITLAB_TUNNEL_USER to your VM login name,
# then run: gitlab-tunnel   (Ctrl+C closes everything)
#
# sudo is needed only for ssh to bind local port 443, so GitLab's URLs work unchanged.
gitlab-tunnel() {
  local log=/tmp/iap-vm01.log
  gcloud compute start-iap-tunnel matrix-dev-vm-01 22 --local-host-port=localhost:2222 --zone us-central1-b >"$log" 2>&1 &
  local iap=$!
  {
    local i; for i in {1..30}; do nc -z localhost 2222 2>/dev/null && break; sleep 1; done
    if ! nc -z localhost 2222 2>/dev/null; then echo "IAP tunnel did not start:"; cat "$log"; return 1; fi
    echo "GitLab: https://gitlab.matrix-dev.internal  (Ctrl+C to close)"
    sudo ssh -i "$HOME/.ssh/id_ed25519" -p 2222 -o HostKeyAlias=matrix-dev-vm-01 -N -L 443:127.0.0.1:443 "${GITLAB_TUNNEL_USER:?set GITLAB_TUNNEL_USER to your VM login}"@localhost
  } always {
    kill $iap 2>/dev/null
  }
}
