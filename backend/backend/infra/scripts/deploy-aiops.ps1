param(
    [string]$BackendTag = "0.1.0",
    [string]$AIServiceTag = "0.1.0",
    [switch]$IncludeInClusterAIService,
    [switch]$IncludeServiceMonitors
)

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

if ($IncludeInClusterAIService) {
    Write-Host "Applying legacy in-cluster AI service manifests..." -ForegroundColor Cyan
    kubectl apply -f "infra/k8s/ai-service-configmap.yaml"
    kubectl apply -f "infra/k8s/ai-service-service.yaml"
    kubectl apply -f "infra/k8s/ai-service-deployment.yaml"
    kubectl -n pfe set image deployment/ai-service "ai-service=pfe-ai-service:$AIServiceTag"
}

if ($IncludeServiceMonitors) {
    Write-Host "Applying optional ServiceMonitors..." -ForegroundColor Cyan
    kubectl apply -f "infra/k8s/backend-servicemonitor.yaml"
    if ($IncludeInClusterAIService) {
        kubectl apply -f "infra/k8s/ai-service-servicemonitor.yaml"
    }
}

Write-Host "Waiting for rollouts..." -ForegroundColor Cyan
kubectl -n pfe rollout status deployment/backend --timeout=180s
if ($IncludeInClusterAIService) {
    kubectl -n pfe rollout status deployment/ai-service --timeout=180s
}

Write-Host "`nDeployment summary:" -ForegroundColor Green
kubectl -n pfe get deploy,pods,svc -o wide
