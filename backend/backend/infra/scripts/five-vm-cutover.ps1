param(
    [string]$KubeconfigPath = "",
    [string]$Namespace = "pfe",
    [switch]$RemoveLegacyAiService = $true
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
if ([string]::IsNullOrWhiteSpace($KubeconfigPath)) {
    $KubeconfigPath = Join-Path $env:TEMP "pfe-kubeconfig-master"
}

Write-Host "Applying repo-managed manifests for the 5-VM target..." -ForegroundColor Cyan
kubectl --kubeconfig $KubeconfigPath apply -k (Join-Path $repoRoot "infra\\k8s")
kubectl --kubeconfig $KubeconfigPath apply -k (Join-Path $repoRoot "infra\\k8s\\monitoring")

if ($RemoveLegacyAiService) {
    Write-Host "Removing legacy in-cluster ai-service deployment and service..." -ForegroundColor Yellow
    kubectl --kubeconfig $KubeconfigPath -n $Namespace delete deploy ai-service --ignore-not-found
    kubectl --kubeconfig $KubeconfigPath -n $Namespace delete svc ai-service --ignore-not-found
    kubectl --kubeconfig $KubeconfigPath -n monitoring delete servicemonitor ai-service --ignore-not-found
}

Write-Host ""
Write-Host "Five-VM cutover steps applied." -ForegroundColor Green
Write-Host "Manual VM follow-up still required:" -ForegroundColor Yellow
Write-Host "  1. Re-run install-monitoring-vm.sh on 192.168.148.160"
Write-Host "  2. Re-run reconfigure-ai-telemetry.sh on 192.168.148.161"
Write-Host "  3. Validate that monitoring VM Prometheus sees job=backend and AI VM still answers on :8000"
