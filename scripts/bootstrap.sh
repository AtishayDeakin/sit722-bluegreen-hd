#!/usr/bin/env bash
# One-time (and repeatable) provisioning of the platform from your terminal.
# The pipeline runs exactly the same Terraform on every release afterwards.
#   ./scripts/bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/../infra/platform"

STATE_DIR="$HOME/.koalatech"
mkdir -p "$STATE_DIR"

echo "==> terraform init (state: $STATE_DIR/terraform.tfstate)"
terraform init -input=false -backend-config="path=$STATE_DIR/terraform.tfstate"

echo "==> terraform apply (first run downloads Prometheus/Grafana and takes a few minutes)"
terraform apply -input=false -auto-approve

echo
echo "Platform ready:"
terraform output -raw production_url; echo "   production (empty until the first release)"
terraform output -raw preview_url;    echo "   preview"
terraform output -raw grafana_url;    echo "   Grafana: Dashboards > KoalaTech - Blue/Green Releases"
echo
echo "App admin login:     admin / \$(terraform -chdir=infra/platform output -raw app_admin_password)"
echo "Grafana admin login: admin / \$(terraform -chdir=infra/platform output -raw grafana_admin_password)"
