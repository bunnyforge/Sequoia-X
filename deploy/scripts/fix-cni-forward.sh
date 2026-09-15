#!/usr/bin/env bash
# Docker's iptables-legacy FORWARD policy DROP breaks k3s pod networking
# when docker.io is installed on the same host. Prefer building images then
# stopping dockerd; always re-open pod CIDR forwarding.
set -euo pipefail
if command -v iptables-legacy >/dev/null; then
  sudo iptables-legacy -P FORWARD ACCEPT || true
  sudo iptables-legacy -C FORWARD -s 10.42.0.0/16 -j ACCEPT 2>/dev/null || \
    sudo iptables-legacy -I FORWARD 1 -s 10.42.0.0/16 -j ACCEPT
  sudo iptables-legacy -C FORWARD -d 10.42.0.0/16 -j ACCEPT 2>/dev/null || \
    sudo iptables-legacy -I FORWARD 1 -d 10.42.0.0/16 -j ACCEPT
  sudo iptables-legacy -C FORWARD -s 10.43.0.0/16 -j ACCEPT 2>/dev/null || \
    sudo iptables-legacy -I FORWARD 1 -s 10.43.0.0/16 -j ACCEPT
  sudo iptables-legacy -C FORWARD -d 10.43.0.0/16 -j ACCEPT 2>/dev/null || \
    sudo iptables-legacy -I FORWARD 1 -d 10.43.0.0/16 -j ACCEPT
fi
echo "CNI forward rules ensured (10.42.0.0/16, 10.43.0.0/16)"
