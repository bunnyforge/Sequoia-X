# Sequoia-X on k3s + CloudNativePG — replay runbook

Copy-pasteable setup for a **new machine**. This host does **not** use systemd for k3s; start it by hand (or via `deploy/scripts/start-k3s.sh`).

Paths assume the app tree lives at `/workspace/apps/Sequoia-X` (local deploy copy; do not re-clone).

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
APP=/workspace/apps/Sequoia-X
```

---

## 1. k3s install / start (manual, not systemd)

### Why these flags

- `--snapshotter native` — overlayfs snapshotter fails on this box (`failed to mount overlay: invalid argument`).
- `--flannel-backend host-gw` — single-node / no VXLAN overlay.
- `--disable traefik` / `--disable metrics-server` — save RAM (~3–4Gi available).
- `--tls-san …` — API reachable via Tailscale IP and MagicDNS name.
- `--write-kubeconfig-mode 644` — kubeconfig readable without being root (still use `sudo kubectl` if file is root-owned).

There is **no** `/etc/rancher/k3s/config.yaml` and **no** systemd unit. Process is started by hand; logs: `/workspace/logs/k3s.log`.

### Binary

```bash
# k3s already at /usr/local/bin/k3s on this host (v1.36.4+k3s1).
# Fresh machine:
curl -sfL https://get.k3s.io | INSTALL_K3S_SKIP_ENABLE=true INSTALL_K3S_SKIP_START=true sh -
```

### Start (exact argv used here)

```bash
sudo mkdir -p /workspace/logs
sudo /usr/local/bin/k3s server \
  --write-kubeconfig-mode 644 \
  --tls-san 100.76.76.54 \
  --tls-san cursor.tail87959b.ts.net \
  --disable metrics-server \
  --disable traefik \
  --snapshotter native \
  --flannel-backend host-gw \
  >>/workspace/logs/k3s.log 2>&1 &
```

Helper (same flags + CNI forward fix):

```bash
"$APP/deploy/scripts/start-k3s.sh"
```

### After reboot

```bash
"$APP/deploy/scripts/start-k3s.sh"
"$APP/deploy/scripts/fix-cni-forward.sh"
sudo kubectl get nodes
# wait for CNPG + app
sudo kubectl get pods,svc,cluster -n sequoia-x
sudo tailscale serve --bg 8002
```

### kubeconfig

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
# or: sudo kubectl …
```

---

## 2. CloudNativePG operator

Current stable used: **1.30.0** (supports Kubernetes 1.36).

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
sudo kubectl apply --server-side -f \
  https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.30/releases/cnpg-1.30.0.yaml
sudo kubectl -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=180s
```

---

## 3. YAML manifests (`deploy/k3s/`)

Apply in this order:

```bash
sudo kubectl apply -f "$APP/deploy/k3s/namespace.yaml"
sudo kubectl apply -f "$APP/deploy/k3s/cnpg-cluster.yaml"
# wait until Cluster reports healthy / readyInstances=1
sudo kubectl get cluster -n sequoia-x
"$APP/deploy/scripts/make-db-secret.sh"
sudo kubectl apply -f "$APP/deploy/k3s/app-deployment.yaml"
```

| File | What |
|------|------|
| `namespace.yaml` | Namespace `sequoia-x` |
| `cnpg-cluster.yaml` | CNPG `Cluster` `sequoia-pg` (1 instance, 5Gi local-path, db `sequoia` / user `sequoia`). Backup **omitted**. |
| `app-db-secret.yaml` | Placeholder `sequoia-x-db` (`DATABASE_URL`). Do not apply the placeholder password; use `make-db-secret.sh`. |
| `app-deployment.yaml` | App Deployment + NodePort Service (`8002:30002`) |
| `backup-placeholder.yaml` | Commented CN2 S3 / barmanObjectStore example — **do not apply** until credentials exist |

Duplicate of the cluster spec also at `deploy/cnpg-cluster.yaml`.

### Cluster spec (memory-tight)

- `instances: 1`
- PVC `5Gi`, StorageClass `local-path`
- Postgres requests `256Mi` / limits `768Mi`
- `shared_buffers=128MB`, `max_connections=50`
- No `backup.barmanObjectStore` (backups off)

### App connection secret (CNPG-generated)

CNPG creates `sequoia-pg-app` in `sequoia-x` (type `kubernetes.io/basic-auth`), keys include:

| Key | Use |
|-----|-----|
| `user` / `username` | `sequoia` |
| `password` | app role password |
| `dbname` | `sequoia` |
| `host` | `sequoia-pg-rw` (short; **unreliable** with `ndots:5`) |
| `uri` | `postgresql://…@sequoia-pg-rw.sequoia-x:5432/sequoia` |
| `fqdn-uri` | `postgresql://…@sequoia-pg-rw.sequoia-x.svc.cluster.local:5432/sequoia` |
| `port` | `5432` |

