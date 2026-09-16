#!/usr/bin/env bash
# One-shot: install Docker Engine + Compose (and Tailscale on Linux) if missing, then start Sequoia-X.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Fixed Tailscale identity so CN2 nginx never needs a hostname change on machine swap.
# Public doorway: http://185.218.4.107/ → http://cursor.tail87959b.ts.net:8002
TS_HOSTNAME="cursor"
TS_MAGICDNS="cursor.tail87959b.ts.net"
TS_BACKEND_PORT="8002"

SKIP_INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --skip-install) SKIP_INSTALL=1 ;;
    -h|--help)
      cat <<EOF
Usage: $0 [--skip-install]
  Linux: install Docker/Compose and Tailscale if missing, then docker compose up --build.
  --skip-install  Skip all host installs (Docker and Tailscale packages). Still starts
                  Compose. If Tailscale is already installed, still sets hostname ${TS_HOSTNAME}.

  Tailscale machine name is always '${TS_HOSTNAME}' (MagicDNS ${TS_MAGICDNS}).
  Unattended join: export TS_AUTHKEY or TAILSCALE_AUTHKEY (reusable auth key from Tailscale admin).
  If unset and not already logged in, the script prints a 'tailscale up' command and continues
  so Compose can still start locally; CN2 cannot reach this host until you authenticate.

  Public front is CN2 nginx → ${TS_MAGICDNS}:${TS_BACKEND_PORT}. Tailscale Serve is not enabled.
EOF
      exit 0
      ;;
  esac
done

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need_root_for_install() {
  [[ "$(id -u)" -eq 0 ]] && return 0
  command -v sudo >/dev/null 2>&1 || die "need root or sudo to install Docker/Tailscale"
}

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

docker_ok() {
  docker info >/dev/null 2>&1
}

dc() {
  if docker_ok; then
    docker compose "$@"
  else
    as_root docker compose "$@"
  fi
}

ensure_curl() {
  command -v curl >/dev/null 2>&1 && return 0
  need_root_for_install
  if command -v apt-get >/dev/null 2>&1; then
    as_root apt-get update -qq
    as_root DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates
  elif command -v dnf >/dev/null 2>&1; then
    as_root dnf install -y curl ca-certificates
  else
    die "install curl, then re-run this script"
  fi
}

wait_docker() {
  local i
  for i in $(seq 1 60); do
    if docker_ok || as_root docker info >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  die "Docker daemon did not become ready. Start Docker and re-run: $0 --skip-install"
}

install_docker_linux() {
  if command -v docker >/dev/null 2>&1 && as_root docker compose version >/dev/null 2>&1; then
    log "Docker Compose already installed"
    return 0
  fi
  need_root_for_install
  ensure_curl
  log "Installing Docker Engine + Compose plugin (get.docker.com)"
  curl -fsSL https://get.docker.com | as_root sh
  if command -v systemctl >/dev/null 2>&1; then
    as_root systemctl enable --now docker >/dev/null 2>&1 || as_root systemctl start docker >/dev/null 2>&1 || true
  elif command -v service >/dev/null 2>&1; then
    as_root service docker start >/dev/null 2>&1 || true
  fi
  if [[ "$(id -u)" -ne 0 ]] && command -v usermod >/dev/null 2>&1; then
    as_root usermod -aG docker "$USER" || true
    log "Added $USER to docker group (log out/in later so docker works without sudo)"
  fi
}

tailscale_authkey() {
  if [[ -n "${TS_AUTHKEY:-}" ]]; then
    printf '%s' "$TS_AUTHKEY"
  elif [[ -n "${TAILSCALE_AUTHKEY:-}" ]]; then
    printf '%s' "$TAILSCALE_AUTHKEY"
  fi
}

install_tailscale_linux() {
  if command -v tailscale >/dev/null 2>&1; then
    log "Tailscale already installed"
    return 0
  fi
  need_root_for_install
  ensure_curl
  log "Installing Tailscale (tailscale.com/install.sh)"
  curl -fsSL https://tailscale.com/install.sh | as_root sh
}

ensure_tailscaled() {
  if command -v systemctl >/dev/null 2>&1; then
    as_root systemctl enable --now tailscaled >/dev/null 2>&1 || as_root systemctl start tailscaled >/dev/null 2>&1 || true
  elif command -v service >/dev/null 2>&1; then
    as_root service tailscaled start >/dev/null 2>&1 || true
  else
    log "No systemd/service helper; start tailscaled manually if Tailscale is not running"
  fi
}

