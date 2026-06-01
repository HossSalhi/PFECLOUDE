param(
    [string]$Kubeconfig = "investigation_outputs/2026-04-11-manual-steps-automation/kubeconfig-master",
    [string]$Namespace = "pfe",
    [switch]$KeepCluster
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$manifest = Join-Path $repoRoot "infra\k8s\postgres-restore-smoke.yaml"

& kubectl --kubeconfig $Kubeconfig apply -f $manifest
& kubectl --kubeconfig $Kubeconfig -n $Namespace wait --for=condition=Ready cluster/postgres-restore-smoke --timeout=900s

$appSecretJson = & kubectl --kubeconfig $Kubeconfig -n $Namespace get secret postgres-app -o json
$appSecret = $appSecretJson | ConvertFrom-Json
$username = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($appSecret.data.username))
$password = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($appSecret.data.password))

$primaryPod = & kubectl --kubeconfig $Kubeconfig -n $Namespace get pods `
    -l cnpg.io/cluster=postgres-restore-smoke,role=primary `
    -o jsonpath="{.items[0].metadata.name}"

if ([string]::IsNullOrWhiteSpace($primaryPod)) {
    throw "Unable to resolve restore-smoke primary pod"
}

& kubectl --kubeconfig $Kubeconfig -n $Namespace exec $primaryPod -- `
    env "PGPASSWORD=$password" `
    psql -U $username -d pfe_db -tAc "select 1"

if (-not $KeepCluster) {
    & kubectl --kubeconfig $Kubeconfig -n $Namespace delete cluster postgres-restore-smoke --wait=true
}
