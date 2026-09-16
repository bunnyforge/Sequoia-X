#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
file="${1:?usage: restore-db.sh backups/sequoia-YYYYMMDD-HHMMSS.db}"
[[ -f "$file" ]] || { echo "missing backup $file" >&2; exit 1; }
dest="${DB_PATH:-data/sequoia_v2.db}"
mkdir -p "$(dirname "$dest")"
# Stop writers first if the web app is running (docker compose stop app).
cp "$file" "$dest"
rm -f "${dest}-wal" "${dest}-shm"
echo "restored $file -> $dest"
