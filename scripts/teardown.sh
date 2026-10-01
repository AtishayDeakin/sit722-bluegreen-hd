#!/usr/bin/env bash
# Removes everything this project created in the local cluster
# (both colours, databases and their data, monitoring).
#   ./scripts/teardown.sh
set -euo pipefail
cd "$(dirname "$0")/../infra/platform"
read -r -p "Delete the KoalaTech platform and ALL its data from the local cluster? [y/N] " answer
[ "$answer" = "y" ] || { echo "Cancelled."; exit 0; }
terraform init -input=false -backend-config="path=$HOME/.koalatech/terraform.tfstate" >/dev/null
terraform destroy -input=false -auto-approve
echo "Done. Release Deployments were inside the deleted namespace, so they are gone too."
