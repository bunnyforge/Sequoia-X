# Sequoia-X — Docker Compose deploy

Production path is **Docker Compose** (Postgres 16 + app).

One-shot (installs Docker + Compose if needed, builds and starts):

```bash
# Linux
./install.sh

# Windows (PowerShell, from the repo root)
powershell -ExecutionPolicy Bypass -File deploy\scripts\bootstrap.ps1
```

Already have Docker and only want to start:

```bash
./install.sh --skip-install
```

Data lives in **`/workspace/sequoia-x/postgres`** (bind-mounted to `/var/lib/postgresql/data`). Install/bootstrap scripts create that directory if missing. It is outside the git repo.

---

## 1. Requirements

- Docker Engine + Compose v2
- About 2Gi RAM for both containers
- Port `8002` free

---

## 2. First start

```bash
git clone https://github.com/bunnyforge/Sequoia-X.git
cd Sequoia-X
mkdir -p /workspace/sequoia-x/postgres   # created automatically by ./install.sh
docker compose up -d --build
docker compose ps
curl -sS http://127.0.0.1:8002/api/health
```

Empty Postgres is OK: the API creates `stock_daily`, sync, and strategy tables on boot, and seeds default sync/strategy rows (`start_date=2024-01-01`, strategies off). No `.env` file is required.

Web UI: `http://127.0.0.1:8002/`

Optional Tailscale:

```bash
sudo tailscale serve --bg 8002
```

---

## 3. Layout

| Path | Role |
|------|------|
| `docker-compose.yml` | `db` (postgres:16-bookworm) + `app` |
| `/workspace/sequoia-x/postgres` | Postgres data directory (copy this to migrate machines) |
| `Dockerfile` | frontend build + Python API |

`DATABASE_URL` inside the app container:

```
postgresql://sequoia:sequoia@db:5432/sequoia
```

Postgres is not published on the host. The user/password in `docker-compose.yml` (`sequoia`/`sequoia`) is **internal Compose plumbing** so Postgres can start; it is not application config and is not stored as the only copy inside the database.

Product settings (strategies, sync interval/retries, `start_date`, run history) live in Postgres and the Web UI. Port `8002` is the process bind in Compose/Dockerfile.

---

## 4. Day-to-day

```bash
docker compose logs -f app
docker compose restart app
docker compose down          # stop; keep /workspace/sequoia-x/postgres
docker compose down -v       # do not use — there is no named volume; data is the bind mount
```

Rebuild after code changes:

```bash
docker compose up -d --build
```

---

## 5. Move to another computer

Same Postgres **major** version (`postgres:16-bookworm`), same OS family (Linux↔Linux or WSL↔WSL). Stop Compose before copying. You do **not** copy a `.env` file.

On the old machine:

```bash
docker compose stop
# copy the actual pgdata dir (not only the git repo)
# example: rsync -aH --numeric-ids /workspace/sequoia-x/postgres/ other:/workspace/sequoia-x/postgres/
```

On the new machine (same `docker-compose.yml` in the repo):

```bash
docker compose up -d --build
curl -sS http://127.0.0.1:8002/api/health
```

Do not copy `/workspace/sequoia-x/postgres` while Postgres is running. After start, strategies, sync settings, and market data should already be there. A durable `/workspace` survives reboot; ephemeral disks still lose it.

---

## 6. Logical backup (optional)

```bash
./deploy/scripts/backup-db.sh
# writes backups/sequoia-YYYYMMDD-HHMMSS.sql.gz
```

Restore into a **running** stack (replaces objects in db `sequoia`):

```bash
./deploy/scripts/restore-db.sh backups/sequoia-YYYYMMDD-HHMMSS.sql.gz
docker compose restart app
```

The dump includes market data **and** product config (`sync_config`, `strategy_config`, run history). Use this instead of copying `/workspace/sequoia-x/postgres` when you cannot copy the data directory.

---

## 7. Check

```bash
docker compose exec app python -c "
from sequoia_x.db import connect, table_exists
import os
with connect(os.environ['DATABASE_URL']) as conn:
    print('postgres', conn.postgres)
    for t in ['stock_daily', 'sync_config', 'strategy_config']:
        print(t, table_exists(conn, t))
"
```
