param(
    [string]$OutputRoot = "investigation_outputs/manual-validation"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Save-CmdOutput {
    param(
        [string]$FileName,
        [scriptblock]$Command
    )
    $path = Join-Path $runDir $FileName
    & $Command | Out-File -FilePath $path -Encoding utf8
    Write-Host "Saved: $path"
}

Assert-Command kubectl

$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$runDir = Join-Path $OutputRoot $timestamp
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

Write-Host "Collecting Kubernetes validation snapshot..." -ForegroundColor Cyan

Save-CmdOutput "01-nodes.txt" { kubectl get nodes -o wide }
Save-CmdOutput "02-namespaces.txt" { kubectl get ns }
Save-CmdOutput "03-pods-all.txt" { kubectl get pods -A -o wide }
Save-CmdOutput "04-services-all.txt" { kubectl get svc -A }
Save-CmdOutput "05-workloads-all.txt" { kubectl get deploy,statefulset,daemonset -A }
Save-CmdOutput "06-pvc-pv-all.txt" { kubectl get pvc,pv -A }
Save-CmdOutput "07-storageclass.txt" { kubectl get storageclass }
Save-CmdOutput "08-events-all.txt" { kubectl get events -A --sort-by=.lastTimestamp }

Save-CmdOutput "09-pfe-deployments.txt" { kubectl -n pfe get deploy -o wide }
Save-CmdOutput "10-pfe-pods.txt" { kubectl -n pfe get pods -o wide }
Save-CmdOutput "11-backend-config.txt" { kubectl -n pfe get configmap backend-config -o yaml }
Save-CmdOutput "12-postgres-cluster.txt" { kubectl -n pfe get cluster postgres -o yaml }

Write-Host "`nAttempting metrics API checks (may fail if metrics-server is missing)..." -ForegroundColor Yellow
try {
    Save-CmdOutput "13-top-nodes.txt" { kubectl top nodes }
    Save-CmdOutput "14-top-pods-all.txt" { kubectl top pods -A }
} catch {
    $_ | Out-File -FilePath (Join-Path $runDir "13-14-top-errors.txt") -Encoding utf8
}

Write-Host "`nValidation snapshot complete. Output directory: $runDir" -ForegroundColor Green
