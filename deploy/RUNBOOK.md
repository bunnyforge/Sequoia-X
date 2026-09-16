# Sequoia-X — local SQLite (optional Docker Compose)

Default path is **local Python** writing **`data/sequoia_v2.db`**. Compose is optional: one `app` container, SQLite on a bind mount.

One-shot Docker (installs Docker + Compose, and on Linux Tailscale, if needed, then builds and starts). **No script edits.** The only optional environment variable is `TS_AUTHKEY` (or `TAILSCALE_AUTHKEY`).

```bash
# Linux (also joins Tailscale as hostname cursor when TS_AUTHKEY / TAILSCALE_AUTHKEY is set)
./install.sh

# Windows (PowerShell, from the repo root; Tailscale/CN2 join is Linux-only)
powershell -ExecutionPolicy Bypass -File deploy\scripts\bootstrap.ps1
```

`--skip-install` skips **all** host package installs (Docker and Tailscale), but still starts Compose, still recovers a broken daemon/plugin on an already-provisioned host, and, if `tailscale` is on PATH, still sets hostname `cursor`.

On Ubuntu+systemd, `get.docker.com` + `systemctl` is enough. On no-systemd / container-like hosts (PID 1 is not systemd; `systemctl start docker` is a no-op), bootstrap starts `dockerd --host=unix:///var/run/docker.sock` in the background (log: `/tmp/dockerd.log`) if `docker info` fails. If `docker compose version` still fails, it downloads the official Compose v2 CLI plugin (`docker-compose-linux-x86_64` / `docker-compose-linux-aarch64`, default `COMPOSE_VER=v2.29.7`) into `/usr/libexec/docker/cli-plugins` and `/usr/local/lib/docker/cli-plugins`.

```bash
./install.sh --skip-install
```

SQLite lives in **`./data`** (override with `SEQUOIA_DATA`). Install/bootstrap scripts create that directory if missing.

Without Docker:

```bash
uv sync
./scripts/run-web.sh
# or: python main.py --backfill
```

---

## 1. Requirements

- Python >= 3.10 for local runs; Docker Engine + Compose v2 if you use `./install.sh`
- Port `8002` free for the web UI
- Linux public access: Tailscale node named **`cursor`** (MagicDNS `cursor.tail87959b.ts.net`). CN2 nginx already proxies `http://185.218.4.107/` → `http://cursor.tail87959b.ts.net:8002`. Do **not** name the Tailscale machine `sequoia`.

---

## 2. First start (Compose)

```bash
git clone https://github.com/bunnyforge/Sequoia-X.git
cd Sequoia-X
mkdir -p data   # created automatically by ./install.sh
docker compose up -d --build
docker compose ps
curl -sS http://127.0.0.1:8002/api/health
```

An empty `data/` directory is OK: the API creates `stock_daily`, sync, and strategy tables on boot, and seeds default sync/strategy rows (`start_date=2024-01-01`, strategies off). No `.env` file is required.

Web UI: `http://127.0.0.1:8002/`

Linux bootstrap installs Tailscale (unless `--skip-install`) and brings the node up as hostname **`cursor`**. It does **not** enable Tailscale Serve; CN2 nginx is the public front.

Unattended join (reusable auth key from the Tailscale admin console, ideally tagged):

```bash
export TS_AUTHKEY='tskey-auth-…'   # or TAILSCALE_AUTHKEY
./install.sh
```

If no key is set and the node is not already logged in, bootstrap prints:

```bash
sudo tailscale up --hostname=cursor --accept-dns --ssh=false
```

and continues so Compose can still start locally. CN2 cannot reach the new machine until that login finishes.

---

## 3. Layout

| Path | Role |
|------|------|
| `docker-compose.yml` | `app` only; SQLite via `./data` |
| `./data/sequoia_v2.db` | SQLite file (copy this to migrate machines) |
| `Dockerfile` | frontend build + Python API |

`DB_PATH` inside the app container:

```
data/sequoia_v2.db
```

Product settings (strategies, sync interval/retries, `start_date`, run history) live in the same SQLite file as market data. Port `8002` is the process bind in Compose/Dockerfile.

---

## 4. Day-to-day

```bash
docker compose logs -f app
docker compose restart app
docker compose down          # stop; keep ./data
```

Rebuild after code changes:

```bash
docker compose up -d --build
```

---

## 5. Move to another computer

Stop writers, then copy the SQLite file. You do **not** copy a `.env` file. Do **not** commit `TS_AUTHKEY` / `TAILSCALE_AUTHKEY`.

On the old machine:

```bash
docker compose stop   # if using Compose
./deploy/scripts/backup-db.sh
# or copy data/sequoia_v2.db (and -wal/-shm if present)
```

On the new machine:

```bash
mkdir -p data
cp backups/sequoia-YYYYMMDD-HHMMSS.db data/sequoia_v2.db
docker compose up -d --build   # or ./scripts/run-web.sh
curl -sS http://127.0.0.1:8002/api/health
```

### Replace the Linux backend (same CN2 doorway)

The public hostname is fixed: CN2 nginx proxies `http://185.218.4.107/` → `http://cursor.tail87959b.ts.net:8002`. A replacement machine must rejoin Tailscale as **`cursor`** so that config never changes.

1. In Tailscale admin, create a **reusable** auth key (ideally tagged). Do not put the key in git.
2. On the new host: clone the repo, copy `data/sequoia_v2.db` as above (or restore a backup).
3. Join and start:

```bash
export TS_AUTHKEY='tskey-auth-…'   # or TAILSCALE_AUTHKEY
./install.sh
```

4. If the old box is still online, delete or rename the previous **`cursor`** node in Tailscale admin so MagicDNS `cursor.tail87959b.ts.net` points at the new machine.
5. Leave CN2 nginx unchanged.

From the CN2 VPS (or any node on the tailnet):

```bash
tailscale ping cursor
curl -sS http://cursor.tail87959b.ts.net:8002/api/health
```

Interactive fallback (no auth key): run `sudo tailscale up --hostname=cursor --accept-dns --ssh=false` when bootstrap prints that command.

Optional Tailscale Serve is **not** used in this layout (CN2 is the public front). If you ever need it on a private tailnet only:

```bash
sudo tailscale serve --bg 8002
```

---

## 6. Backup

```bash
./deploy/scripts/backup-db.sh
# writes backups/sequoia-YYYYMMDD-HHMMSS.db  (sqlite3 .backup, WAL-safe)
```

Restore (stop the app first so nothing is writing):

```bash
docker compose stop app   # if using Compose
./deploy/scripts/restore-db.sh backups/sequoia-YYYYMMDD-HHMMSS.db
docker compose start app  # or ./scripts/run-web.sh
```

The file includes market data **and** product config (`sync_config`, `strategy_config`, run history).

---

## 7. Check

```bash
python -c "
from sequoia_x.db import connect, table_exists, resolve_db_path
print(resolve_db_path())
with connect() as conn:
    for t in ['stock_daily', 'sync_config', 'strategy_config']:
        print(t, table_exists(conn, t))
"
```
