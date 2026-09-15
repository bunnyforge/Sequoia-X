#!/usr/bin/env bash
# Build sequoia-x:local with docker (vfs) and import into k3s containerd.
# Stop dockerd afterwards so iptables-legacy FORWARD DROP does not break CNI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
cd "$ROOT"

if ! command -v docker >/dev/null; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
fi
if ! sudo docker info >/dev/null 2>&1; then
  sudo dockerd --storage-driver=vfs --host=unix:///var/run/docker.sock >/tmp/dockerd.log 2>&1 &
  for i in $(seq 1 30); do
    sudo docker info >/dev/null 2>&1 && break
    sleep 1
  done
fi
sudo docker build -t sequoia-x:local .
sudo docker save sequoia-x:local -o /tmp/sequoia-x-local.tar
sudo k3s ctr images import /tmp/sequoia-x-local.tar
rm -f /tmp/sequoia-x-local.tar
# Stop docker to avoid iptables-legacy FORWARD DROP
if pgrep -x dockerd >/dev/null; then
  sudo kill "$(pgrep -x dockerd | head -1)" || true
fi
"$(dirname "$0")/fix-cni-forward.sh" || true
echo "Imported docker.io/library/sequoia-x:local into k3s containerd"
sudo k3s ctr images ls | grep sequoia-x || true
