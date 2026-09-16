#!/usr/bin/env bash
# Linux one-shot: Docker + Tailscale hostname "cursor" + Compose. See deploy/scripts/bootstrap.sh.
set -euo pipefail
exec "$(cd "$(dirname "$0")" && pwd)/deploy/scripts/bootstrap.sh" "$@"
