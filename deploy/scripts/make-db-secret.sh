#!/usr/bin/env bash
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
NS=sequoia-x
USER=$(sudo kubectl get secret sequoia-pg-app -n "$NS" -o jsonpath='{.data.user}' | base64 -d)
PASS=$(sudo kubectl get secret sequoia-pg-app -n "$NS" -o jsonpath='{.data.password}' | base64 -d)
DB=$(sudo kubectl get secret sequoia-pg-app -n "$NS" -o jsonpath='{.data.dbname}' | base64 -d)
# URL-encode password
PASS_ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$PASS")
URI="postgresql://${USER}:${PASS_ENC}@sequoia-pg-rw.${NS}.svc.cluster.local:5432/${DB}?sslmode=require"
sudo kubectl -n "$NS" create secret generic sequoia-x-db \
  --from-literal=DATABASE_URL="$URI" \
  --dry-run=client -o yaml | sudo kubectl apply -f -
echo "Applied secret sequoia-x-db (DATABASE_URL -> CNPG FQDN + sslmode=require)"
