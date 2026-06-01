param(
    [Alias("KubeconfigPath")]
    [string]$Kubeconfig = "investigation_outputs/2026-04-11-manual-steps-automation/kubeconfig-master",
    [string]$Namespace = "pfe",
    [string]$OutputRoot = "investigation_outputs",
    [string]$MonitoringVmIp = "192.168.148.160",
    [string]$AiCamundaVmIp = "192.168.148.161",
    [int[]]$MonitoringPorts = @(9000, 3000, 3100, 9090),
    [int[]]$AiCamundaPorts = @(8000, 8088, 26500)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$outputDir = Join-Path $OutputRoot "$timestamp-pretest-readiness"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$results = [System.Collections.Generic.List[object]]::new()

function Save-Text {
    param(
        [string]$Name,
        [string[]]$Content
    )

    $path = Join-Path $outputDir $Name
    $Content | Set-Content -Path $path
}

function Add-Result {
    param(
        [string]$Check,
        [string]$Status,
        [string]$Summary
    )

    $results.Add([pscustomobject]@{
        Check = $Check
        Status = $Status
        Summary = $Summary
    }) | Out-Null
}

function Invoke-KubectlCapture {
    param(
        [string]$Name,
        [string[]]$Arguments
    )

    $content = & kubectl --kubeconfig $Kubeconfig @Arguments 2>&1
    Save-Text -Name $Name -Content ($content | ForEach-Object { "$_" })
    return $content
}

function Test-RemotePorts {
    param(
        [string]$Check,
        [string]$ComputerName,
        [int[]]$Ports,
        [string]$OutputName,
        [string]$PassSummary,
        [string]$FailSummary
    )

    $checks = foreach ($port in $Ports) {
        $test = Test-NetConnection -ComputerName $ComputerName -Port $port -WarningAction SilentlyContinue
        [pscustomobject]@{
            Port = $port
            TcpTestSucceeded = $test.TcpTestSucceeded
        }
    }

    Save-Text -Name $OutputName -Content ($checks | ForEach-Object {
        "target=$ComputerName port=$($_.Port) tcp=$($_.TcpTestSucceeded)"
    })

    if (@($checks | Where-Object { -not $_.TcpTestSucceeded }).Count -eq 0) {
        Add-Result -Check $Check -Status "PASS" -Summary $PassSummary
    } else {
        Add-Result -Check $Check -Status "FAIL" -Summary $FailSummary
    }
}

function Invoke-WebCapture {
    param(
        [string]$Name,
        [string]$Uri
    )

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 20
        Save-Text -Name $Name -Content @($response.Content)
        return $response.Content
    } catch {
        Save-Text -Name $Name -Content @($_.Exception.Message)
        return $null
    }
}

Write-Host "Collecting pre-test readiness snapshot into $outputDir" -ForegroundColor Cyan

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$hostIdentity = @(
    "user=$([Security.Principal.WindowsIdentity]::GetCurrent().Name)"
    "isAdmin=$isAdmin"
)
Save-Text -Name "00-host-identity.txt" -Content $hostIdentity
if ($isAdmin) {
    Add-Result -Check "OperatorShell" -Status "PASS" -Summary "Shell is elevated if route or firewall adjustments become necessary."
} else {
    Add-Result -Check "OperatorShell" -Status "WARN" -Summary "Shell is not elevated; VM-level network fixes may need an admin session."
}

Test-RemotePorts `
    -Check "MonitoringVmPorts" `
    -ComputerName $MonitoringVmIp `
    -Ports $MonitoringPorts `
    -OutputName "01-monitoring-vm-ports.txt" `
    -PassSummary "Monitoring VM ports are reachable from the operator workstation." `
    -FailSummary "One or more required monitoring VM ports are unreachable from the operator workstation."

Test-RemotePorts `
    -Check "AiCamundaVmPorts" `
    -ComputerName $AiCamundaVmIp `
    -Ports $AiCamundaPorts `
    -OutputName "02-ai-camunda-vm-ports.txt" `
    -PassSummary "AI/Camunda VM ports are reachable from the operator workstation." `
    -FailSummary "One or more required AI/Camunda VM ports are unreachable from the operator workstation."

