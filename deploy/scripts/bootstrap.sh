#!/usr/bin/env bash
# One-shot: install Docker Engine + Compose (if missing) and start Sequoia-X.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

SKIP_INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --skip-install) SKIP_INSTALL=1 ;;
    -h|--help)
      echo "Usage: $0 [--skip-install]"
      echo "  Install Docker/Compose if needed, then docker compose up --build."
      exit 0
      ;;
  esac
done

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need_root_for_install() {
  [[ "$(id -u)" -eq 0 ]] && return 0
  command -v sudo >/dev/null 2>&1 || die "need root or sudo to install Docker"
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
  if ! command -v curl >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      as_root apt-get update -qq
      as_root DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates
    elif command -v dnf >/dev/null 2>&1; then
      as_root dnf install -y curl ca-certificates
    else
      die "install curl, then re-run this script"
    fi
  fi
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

wait_health() {
  local i url
  url="http://127.0.0.1:8002/api/health"
  log "Waiting for $url"
  for i in $(seq 1 90); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "Healthy: $url"
      echo "Open http://127.0.0.1:8002/"
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

log "Building and starting containers (first run downloads images; can take several minutes)"
dc up -d --build
dc ps
wait_health
