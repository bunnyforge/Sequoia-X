#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p backups
src="${DB_PATH:-data/sequoia_v2.db}"
[[ -f "$src" ]] || { echo "no SQLite file at $src" >&2; exit 1; }
stamp="$(date +%Y%m%d-%H%M%S)"
out="backups/sequoia-${stamp}.db"
sqlite3 "$src" ".backup '$out'"
echo "wrote $out"
