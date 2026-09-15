#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p backups
stamp="$(date +%Y%m%d-%H%M%S)"
out="backups/sequoia-${stamp}.sql.gz"
docker compose exec -T db pg_dump -U sequoia --no-owner --no-acl sequoia | gzip > "$out"
echo "wrote $out"
