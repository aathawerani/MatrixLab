#!/usr/bin/env bash
# Build (or re-apply) a local single-node environment from this repository.
# Run on the VM itself, from a clone of the repository:
#
#   bash scripts/lab/build.sh                      # environments/hyperv/lab
#   bash scripts/lab/build.sh environments/hyperv/lab --check --diff
#
# Installs Ansible if it is missing, renders the topology and runs the site playbook
# against this machine. Asks for your sudo password once. Safe to re-run.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
env_dir=${1:-environments/hyperv/lab}
shift || true

if ! command -v ansible-playbook >/dev/null 2>&1; then
  echo "== Installing Ansible"
  sudo apt-get update -qq
  sudo apt-get install -y ansible-core python3-yaml python3-jinja2
fi

cd "$repo"
echo "== Installing required Ansible collections"
ansible-galaxy collection install -r configuration/requirements.yml >/dev/null

echo "== Rendering $env_dir"
python3 scripts/render_environment.py "$env_dir"
provider=$(python3 -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))['provider'])" "$env_dir/topology.yml")
name=$(python3 -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))['environment'])" "$env_dir/topology.yml")
inventory="$repo/generated/$provider/$name/inventory.yml"

expected=$(python3 -c "import yaml,sys; print(next(iter(yaml.safe_load(open(sys.argv[1]))['all']['hosts'])))" "$inventory")
if [ "$(hostname)" != "$expected" ]; then
  echo "This machine is '$(hostname)' but the topology describes '$expected'. Nothing changed." >&2
  exit 1
fi

echo "== Applying (sudo password is asked once as 'BECOME password')"
cd "$repo/configuration"
ansible-playbook -i "$inventory" playbooks/site.yml -e matrix_allow_changes=true -K "$@"
