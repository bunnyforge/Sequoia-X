#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
file="${1:?usage: restore-db.sh backups/sequoia-YYYYMMDD-HHMMSS.sql.gz}"
if [[ "$file" == *.gz ]]; then
  gzip -dc "$file" | docker compose exec -T db psql -U sequoia -d sequoia
else
  docker compose exec -T db psql -U sequoia -d sequoia < "$file"
fi
echo "restored $file"
