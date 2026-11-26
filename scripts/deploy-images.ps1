# ============================================================================
#  HELIOS AIOps — Image deploy (post-build)
# ----------------------------------------------------------------------------
#  Distributes pre-built image tars (from build-images.ps1) to all K8s nodes
#  via SFTP, imports them via containerd's ctr CLI, applies any updated K8s
#  manifests, then waits for the rollout.
#
#  Prereq: cluster up (run start-aiops.ps1 first), tars present in $env:TEMP.
# ============================================================================
[CmdletBinding()]
param(
    [string]$BackendTag  = "pfe-backend:0.2.12",
    [string]$FrontendTag = "pfe-frontend:1.0.6",
    [switch]$ApplyManifests = $true
)
$ErrorActionPreference = 'Stop'

Import-Module Posh-SSH -ErrorAction Stop
$cred = New-Object System.Management.Automation.PSCredential('kube',
    (ConvertTo-SecureString 'kube' -AsPlainText -Force))

$nodes = @(
    @{ip='192.168.148.153'; name='master'},
    @{ip='192.168.148.158'; name='worker1'},
    @{ip='192.168.148.155'; name='worker2'}
)

$beTar = "$env:TEMP\$($BackendTag -replace ':','-').tar"
$feTar = "$env:TEMP\$($FrontendTag -replace ':','-').tar"
$repoRoot = Resolve-Path "$PSScriptRoot\.."
$k8sDir   = "$repoRoot\backend\backend\infra\k8s"

function Write-Step($msg) { Write-Host "[$([DateTime]::Now.ToString('HH:mm:ss'))] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  $msg" -ForegroundColor Green }
function Write-Err($msg)  { Write-Host "  $msg" -ForegroundColor Red }

if (-not (Test-Path $beTar)) { Write-Err "Backend tar missing: $beTar"; exit 1 }
if (-not (Test-Path $feTar)) { Write-Err "Frontend tar missing: $feTar"; exit 1 }

# ── Open SSH sessions ────────────────────────────────────────────────────
Write-Step "Opening SSH sessions to 3 K8s nodes..."
$sessions = @{}
foreach ($n in $nodes) {
    $sessions[$n.name] = New-SSHSession -ComputerName $n.ip -Credential $cred -AcceptKey -ConnectionTimeout 15
}
Write-Ok "3 sessions open"

function Deploy-Tar($node, $localTar, $tarName) {
    Write-Step "  $($node.name): upload $tarName..."
    $sftp = New-SFTPSession -ComputerName $node.ip -Credential $cred -AcceptKey -ConnectionTimeout 30
    Set-SFTPItem -SessionId $sftp.SessionId -Path $localTar -Destination '/tmp/' -Force
    Remove-SFTPSession -SessionId $sftp.SessionId | Out-Null
    Write-Step "  $($node.name): ctr import..."
    $r = Invoke-SSHCommand -SessionId $sessions[$node.name].SessionId `
        -Command "echo kube | sudo -S ctr -n k8s.io images import /tmp/$tarName 2>&1 | tail -2 && rm /tmp/$tarName" `
        -TimeOut 120
    Write-Ok "  $($r.Output -join ' | ')"
}

# ── Distribute images ────────────────────────────────────────────────────
Write-Step "Distributing $BackendTag to 3 nodes"
foreach ($n in $nodes) { Deploy-Tar $n $beTar (Split-Path -Leaf $beTar) }
Write-Step "Distributing $FrontendTag to 3 nodes"
foreach ($n in $nodes) { Deploy-Tar $n $feTar (Split-Path -Leaf $feTar) }

# ── Apply manifests if requested ─────────────────────────────────────────
if ($ApplyManifests) {
    Write-Step "Applying updated K8s manifests"
    foreach ($f in @('backend-deployment.yaml','backend-hpa.yaml','frontend-deployment.yaml')) {
        $local = "$k8sDir\$f"
        if (-not (Test-Path $local)) { Write-Host "    skip (not found): $f" -ForegroundColor DarkGray; continue }
        $sftp = New-SFTPSession -ComputerName '192.168.148.153' -Credential $cred -AcceptKey
        Set-SFTPItem -SessionId $sftp.SessionId -Path $local -Destination '/tmp/' -Force
        Remove-SFTPSession -SessionId $sftp.SessionId | Out-Null
        $r = Invoke-SSHCommand -SessionId $sessions['master'].SessionId `
            -Command "kubectl apply -f /tmp/$f && rm /tmp/$f" -TimeOut 20
        Write-Ok "    $f -> $($r.Output -join ' | ')"
    }
}

# ── Force the new images via kubectl set image (idempotent, triggers rollout)─
Write-Step "Setting backend image: $BackendTag"
(Invoke-SSHCommand -SessionId $sessions['master'].SessionId `
    -Command "kubectl -n pfe set image deployment/backend backend=$BackendTag" -TimeOut 15).Output | Out-Host
Write-Step "Setting frontend image: $FrontendTag"
(Invoke-SSHCommand -SessionId $sessions['master'].SessionId `
    -Command "kubectl -n pfe set image deployment/frontend frontend=$FrontendTag" -TimeOut 15).Output | Out-Host

# ── Wait for rollouts ────────────────────────────────────────────────────
Write-Step "Waiting for rollouts (timeout 4 min)"
(Invoke-SSHCommand -SessionId $sessions['master'].SessionId `
    -Command 'kubectl -n pfe rollout status deployment/backend --timeout=240s 2>&1' -TimeOut 260).Output | Out-Host
(Invoke-SSHCommand -SessionId $sessions['master'].SessionId `
    -Command 'kubectl -n pfe rollout status deployment/frontend --timeout=120s 2>&1' -TimeOut 140).Output | Out-Host

# ── Final state ──────────────────────────────────────────────────────────
Write-Step "Final state:"
(Invoke-SSHCommand -SessionId $sessions['master'].SessionId `
    -Command 'kubectl get pods -n pfe -o wide' -TimeOut 10).Output | Out-Host

foreach ($n in $nodes) { Remove-SSHSession -SessionId $sessions[$n.name].SessionId | Out-Null }

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  DEPLOY COMPLETE" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  Frontend : http://192.168.148.153:30880" -ForegroundColor White
Write-Host "  Backend  : http://192.168.148.153:30880/actuator/health" -ForegroundColor White
