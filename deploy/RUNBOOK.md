# Sequoia-X — Docker Compose deploy

Production path is **Docker Compose** (Postgres 16 + app).

One-shot (installs Docker + Compose, and on Linux Tailscale, if needed, then builds and starts). **No script edits.** The only optional environment variable is `TS_AUTHKEY` (or `TAILSCALE_AUTHKEY`).

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

Data lives in **`/workspace/sequoia-x/postgres`** (bind-mounted to `/var/lib/postgresql/data`). Install/bootstrap scripts create that directory if missing. It is outside the git repo.

---

## 1. Requirements

- Docker Engine + Compose v2 (bootstrap installs them, and will start `dockerd` without systemd and fetch the Compose plugin if the package left them missing)
- About 2Gi RAM for both containers
- Port `8002` free
- Linux public access: Tailscale node named **`cursor`** (MagicDNS `cursor.tail87959b.ts.net`). CN2 nginx already proxies `http://185.218.4.107/` → `http://cursor.tail87959b.ts.net:8002`. Do **not** name the Tailscale machine `sequoia`.

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

Same Postgres **major** version (`postgres:16-bookworm`), same OS family (Linux↔Linux or WSL↔WSL). Stop Compose before copying. You do **not** copy a `.env` file. Do **not** commit `TS_AUTHKEY` / `TAILSCALE_AUTHKEY`.

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

### Replace the Linux backend (same CN2 doorway)

The public hostname is fixed: CN2 nginx proxies `http://185.218.4.107/` → `http://cursor.tail87959b.ts.net:8002`. A replacement machine must rejoin Tailscale as **`cursor`** so that config never changes.

1. In Tailscale admin, create a **reusable** auth key (ideally tagged). Do not put the key in git.
2. On the new host: clone the repo, copy Postgres data as above (or restore a dump).
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
