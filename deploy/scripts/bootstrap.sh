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
                  Compose, and still recovers a missing dockerd / Compose plugin.
                  If Tailscale is already installed, still sets hostname ${TS_HOSTNAME}.

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

docker_info_ok() {
  docker_ok || as_root docker info >/dev/null 2>&1
}

compose_ok() {
  docker compose version >/dev/null 2>&1 || as_root docker compose version >/dev/null 2>&1
}

dc() {
  if docker_ok; then
    docker compose "$@"
  else
    as_root docker compose "$@"
  fi
}

# GitHub Compose release assets: docker-compose-linux-x86_64 / docker-compose-linux-aarch64
# (also armv6, armv7, ppc64le, riscv64, s390x). uname -m amd64/arm64 aliases included.
compose_linux_arch() {
  local m
  m="$(uname -m)"
  case "$m" in
    x86_64|amd64)  printf 'x86_64' ;;
    aarch64|arm64) printf 'aarch64' ;;
    armv7l|armv7)  printf 'armv7' ;;
    armv6l|armv6)  printf 'armv6' ;;
    ppc64le)       printf 'ppc64le' ;;
    riscv64)       printf 'riscv64' ;;
    s390x)         printf 's390x' ;;
    *) die "unsupported CPU architecture for Compose plugin: ${m}" ;;
  esac
}

find_dockerd() {
  if command -v dockerd >/dev/null 2>&1; then
    command -v dockerd
    return 0
  fi
  local p
  for p in /usr/bin/dockerd /usr/sbin/dockerd /usr/local/bin/dockerd; do
    if [[ -x "$p" ]]; then
      printf '%s' "$p"
      return 0
    fi
  done
  p="$(as_root sh -c 'command -v dockerd' 2>/dev/null || true)"
  if [[ -n "$p" ]]; then
    printf '%s' "$p"
    return 0
  fi
  return 1
}

dockerd_pid_exists() {
  if command -v pgrep >/dev/null 2>&1; then
    if pgrep -x dockerd >/dev/null 2>&1; then
      return 0
    fi
    if as_root pgrep -x dockerd >/dev/null 2>&1; then
      return 0
    fi
    return 1
  fi
  [[ -f /var/run/docker.pid ]]
}

# When systemd is absent (PID1=tini, systemctl no-ops), start dockerd ourselves.
ensure_dockerd() {
  if docker_info_ok; then
    return 0
  fi
  log "Docker daemon not responding; trying to start it"
  if command -v systemctl >/dev/null 2>&1; then
    as_root systemctl enable --now docker >/dev/null 2>&1 || as_root systemctl start docker >/dev/null 2>&1 || true
  fi
  if docker_info_ok; then
    return 0
  fi
  if command -v service >/dev/null 2>&1; then
    as_root service docker start >/dev/null 2>&1 || true
  fi
  if docker_info_ok; then
    return 0
  fi

  local bin logf
  bin="$(find_dockerd || true)"
  if [[ -z "$bin" ]]; then
    log "dockerd binary not found (systemctl/service did not start Docker)"
    return 1
  fi
  if dockerd_pid_exists && [[ -S /var/run/docker.sock ]]; then
    log "dockerd already present; waiting for it to become ready"
    return 0
  fi
  logf="${SEQUOIA_DOCKERD_LOG:-/tmp/dockerd.log}"
  log "No working systemd Docker unit; starting ${bin} in background (log: ${logf})"
  as_root sh -c "nohup '$bin' --host=unix:///var/run/docker.sock >>'$logf' 2>&1 </dev/null & exit 0" || true
}

# Official Compose v2 CLI plugin when the package/plugin is missing (common with Debian docker.io).
ensure_compose_plugin() {
  if compose_ok; then
    return 0
  fi
  command -v docker >/dev/null 2>&1 || die "docker CLI is missing; install Docker or re-run without --skip-install"
  need_root_for_install
  ensure_curl

  local arch ver url tmp dest installed magic
  arch="$(compose_linux_arch)"
  ver="${COMPOSE_VER:-v2.29.7}"
  url="https://github.com/docker/compose/releases/download/${ver}/docker-compose-linux-${arch}"
  log "docker compose plugin missing; downloading ${ver} (linux-${arch})"

  tmp="$(mktemp)"
  if ! curl -fsSL -o "$tmp" "$url"; then
    rm -f "$tmp"
    die "failed to download Compose plugin from ${url}"
  fi
  magic="$(od -An -N4 -tx1 "$tmp" | tr -d ' \n')"
  if [[ "$magic" != "7f454c46" ]]; then
    rm -f "$tmp"
    die "Compose download was not an ELF binary (got magic ${magic}); check ${url}"
  fi

  installed=0
  for dest in /usr/libexec/docker/cli-plugins /usr/local/lib/docker/cli-plugins; do
    if as_root mkdir -p "$dest" \
      && as_root cp "$tmp" "${dest}/docker-compose" \
      && as_root chmod 0755 "${dest}/docker-compose"; then
      installed=1
      log "Installed Compose plugin to ${dest}/docker-compose"
    fi
  done
  rm -f "$tmp"
  [[ "$installed" -eq 1 ]] || die "could not write Compose plugin to /usr/libexec/docker/cli-plugins or /usr/local/lib/docker/cli-plugins"
  compose_ok || die "docker compose plugin still missing after install"
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
  local i logf
  logf="${SEQUOIA_DOCKERD_LOG:-/tmp/dockerd.log}"
  if docker_info_ok; then
    return 0
  fi
  ensure_dockerd || true
  for i in $(seq 1 60); do
    if docker_info_ok; then
      return 0
    fi
    sleep 2
  done
  die "Docker daemon did not become ready (no-systemd hosts: see ${logf} if dockerd was started in the background). Start Docker and re-run: $0 --skip-install"
}

install_docker_linux() {
  if command -v docker >/dev/null 2>&1; then
    log "Docker CLI already installed"
    return 0
  fi
  need_root_for_install
  ensure_curl
  log "Installing Docker Engine + Compose plugin (get.docker.com)"
  curl -fsSL https://get.docker.com | as_root sh
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
if [[ "$(uname -s)" == Linux ]]; then
  ensure_compose_plugin
else
  compose_ok || die "docker compose plugin missing"
fi

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
