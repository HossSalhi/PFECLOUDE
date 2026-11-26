param(
    [string]$BackendTag = "0.1.0",
    [switch]$IncludeServiceMonitors
)

# Retrait de l'IA (11 nov. 2026) : ce script ne déploie plus que le backend (le service IA, Ollama et ChromaDB
# ont été retirés du dépôt ; archive de sécurité : helios-state\snapshots\ai-removal-20261106).

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

Assert-Command kubectl

Write-Host "Applying base manifests with kustomize..." -ForegroundColor Cyan
kubectl apply -k "infra/k8s"

Write-Host "Pinning runtime image tags..." -ForegroundColor Cyan
kubectl -n pfe set image deployment/backend "backend=pfe-backend:$BackendTag"

if ($IncludeServiceMonitors) {
    Write-Host "Applying optional ServiceMonitors..." -ForegroundColor Cyan
    kubectl apply -f "infra/k8s/backend-servicemonitor.yaml"
}

Write-Host "Waiting for rollouts..." -ForegroundColor Cyan
kubectl -n pfe rollout status deployment/backend --timeout=180s

Write-Host "`nDeployment summary:" -ForegroundColor Green
kubectl -n pfe get deploy,pods,svc -o wide