**App wiring:** secret `sequoia-x-db` key `DATABASE_URL`:

```
postgresql://sequoia:<urlencoded-password>@sequoia-pg-rw.sequoia-x.svc.cluster.local:5432/sequoia?sslmode=require
```

Built by:

```bash
"$APP/deploy/scripts/make-db-secret.sh"
```

The Deployment envFrom/env is:

```yaml
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: sequoia-x-db
      key: DATABASE_URL
```

Do **not** use `sequoia-pg-app` `uri` as-is: short hostname + `ndots:5` can fail DNS; CNPG also expects TLS (`sslmode=require`).

---

## 4. Build / import the app image (no separate k3s docker daemon)

k3s uses containerd (`/run/k3s/containerd/containerd.sock`). This box had no docker until install; **docker.io + vfs** is used only to build, then the tarball is imported.

**Important:** installing `docker.io` sets **iptables-legacy FORWARD policy DROP**, which breaks pod-to-pod (CoreDNS, CNPG status, app→Postgres). Always stop dockerd after import and run `fix-cni-forward.sh`.

```bash
"$APP/deploy/scripts/build-import-image.sh"
```

Manual equivalent:

```bash
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
# no systemd start; launch vfs dockerd
sudo dockerd --storage-driver=vfs --host=unix:///var/run/docker.sock >/tmp/dockerd.log 2>&1 &
cd /workspace/apps/Sequoia-X
sudo docker build -t sequoia-x:local .
sudo docker save sequoia-x:local -o /tmp/sequoia-x-local.tar
sudo k3s ctr images import /tmp/sequoia-x-local.tar
sudo kill "$(pgrep -x dockerd | head -1)" || true
"$APP/deploy/scripts/fix-cni-forward.sh"
sudo k3s ctr images ls | grep sequoia-x
```

Image name in the Deployment: `docker.io/library/sequoia-x:local` with `imagePullPolicy: IfNotPresent`.

Dockerfile: Python 3.13 slim + uv, serves `api.main:app` on **8002**, copies `frontend/dist`.

---

## 5. DATABASE_URL / Postgres migration notes

App prefers `DATABASE_URL` (`postgresql://…`). SQLite file path (`DB_PATH`) remains a local/dev fallback via `sequoia_x.db`.

Dialect changes:

- `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGINT GENERATED BY DEFAULT AS IDENTITY` on Postgres
- `INSERT OR IGNORE` → `INSERT … ON CONFLICT DO NOTHING`
- SQLite `MAX(a,b)` / `MIN(a,b)` in upserts → `GREATEST` / `LEAST`
- `sqlite_master` / `PRAGMA table_info` → `information_schema`
- Placeholders `?` → `%s` for psycopg
- Dependency: `psycopg[binary]>=3.2` in `pyproject.toml` / `uv.lock`

Empty Postgres is OK: API lifespan initializes `stock_daily`, sync control tables, and strategy tables.

---

## 6. Tailscale Serve

Previously: `https://cursor.tail87959b.ts.net` → `http://127.0.0.1:8002` (host uvicorn). Now the app is the k8s Service (**NodePort 30002**).

**Do not use hostPort:** this kernel lacks `xt_multiport`, so CNI portmap fails (`Extension multiport revision 0 not supported`).

### A. kubectl port-forward → localhost:8002 (keeps Serve on :8002)

```bash
"$APP/deploy/scripts/host-port-forward.sh"
sudo tailscale serve --bg 8002
sudo tailscale serve status
```

### B. Serve NodePort directly (no localhost proxy)

