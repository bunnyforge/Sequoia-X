#!/usr/bin/env bash
# After enabling Serve on the tailnet, run this to publish :8002
set -euo pipefail
sudo tailscale serve --bg 8002
sudo tailscale serve status
