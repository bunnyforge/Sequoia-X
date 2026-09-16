# One-shot: install Docker Desktop (if missing) and start Sequoia-X.
# Tailscale hostname "cursor" / CN2 MagicDNS is Linux-only (see deploy/scripts/bootstrap.sh).
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message"
}

function Wait-Docker {
    for ($i = 0; $i -lt 90; $i++) {
        try {
            docker info 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return }
        } catch {}
        Start-Sleep -Seconds 2
    }
    throw "Docker is not ready. Start Docker Desktop, wait until it is running, then re-run: .\deploy\scripts\bootstrap.ps1 -SkipInstall"
}

function Install-DockerDesktop {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Write-Step "Docker CLI already present"
        return
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Docker is not installed and winget is missing. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ then re-run this script."
    }
    Write-Step "Installing Docker Desktop via winget (may need a reboot / first-launch GUI)"
    winget install --id Docker.DockerDesktop -e --accept-source-agreements --accept-package-agreements
    $dockerDesktop = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($dockerDesktop) {
        Start-Process $dockerDesktop | Out-Null
    }
}

if (-not (Test-Path "docker-compose.yml")) {
    throw "run this from the Sequoia-X repo (docker-compose.yml missing)"
}

if (-not $SkipInstall) {
    Install-DockerDesktop
}

Wait-Docker
docker compose version | Out-Null

# Compose bind is always /workspace/sequoia-x/postgres (Docker Desktop Linux VM).
# Also create C:\workspace\... so a Windows-side directory exists if Docker maps it.
$pgUnix = "/workspace/sequoia-x/postgres"
$pgWin = "C:\workspace\sequoia-x\postgres"
New-Item -ItemType Directory -Force -Path $pgWin | Out-Null
$env:SEQUOIA_PGDATA = $pgUnix
Write-Step "Postgres data directory: $pgUnix (host also $pgWin)"

Write-Step "Building and starting containers (first run can take several minutes)"
docker compose up -d --build
docker compose ps

$url = "http://127.0.0.1:8002/api/health"
Write-Step "Waiting for $url"
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) {
            $ok = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 2
}
if (-not $ok) {
    docker compose logs --tail 80 app
    throw "app did not become healthy on $url"
}

Write-Step "Healthy: $url"
Write-Host "Open http://127.0.0.1:8002/"
Write-Host "Tailscale (Linux hostname 'cursor' / cursor.tail87959b.ts.net:8002 for CN2) is out of scope for this Windows script; see deploy/RUNBOOK.md."