```bash
NODE_IP=$(sudo kubectl get node -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
sudo tailscale serve --bg "http://${NODE_IP}:30002"
```

Public URL (tailnet only):

```
https://cursor.tail87959b.ts.net/api/health
https://cursor.tail87959b.ts.net/
```

Also: `scripts/enable-tailscale-serve.sh`.

---

## 7. CN2 backup placeholder (do not block)

Backups are **off**. When the CN2 S3 endpoint exists:

1. Create credentials (do not commit secrets):

```bash
sudo kubectl -n sequoia-x create secret generic sequoia-pg-backup-creds \
  --from-literal=ACCESS_KEY_ID='REPLACE_ME' \
  --from-literal=ACCESS_SECRET_KEY='REPLACE_ME'
```

2. Uncomment / merge the `spec.backup.barmanObjectStore` block in `deploy/k3s/cnpg-cluster.yaml` (also copied in `deploy/k3s/backup-placeholder.yaml`):

```yaml
backup:
  barmanObjectStore:
    destinationPath: s3://YOUR_CN2_BUCKET/sequoia-pg
    endpointURL: https://YOUR_CN2_S3_ENDPOINT
    s3Credentials:
      accessKeyId:
        name: sequoia-pg-backup-creds
        key: ACCESS_KEY_ID
      secretAccessKey:
        name: sequoia-pg-backup-creds
        key: ACCESS_SECRET_KEY
    wal:
      compression: gzip
    data:
      compression: gzip
  retentionPolicy: "7d"
```

3. `sudo kubectl apply -f deploy/k3s/cnpg-cluster.yaml`

Until then, **do not** set `enableBackup` / object store; cluster must stay healthy without it.

---

## 8. Verification

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
sudo kubectl get pods,svc,cluster -n sequoia-x
sudo kubectl get cluster sequoia-pg -n sequoia-x
# expect: STATUS "Cluster in healthy state", READY 1

# in-cluster health
sudo kubectl exec -n sequoia-x deploy/sequoia-x -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8002/api/health').read())"

# via port-forward / host-port-forward.sh
curl -sS http://127.0.0.1:8002/api/health
curl -sS http://127.0.0.1:8002/api/dashboard/summary

# NodePort (node IP, not always 127.0.0.1)
curl -sS http://172.30.0.2:30002/api/health

# Tailscale
curl -sS https://cursor.tail87959b.ts.net/api/health

# schema
sudo kubectl exec -n sequoia-x deploy/sequoia-x -- python -c "
from sequoia_x.db import connect, table_exists
import os
with connect(os.environ['DATABASE_URL']) as conn:
    print('postgres', conn.postgres)
    for t in ['stock_daily','sync_config','strategy_config']:
        print(t, table_exists(conn, t))
"
```

---

## 9. Known host quirks (replay these)

1. **No systemd for k3s** — start with `start-k3s.sh`.
2. **docker.io vs k3s CNI** — after any docker install/start, run `fix-cni-forward.sh` and stop dockerd. Symptom: app log `failed to resolve host 'sequoia-pg-rw.sequoia-x'` / operator `Get https://10.42.x.x:8000/pg/status: i/o timeout`.
3. **Do not use hostPort** (missing `xt_multiport`); use NodePort 30002 + `host-port-forward.sh` or Tailscale→NodePort.
4. Do **not** `pkill uvicorn` on the host — the k8s container process is visible and you will kill the Deployment.
5. Traefik leftover from an earlier k3s start may remain; svclb can sit in `ContainerCreating`. App does not depend on Traefik.
6. After reboot: `start-k3s.sh` → `fix-cni-forward.sh` → wait for pods → `host-port-forward.sh` → `tailscale serve --bg 8002`.

---

## 10. File map

```
deploy/
  RUNBOOK.md                 # this file
  cnpg-cluster.yaml          # same Cluster spec
  k3s/
    namespace.yaml
    cnpg-cluster.yaml
    app-deployment.yaml
    app-db-secret.yaml       # placeholder only
    backup-placeholder.yaml
  scripts/
    start-k3s.sh
    build-import-image.sh
    make-db-secret.sh
    fix-cni-forward.sh
    host-port-forward.sh
Dockerfile
```