$null = Invoke-KubectlCapture -Name "03-nodes-wide.txt" -Arguments @("get", "nodes", "-o", "wide")
$null = Invoke-KubectlCapture -Name "04-pfe-pods-wide.txt" -Arguments @("-n", $Namespace, "get", "pods", "-o", "wide")
$null = Invoke-KubectlCapture -Name "05-service-endpoints.txt" -Arguments @("-n", $Namespace, "get", "endpoints", "backend", "postgres-rw", "postgres-ro", "postgres-r")
$null = Invoke-KubectlCapture -Name "06-backend-config.txt" -Arguments @("-n", $Namespace, "get", "configmap", "backend-config", "-o", "yaml")
$null = Invoke-KubectlCapture -Name "07-cnpg-cluster.txt" -Arguments @("-n", $Namespace, "get", "cluster", "postgres", "-o", "yaml")
$null = Invoke-KubectlCapture -Name "08-cnpg-backups.txt" -Arguments @("-n", $Namespace, "get", "backup,scheduledbackup")

$backendPodsJson = & kubectl --kubeconfig $Kubeconfig -n $Namespace get pods -l app=backend -o json | ConvertFrom-Json
$backendPods = @($backendPodsJson.items)
$backendNodes = @($backendPods | ForEach-Object { $_.spec.nodeName } | Sort-Object -Unique)
if ($backendPods.Count -eq 2 -and $backendNodes.Count -eq 2) {
    Add-Result -Check "BackendPlacement" -Status "PASS" -Summary "Backend replicas are split across both worker nodes."
} else {
    Add-Result -Check "BackendPlacement" -Status "FAIL" -Summary "Backend replicas are not cleanly spread across two workers."
}

$backendConfigMap = & kubectl --kubeconfig $Kubeconfig -n $Namespace get configmap backend-config -o json | ConvertFrom-Json
$camundaEnabled = $backendConfigMap.data.CAMUNDA_ENABLED
$automationEnabled = $backendConfigMap.data.REMEDIATION_AUTOMATION_ENABLED
$aiBaseUrl = "$($backendConfigMap.data.AIOPS_SERVICE_BASE_URL)"
$camundaRest = "$($backendConfigMap.data.CAMUNDA_REST_ADDRESS)"
$camundaGrpc = "$($backendConfigMap.data.CAMUNDA_GRPC_ADDRESS)"

if (
    $aiBaseUrl -match [regex]::Escape($AiCamundaVmIp) -and
    $camundaRest -match [regex]::Escape($AiCamundaVmIp) -and
    $camundaGrpc -match [regex]::Escape($AiCamundaVmIp)
) {
    Add-Result -Check "ExternalServiceTargets" -Status "PASS" -Summary "Backend points to the dedicated AI/Camunda VM."
} else {
    Add-Result -Check "ExternalServiceTargets" -Status "FAIL" -Summary "Backend config still points to an unexpected AI or Camunda endpoint."
}

if ($camundaEnabled -eq "false" -and $automationEnabled -eq "false") {
    Add-Result -Check "AutomationFlags" -Status "PASS" -Summary "Camunda and automation remain disabled for safe pre-test recovery."
} else {
    Add-Result -Check "AutomationFlags" -Status "WARN" -Summary "One or more orchestration automation flags are enabled."
}

$monitoringBackendQuery = Invoke-WebCapture `
    -Name "06a-monitoring-vm-backend-query.json" `
    -Uri "http://$MonitoringVmIp`:9090/api/v1/query?query=up%7Bjob=%22backend%22%7D"
if ($monitoringBackendQuery -and $monitoringBackendQuery -match '"result":\[(?s:.+)\]') {
    Add-Result -Check "MonitoringVmBackendMetrics" -Status "PASS" -Summary "Monitoring VM Prometheus can see backend metrics."
} else {
    Add-Result -Check "MonitoringVmBackendMetrics" -Status "FAIL" -Summary "Monitoring VM Prometheus does not currently expose backend metrics."
}

$monitoringLokiLabels = Invoke-WebCapture `
    -Name "06b-monitoring-vm-loki-labels.json" `
    -Uri "http://$MonitoringVmIp`:3100/loki/api/v1/labels"
if ($monitoringLokiLabels -and $monitoringLokiLabels -match '"status":"success"') {
    Add-Result -Check "MonitoringVmLoki" -Status "PASS" -Summary "Monitoring VM exposes a Loki-compatible API."
} else {
    Add-Result -Check "MonitoringVmLoki" -Status "FAIL" -Summary "Monitoring VM Loki endpoint is not healthy."
}

