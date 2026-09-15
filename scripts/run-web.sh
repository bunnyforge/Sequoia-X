#!/usr/bin/env bash
# Restart Sequoia-X web API (serves frontend from frontend/dist on :8002)
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data
exec uv run uvicorn api.main:app --host 0.0.0.0 --port 8002
