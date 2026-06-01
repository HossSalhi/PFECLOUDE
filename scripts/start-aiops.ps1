# ============================================================================
#  HELIOS AIOps — Cluster startup
# ----------------------------------------------------------------------------
#  Boots the 5 VMs in the correct order, waits for the K8s control plane and
#  worker nodes to be Ready, then prints the access URLs.
#
#  GOLDEN RULES (read first, ignore at your peril):
#    - Docker Desktop MUST be off when this runs. WSL2 backend conflicts with
#      VMware Workstation virtualization and crashes running VMs.
#    - If a previous session crashed, this script cleans stale .lck files.
#    - If the VMs were suspended, .vmem session files are removed to force a
#      clean cold boot (safer than a stale resume).
# ============================================================================
[CmdletBinding()]
param(
    [int]$ReadyTimeoutMinutes = 5,
    [switch]$SkipDockerCheck
)
$ErrorActionPreference = 'Stop'

$vmrun = 'C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe'
$vmDir = "$env:USERPROFILE\Documents\Virtual Machines"
$vms = @('k8s-master','k8s-worker1','k8s-worker2','k8s-monitoring','k8s-aiCamunda')
$masterIp = '192.168.148.153'
$key = "$env:USERPROFILE\.ssh\id_ed25519"

function Write-Step($msg) { Write-Host "[$([DateTime]::Now.ToString('HH:mm:ss'))] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  $msg" -ForegroundColor Red }

# ── Guard: Docker Desktop must NOT be running ────────────────────────────
if (-not $SkipDockerCheck) {
    Write-Step "Checking Docker Desktop is OFF (it would crash VMs via WSL2 conflict)"
    $dockerProcs = Get-Process | Where-Object {$_.ProcessName -match '^(Docker Desktop|com\.docker|vpnkit)$'}
    if ($dockerProcs) {
        Write-Err "Docker Desktop is running. Quit it first."
        Write-Err "Found: $(($dockerProcs | Select-Object -ExpandProperty ProcessName -Unique) -join ', ')"
        Write-Err "Run:  & 'C:\Program Files\Docker\Docker\Docker Desktop.exe' --quit"
        Write-Err "Then: wsl --shutdown"
        Write-Err "Then retry this script. (Or pass -SkipDockerCheck to override.)"
        exit 1
    }
    Write-Ok "Docker Desktop is off — OK to proceed"
}

# ── Check vmrun exists ───────────────────────────────────────────────────
if (-not (Test-Path $vmrun)) {
    Write-Err "vmrun.exe not found at: $vmrun"
    exit 1
}

# ── Clean stale .lck and .vmem ──────────────────────────────────────────
Write-Step "Cleaning stale lock + session files"
$lckDirs = Get-ChildItem -Path $vmDir -Recurse -Filter "*.lck" -Directory -ErrorAction SilentlyContinue
foreach ($d in $lckDirs) { Remove-Item -Path $d.FullName -Recurse -Force -ErrorAction SilentlyContinue }
$vmems = Get-ChildItem -Path $vmDir -Recurse -Filter "*.vmem" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch 'Snapshot' }
foreach ($f in $vmems) { Remove-Item -Path $f.FullName -Force -ErrorAction SilentlyContinue }
Write-Ok "Removed $($lckDirs.Count) .lck dirs and $($vmems.Count) session .vmem files"

# ── Boot VMs (master first to stagger I/O, others in parallel) ───────────
Write-Step "Booting master..."
& $vmrun -T ws start "$vmDir\k8s-master\k8s-master.vmx" nogui
if ($LASTEXITCODE -ne 0) { Write-Err "Master start failed (exit $LASTEXITCODE). Is virtualization free?"; exit 1 }
Start-Sleep -Seconds 8

foreach ($vm in @('k8s-worker1','k8s-worker2','k8s-monitoring','k8s-aiCamunda')) {
    Write-Step "Booting $vm..."
    & $vmrun -T ws start "$vmDir\$vm\$vm.vmx" nogui
    if ($LASTEXITCODE -ne 0) { Write-Warn "$vm start exit=$LASTEXITCODE — continuing" }
    Start-Sleep -Seconds 2
}

# ── Wait for cluster ─────────────────────────────────────────────────────
Write-Step "Waiting for K8s cluster Ready (timeout=$ReadyTimeoutMinutes min)"
$deadline = (Get-Date).AddMinutes($ReadyTimeoutMinutes)
$ready = $false
while ((Get-Date) -lt $deadline) {
    $out = & ssh -i $key -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no `
        "kube@$masterIp" 'kubectl get nodes --no-headers' 2>&1
    if ($LASTEXITCODE -eq 0) {
        $readyCount = (($out -split "`n") | Where-Object { $_ -match '\bReady\b' -and $_ -notmatch 'NotReady' }).Count
        Write-Host "  nodes Ready: $readyCount/3" -ForegroundColor Gray
        if ($readyCount -ge 3) { $ready = $true; break }
    } else {
        Write-Host "  waiting for SSH..." -ForegroundColor DarkGray
    }
    Start-Sleep -Seconds 8
}

if (-not $ready) {
    Write-Err "Cluster did not reach 3 Ready nodes within $ReadyTimeoutMinutes minutes"
    Write-Err "Run: ssh kube@$masterIp 'kubectl get nodes && kubectl get pods -A'"
    exit 1
}

# ── Status report ─────────────────────────────────────────────────────────
Write-Step "Cluster ready — current state:"
ssh -i $key -o BatchMode=yes -o StrictHostKeyChecking=no "kube@$masterIp" 'kubectl get pods -n pfe -o wide 2>&1'

$os = Get-CimInstance Win32_OperatingSystem
$freeGB = [math]::Round($os.FreePhysicalMemory/1MB, 1)
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  HELIOS AIOps READY" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  Frontend  : http://192.168.148.153:30880" -ForegroundColor White
Write-Host "  Backend   : http://192.168.148.153:30880/actuator/health" -ForegroundColor White
Write-Host "  Grafana   : http://192.168.148.160:3000" -ForegroundColor White
Write-Host "  AI service: http://192.168.148.161:8000/monitor/status" -ForegroundColor White
Write-Host "  Camunda   : http://192.168.148.161:8080/v2/topology" -ForegroundColor White
Write-Host "  Host free RAM: $freeGB GB" -ForegroundColor Gray
Write-Host "================================================================" -ForegroundColor Green