$probePod = ""
if ($backendPods.Count -gt 0) {
    $probePod = "$($backendPods[0].metadata.name)"
}
foreach ($candidate in @("postgres-2", "postgres-1")) {
    if (-not [string]::IsNullOrWhiteSpace($probePod)) {
        break
    }
    $podName = (& kubectl --kubeconfig $Kubeconfig -n $Namespace get pod $candidate --ignore-not-found -o jsonpath="{.metadata.name}" 2>$null)
    if (-not [string]::IsNullOrWhiteSpace($podName)) {
        $probePod = $podName
    }
}

$podExternalConnectivityLines = @()
if ([string]::IsNullOrWhiteSpace($probePod)) {
    $podExternalConnectivityLines = @("no suitable probe pod found")
    Add-Result -Check "PodToExternalServices" -Status "FAIL" -Summary "Unable to locate a pod for external VM connectivity testing."
} else {
    $targets = @(
        "${MonitoringVmIp}:9000",
        "${AiCamundaVmIp}:8000",
        "${AiCamundaVmIp}:8088",
        "${AiCamundaVmIp}:26500"
    )
    $executionErrors = $false
    foreach ($target in $targets) {
        $parts = $target.Split(":")
        $targetHost = $parts[0]
        $port = $parts[1]
        $command = "timeout 5 bash -lc 'exec 3<>/dev/tcp/$targetHost/$port && exec 3<&- && exec 3>&-' >/dev/null 2>&1"

        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & kubectl --kubeconfig $Kubeconfig -n $Namespace exec $probePod -- bash -lc $command *> $null
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorActionPreference

        if ($exitCode -eq 0) {
            $podExternalConnectivityLines += "$target OK"
        } elseif ($exitCode -eq 1 -or $exitCode -eq 124) {
            $podExternalConnectivityLines += "$target ERROR"
        } else {
            $podExternalConnectivityLines += "$target EXECUTION_ERROR($exitCode)"
            $executionErrors = $true
        }
    }

    if ($executionErrors) {
        Add-Result -Check "PodToExternalServices" -Status "WARN" -Summary "Pod-side connectivity probe could not be executed reliably in the chosen probe container."
    } elseif (@($podExternalConnectivityLines | Where-Object { $_ -match " ERROR" }).Count -eq 0) {
        Add-Result -Check "PodToExternalServices" -Status "PASS" -Summary "Cluster pods can reach the monitoring and AI/Camunda VMs."
    } else {
        Add-Result -Check "PodToExternalServices" -Status "FAIL" -Summary "Cluster pod connectivity to one or more external VM services still failed with the direct TCP probe."
    }
}
Save-Text -Name "09-pod-to-external-services.txt" -Content $podExternalConnectivityLines

$cnpgJson = & kubectl --kubeconfig $Kubeconfig -n $Namespace get cluster postgres -o json | ConvertFrom-Json
$cnpgReadyCondition = @($cnpgJson.status.conditions | Where-Object { $_.type -eq "Ready" } | Select-Object -First 1)
$cnpgPhase = "$($cnpgJson.status.phase)"
$cnpgReady = if ($cnpgReadyCondition) { "$($cnpgReadyCondition.status)" } else { "Unknown" }
$cnpgPrimary = "$($cnpgJson.status.currentPrimary)"
$cnpgTargetPrimary = "$($cnpgJson.status.targetPrimary)"
$backupEndpoint = "$($cnpgJson.spec.backup.barmanObjectStore.endpointURL)"
if ($backupEndpoint -match [regex]::Escape($MonitoringVmIp)) {
    Add-Result -Check "BackupTargetConfig" -Status "PASS" -Summary "CNPG backup target points to the monitoring VM MinIO endpoint."
} else {
    Add-Result -Check "BackupTargetConfig" -Status "FAIL" -Summary "CNPG backup target does not point to the expected monitoring VM."
}
if ($cnpgReady -eq "True" -and $cnpgPhase -eq "Cluster in healthy state") {
    Add-Result -Check "CNPGCluster" -Status "PASS" -Summary "CNPG cluster is healthy with a writable primary."
} else {
    Add-Result -Check "CNPGCluster" -Status "FAIL" -Summary "CNPG is $cnpgPhase with Ready=$cnpgReady currentPrimary=$cnpgPrimary targetPrimary=$cnpgTargetPrimary."
}

