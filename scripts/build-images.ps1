# ============================================================================
#  HELIOS AIOps — Image build pipeline
# ----------------------------------------------------------------------------
#  Builds pfe-backend and pfe-frontend Docker images and saves them as .tar
#  ready for deploy-images.ps1.
#
#  This script auto-manages the VMware/Docker conflict:
#    1. Stops all running VMs (if any) — they would crash when Docker starts.
#    2. Starts Docker Desktop, waits for daemon.
#    3. Builds backend and frontend (frontend now multi-stage, builds Angular
#       inside Docker — no ng-build-on-host step needed).
#    4. Saves both images to $env:TEMP.
#    5. Quits Docker Desktop and waits for full release of virtualization.
#
#  After running this: use scripts/start-aiops.ps1 to bring VMs back up, then
#  scripts/deploy-images.ps1 to roll out the new images.
# ============================================================================
[CmdletBinding()]
param(
    [string]$BackendTag  = "pfe-backend:0.2.12",
    [string]$FrontendTag = "pfe-frontend:1.0.6",
    [string]$BackendDir  = "$PSScriptRoot\..\backend\backend",
    [string]$FrontendDir = "$PSScriptRoot\..\frontend"
)
$ErrorActionPreference = 'Stop'

$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$dd     = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$vmrun  = 'C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe'

function Write-Step($msg) { Write-Host "[$([DateTime]::Now.ToString('HH:mm:ss'))] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  $msg" -ForegroundColor Red }

if (-not (Test-Path $docker)) { Write-Err "docker.exe not found: $docker"; exit 1 }
if (-not (Test-Path $dd))     { Write-Err "Docker Desktop.exe not found: $dd"; exit 1 }

# ── Stop any running VMs first (VMware/WSL2 conflict) ────────────────────
Write-Step "Checking for running VMs..."
$runningVms = & $vmrun list | Select-String '\.vmx$'
if ($runningVms) {
    Write-Warn "Found $($runningVms.Count) running VMs — stopping them first (Docker would crash them)"
    & "$PSScriptRoot\stop-aiops.ps1"
} else {
    Write-Ok "No VMs running"
}

# ── Start Docker Desktop ─────────────────────────────────────────────────
Write-Step "Starting Docker Desktop..."
Start-Process -FilePath $dd -WindowStyle Hidden
$deadline = (Get-Date).AddMinutes(2)
$ready = $false
while ((Get-Date) -lt $deadline) {
    $v = & $docker info --format '{{.ServerVersion}}' 2>&1
    if ($LASTEXITCODE -eq 0 -and "$v" -notmatch 'error|cannot|denied') {
        Write-Ok "Daemon ready: $v"; $ready = $true; break
    }
    Start-Sleep -Seconds 4
}
if (-not $ready) { Write-Err "Docker daemon not ready after 2 min"; exit 1 }

# ── Build backend ────────────────────────────────────────────────────────
Write-Step "Building $BackendTag from $BackendDir"
$backendPath = Resolve-Path $BackendDir
Push-Location $backendPath
try {
    & $docker build -t $BackendTag . 2>&1 | Tee-Object -Variable buildOut | Select-Object -Last 5
    if ($LASTEXITCODE -ne 0) { throw "backend docker build failed" }
    Write-Ok "Backend build OK"
} finally { Pop-Location }

# ── Build frontend (multi-stage, builds Angular inside Docker) ───────────
Write-Step "Building $FrontendTag from $FrontendDir (multi-stage, may take 3-5 min)"
$frontendPath = Resolve-Path $FrontendDir
Push-Location $frontendPath
try {
    & $docker build -t $FrontendTag . 2>&1 | Select-Object -Last 5
    if ($LASTEXITCODE -ne 0) { throw "frontend docker build failed" }
    Write-Ok "Frontend build OK"
} finally { Pop-Location }

# ── Sanity check: HELIOS in the frontend image ───────────────────────────
Write-Step "Verifying HELIOS is present in $FrontendTag"
$cid = & $docker create $FrontendTag
& $docker cp "${cid}:/usr/share/nginx/html/index.html" "$env:TEMP\__verify-index.html" 2>&1 | Out-Null
& $docker rm $cid | Out-Null
$content = Get-Content "$env:TEMP\__verify-index.html" -Raw
if ($content -match 'HELIOS') {
    Write-Ok "HELIOS branding present in the image"
} else {
    Write-Warn "HELIOS NOT in image — may indicate a stale source. Check frontend/src/index.html"
}
Remove-Item "$env:TEMP\__verify-index.html" -ErrorAction SilentlyContinue

# ── Save tars ────────────────────────────────────────────────────────────
$beTar = "$env:TEMP\$($BackendTag -replace ':','-').tar"
$feTar = "$env:TEMP\$($FrontendTag -replace ':','-').tar"
Write-Step "Saving backend tar to $beTar"
& $docker save -o $beTar $BackendTag
Write-Ok "$([math]::Round((Get-Item $beTar).Length/1MB, 1)) MB"
Write-Step "Saving frontend tar to $feTar"
& $docker save -o $feTar $FrontendTag
Write-Ok "$([math]::Round((Get-Item $feTar).Length/1MB, 1)) MB"

# ── Quit Docker Desktop (release virtualization for VMware) ─────────────
Write-Step "Stopping Docker Desktop..."
& $dd --quit 2>&1 | Out-Null
Start-Sleep -Seconds 8
Get-Process | Where-Object {$_.ProcessName -match '^(Docker Desktop|com\.docker|vpnkit|wslservice|vmcompute)$'} |
    ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3
wsl --shutdown 2>&1 | Out-Null
Start-Sleep -Seconds 3
Write-Ok "Docker stopped — VMware can now use virtualization extensions"

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  BUILD COMPLETE" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  Backend tar : $beTar" -ForegroundColor White
Write-Host "  Frontend tar: $feTar" -ForegroundColor White
Write-Host ""
Write-Host "  Next:" -ForegroundColor Gray
Write-Host "    .\scripts\start-aiops.ps1        # boot the cluster" -ForegroundColor White
Write-Host "    .\scripts\deploy-images.ps1 -BackendTag $BackendTag -FrontendTag $FrontendTag" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Green
