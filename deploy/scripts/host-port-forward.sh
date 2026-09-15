#!/usr/bin/env bash
# Publish Service on 127.0.0.1:8002 for Tailscale Serve.
# Prefer kubectl port-forward (hostPort CNI portmap needs xt_multiport, missing here).
# Alternative: sudo tailscale serve --bg http://NODE_IP:30002
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
sudo pkill -f 'port-forward.*8002:8002' 2>/dev/null || true
sleep 0.5
nohup sudo kubectl -n sequoia-x port-forward --address 127.0.0.1 svc/sequoia-x 8002:8002 \
  >/tmp/pf-8002.log 2>&1 &
echo "kubectl port-forward 127.0.0.1:8002 -> svc/sequoia-x:8002 (pid $!)"
sleep 1
curl -sf http://127.0.0.1:8002/api/health && echo
