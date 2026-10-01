#!/usr/bin/env bash
# Checks everything the self-hosted runner machine needs, and says how to fix
# anything that is missing. Safe to run any time; it changes nothing.
#   ./scripts/check-prereqs.sh

CONTEXT="${BG_KUBE_CONTEXT:-docker-desktop}"
problems=0

ok()   { printf "  \033[32mOK\033[0m    %s\n" "$1"; }
bad()  { printf "  \033[31mFIX\033[0m   %s\n        -> %s\n" "$1" "$2"; problems=$((problems + 1)); }
warn() { printf "  \033[33mNOTE\033[0m  %s\n" "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

echo "Checking tools..."
if have docker; then ok "docker CLI ($(docker --version | cut -d, -f1))"; else bad "docker CLI not found" "Install Docker Desktop: https://www.docker.com/products/docker-desktop/"; fi
if docker info >/dev/null 2>&1; then ok "Docker Desktop is running"; else bad "Docker is not running" "Open Docker Desktop and wait until it says 'Engine running'"; fi
if have kubectl; then ok "kubectl ($(kubectl version --client -o json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["clientVersion"]["gitVersion"])' 2>/dev/null))"; else bad "kubectl not found" "brew install kubectl"; fi
if have terraform; then
  tf_version=$(terraform version -json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["terraform_version"])' 2>/dev/null)
  ok "terraform ${tf_version}"
else
  bad "terraform not found" "brew tap hashicorp/tap && brew install hashicorp/tap/terraform"
fi
if have python3 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then ok "python3 $(python3 -c 'import platform;print(platform.python_version())')"; else bad "python3 3.9+ not found" "xcode-select --install   (or: brew install python)"; fi
if have git; then ok "git"; else bad "git not found" "xcode-select --install"; fi
if have helm; then ok "helm (optional, handy for debugging)"; else warn "helm not installed (optional; Terraform installs charts itself)"; fi

echo "Checking Kubernetes..."
if have kubectl && kubectl config get-contexts -o name 2>/dev/null | grep -qx "$CONTEXT"; then
  ok "kube context '$CONTEXT' exists"
  if kubectl --context "$CONTEXT" get nodes >/dev/null 2>&1; then
    ok "cluster reachable ($(kubectl --context "$CONTEXT" get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ') node(s))"
  else
    bad "cluster not reachable" "Docker Desktop > Settings > Kubernetes > Enable Kubernetes, then Apply & restart"
  fi
else
  bad "kube context '$CONTEXT' not found" "Docker Desktop > Settings > Kubernetes > Enable Kubernetes"
fi

echo "Checking resources..."
if docker info >/dev/null 2>&1; then
  mem_bytes=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
  mem_gb=$(( mem_bytes / 1024 / 1024 / 1024 ))
  if [ "$mem_gb" -ge 6 ]; then ok "Docker Desktop memory: ${mem_gb} GB"; else bad "Docker Desktop memory is ${mem_gb} GB" "Docker Desktop > Settings > Resources > Memory: set 8 GB"; fi
fi

echo "Checking ports (8080 production, 8081 preview, 3001 Grafana)..."
for port in 8080 8081 3001; do
  owner=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1}')
  if [ -z "$owner" ]; then ok "port $port free"
  elif echo "$owner" | grep -qi "com.docke\|docker\|vpnkit"; then ok "port $port already served by Docker Desktop (platform is up)"
  else bad "port $port is used by '$owner'" "Stop that program, or change the port in infra/platform/variables.tf"; fi
done

echo
if [ "$problems" -eq 0 ]; then
  echo "All good. Next: ./scripts/bootstrap.sh"
else
  echo "$problems thing(s) to fix above, then run this check again."
  exit 1
fi
