param(
    [string]$Kubeconfig = "investigation_outputs/2026-04-11-manual-steps-automation/kubeconfig-master",
    [string]$Namespace = "pfe"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$clusterManifest = Join-Path $repoRoot "infra\k8s\postgres-cluster.yaml"
$scheduledBackupManifest = Join-Path $repoRoot "infra\k8s\postgres-scheduled-backup.yaml"
$syncScript = Join-Path $repoRoot "infra\scripts\sync-cnpg-backup-secret.ps1"

& $syncScript -Kubeconfig $Kubeconfig -Namespace $Namespace
& kubectl --kubeconfig $Kubeconfig apply -f $clusterManifest
& kubectl --kubeconfig $Kubeconfig apply -f $scheduledBackupManifest
& kubectl --kubeconfig $Kubeconfig -n $Namespace wait --for=condition=Ready cluster/postgres --timeout=600s
& kubectl --kubeconfig $Kubeconfig -n $Namespace get scheduledbackup postgres-scheduled-backup