wait_tailscaled() {
  local i
  for i in $(seq 1 30); do
    if as_root tailscale status --json >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

tailscale_logged_in() {
  as_root tailscale status --self >/dev/null 2>&1
}

setup_tailscale_linux() {
  if [[ "$SKIP_INSTALL" -eq 0 ]]; then
    install_tailscale_linux
  elif ! command -v tailscale >/dev/null 2>&1; then
    log "Skipping Tailscale install (--skip-install). CN2/MagicDNS needs hostname ${TS_HOSTNAME}."
    return 0
  fi

  if ! command -v tailscale >/dev/null 2>&1; then
    log "Tailscale not installed; skipping join. Re-run without --skip-install, or install Tailscale and authenticate as ${TS_HOSTNAME}."
    return 0
  fi

  ensure_tailscaled
  if ! wait_tailscaled; then
    log "tailscaled is not ready. Start it, then: sudo tailscale up --hostname=${TS_HOSTNAME} --accept-dns --ssh=false"
    log "Continuing with Docker/app start; ${TS_MAGICDNS} will not resolve until Tailscale is up."
    return 0
  fi

  local key
  key="$(tailscale_authkey)"
  if [[ -n "$key" ]]; then
    log "Joining tailnet as hostname ${TS_HOSTNAME} (auth key)"
    as_root tailscale up --auth-key="$key" --hostname="$TS_HOSTNAME" --accept-dns --ssh=false
  elif tailscale_logged_in; then
    log "Tailscale already authenticated; ensuring hostname ${TS_HOSTNAME}"
    as_root tailscale set --hostname="$TS_HOSTNAME" || as_root tailscale up --hostname="$TS_HOSTNAME" --accept-dns --ssh=false
  else
    log "Tailscale is not logged in. CN2 nginx expects ${TS_MAGICDNS}:${TS_BACKEND_PORT}."
    echo "  Authenticate (prints a login URL):"
    echo "    sudo tailscale up --hostname=${TS_HOSTNAME} --accept-dns --ssh=false"
    echo "  Or re-run with TS_AUTHKEY / TAILSCALE_AUTHKEY set (reusable key from Tailscale admin)."
    echo "  Continuing so Compose can still start locally."
  fi
}

wait_health() {
  local i url
  url="http://127.0.0.1:8002/api/health"
  log "Waiting for $url"
  for i in $(seq 1 90); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "Healthy: $url"
      echo "Open http://127.0.0.1:8002/"
      echo "CN2 should proxy to ${TS_MAGICDNS}:${TS_BACKEND_PORT} (Tailscale hostname ${TS_HOSTNAME}; Serve is not enabled)."
      return 0
    fi
    sleep 2
  done
  log "Health check timed out. Last app logs:"
  dc logs --tail 80 app || true
  die "app did not become healthy on $url"
}

[[ -f docker-compose.yml ]] || die "run this from the Sequoia-X repo (docker-compose.yml missing)"

case "$(uname -s)" in
  Linux)
    if [[ "$SKIP_INSTALL" -eq 0 ]]; then
      install_docker_linux
    fi
    setup_tailscale_linux
    ;;
  Darwin)
    command -v docker >/dev/null 2>&1 || die "On macOS install Docker Desktop, start it, then re-run: $0 --skip-install"
    ;;
  *)
    die "Unsupported OS $(uname -s). On Windows use: powershell -File deploy/scripts/bootstrap.ps1"
    ;;
esac

wait_docker
as_root docker compose version >/dev/null 2>&1 || docker compose version >/dev/null 2>&1 || die "docker compose plugin missing"

SEQUOIA_PGDATA="${SEQUOIA_PGDATA:-/workspace/sequoia-x/postgres}"
export SEQUOIA_PGDATA
if mkdir -p "$SEQUOIA_PGDATA" 2>/dev/null; then
  :
else
  as_root mkdir -p "$SEQUOIA_PGDATA" || die "could not create $SEQUOIA_PGDATA"
  as_root chmod a+rwx /workspace /workspace/sequoia-x "$SEQUOIA_PGDATA" 2>/dev/null || true
fi
log "Postgres data directory: $SEQUOIA_PGDATA"

log "Building and starting containers (first run downloads images; can take several minutes)"
dc up -d --build
dc ps
wait_health
