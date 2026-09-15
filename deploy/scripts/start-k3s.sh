#!/usr/bin/env bash
# Start k3s manually (this host does NOT use systemd for k3s).
# Overlay snapshotter fails on this box; use native. Single-node CNI: host-gw.
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
LOG="${K3S_LOG:-/workspace/logs/k3s.log}"
mkdir -p "$(dirname "$LOG")"
if pgrep -x k3s >/dev/null; then
  echo "k3s already running (pid $(pgrep -x k3s | head -1))"
  exit 0
fi
# Restore pod forwarding in case docker.io left iptables-legacy FORWARD DROP.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -x "$SCRIPT_DIR/fix-cni-forward.sh" ]]; then
  "$SCRIPT_DIR/fix-cni-forward.sh" || true
fi
sudo /usr/local/bin/k3s server \
  --write-kubeconfig-mode 644 \
  --tls-san 100.76.76.54 \
  --tls-san cursor.tail87959b.ts.net \
  --disable metrics-server \
  --disable traefik \
  --snapshotter native \
  --flannel-backend host-gw \
  >>"$LOG" 2>&1 &
echo "k3s server started, logging to $LOG"
# wait for apiserver
for i in $(seq 1 60); do
  if sudo kubectl get nodes >/dev/null 2>&1; then
    echo "apiserver ready"
    "$SCRIPT_DIR/fix-cni-forward.sh" || true
    exit 0
  fi
  sleep 2
done
echo "timed out waiting for apiserver; see $LOG"
exit 1