$rwEndpoints = & kubectl --kubeconfig $Kubeconfig -n $Namespace get endpoints postgres-rw -o json | ConvertFrom-Json
$rwAddresses = @()
$rwSubsets = @()
if ($rwEndpoints.PSObject.Properties.Name -contains "subsets") {
    $rwSubsets = @($rwEndpoints.subsets)
}
if ($rwSubsets.Count -gt 0) {
    foreach ($subset in $rwSubsets) {
        foreach ($address in $subset.addresses) {
            $rwAddresses += "$($address.ip):$($subset.ports[0].port)"
        }
    }
}
Save-Text -Name "10-postgres-rw-endpoints.txt" -Content ($(if ($rwAddresses.Count -gt 0) { $rwAddresses } else { @("no endpoints") }))
if ($rwAddresses.Count -gt 0) {
    Add-Result -Check "PostgresRWEndpoint" -Status "PASS" -Summary "postgres-rw has at least one endpoint."
} else {
    Add-Result -Check "PostgresRWEndpoint" -Status "FAIL" -Summary "postgres-rw has no endpoints."
}

$primaryRecoveryLines = @("current primary not available")
if (-not [string]::IsNullOrWhiteSpace($cnpgPrimary)) {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $recoveryOutput = & kubectl --kubeconfig $Kubeconfig -n $Namespace exec $cnpgPrimary -- bash -lc "psql -U postgres -d postgres -tAc 'select pg_is_in_recovery();'" 2>&1
    $ErrorActionPreference = $previousErrorActionPreference
    $primaryRecoveryLines = @($recoveryOutput | ForEach-Object { "$_" } | Where-Object {
        $_ -notmatch "log.go:245" -and
        $_ -notmatch "Create stream|Reply frame received|Stream added|Data frame|Go away received|Stream removed"
    })
    if (($primaryRecoveryLines -join " ") -match "\bf\b") {
        Add-Result -Check "PostgresPrimaryWritable" -Status "PASS" -Summary "$cnpgPrimary is writable and no longer in recovery."
    } else {
        Add-Result -Check "PostgresPrimaryWritable" -Status "FAIL" -Summary "$cnpgPrimary is still in recovery or could not confirm writable state."
    }
}
Save-Text -Name "11-postgres-primary-recovery.txt" -Content $primaryRecoveryLines

$backupJson = & kubectl --kubeconfig $Kubeconfig -n $Namespace get backups.postgresql.cnpg.io -o json 2>$null
if ($LASTEXITCODE -eq 0 -and $backupJson) {
    $backupData = $backupJson | ConvertFrom-Json
    $completedBackups = @($backupData.items | Where-Object { $_.status.phase -eq "completed" })
    if ($completedBackups.Count -gt 0) {
        Add-Result -Check "CNPGBackup" -Status "PASS" -Summary "At least one CNPG backup completed successfully."
    } else {
        Add-Result -Check "CNPGBackup" -Status "FAIL" -Summary "No completed CNPG backup exists yet."
    }
} else {
    Add-Result -Check "CNPGBackup" -Status "FAIL" -Summary "Unable to read CNPG backup resources."
}

$legacyAiDeploy = & kubectl --kubeconfig $Kubeconfig -n $Namespace get deploy ai-service --ignore-not-found -o name 2>$null
if ([string]::IsNullOrWhiteSpace(($legacyAiDeploy | Out-String).Trim())) {
    Add-Result -Check "LegacyClusterAiService" -Status "PASS" -Summary "No legacy in-cluster ai-service deployment remains."
} else {
    Add-Result -Check "LegacyClusterAiService" -Status "WARN" -Summary "Legacy in-cluster ai-service deployment still exists."
}

$summaryLines = @(
    "timestamp=$((Get-Date).ToString("o"))",
    "kubeconfig=$Kubeconfig",
    "monitoringVmIp=$MonitoringVmIp",
    "aiCamundaVmIp=$AiCamundaVmIp",
    ""
)
$summaryLines += $results | ForEach-Object { "$($_.Status) $($_.Check): $($_.Summary)" }
Save-Text -Name "99-summary.txt" -Content $summaryLines

$results | Format-Table -AutoSize
Write-Host ""
Write-Host "Pre-test readiness snapshot written to $outputDir" -ForegroundColor Green
