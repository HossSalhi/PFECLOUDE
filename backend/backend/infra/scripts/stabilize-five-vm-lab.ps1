param(
    [string]$KubeconfigPath = "investigation_outputs/live-kubeconfig-master",
    [string]$Namespace = "pfe",
    [string]$ClusterName = "postgres",
    [string]$MonitoringVmIp = "192.168.148.160",
    [string]$AiCamundaVmIp = "192.168.148.161",
    [string]$Worker1Ip = "192.168.148.158",
    [string]$Worker2Ip = "192.168.148.155",
    [string]$SshUser = "kube",
    [string]$VmPassword = "kube",
    [switch]$RepairVms,
    [switch]$RepairCnpgReplica,
    [int]$CnpgWaitMinutes = 15,
    [int]$PodTcpWaitSeconds = 90,
    [int]$PodTcpRetryDelaySeconds = 10,
    [int]$KubeApiWaitSeconds = 180,
    [int]$KubeApiRetryDelaySeconds = 5
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not [System.IO.Path]::IsPathRooted($KubeconfigPath)) {
    $KubeconfigPath = Join-Path $repoRoot $KubeconfigPath
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Kubectl {
    param([string[]]$Arguments)
    & kubectl --kubeconfig $KubeconfigPath @Arguments
}

function Test-KubeApiReachable {
    try {
        $null = & kubectl --kubeconfig $KubeconfigPath get --raw=/readyz?verbose --request-timeout=20s 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Wait-KubeApiReachable {
    param(
        [string]$Context,
        [int]$TimeoutSeconds = $KubeApiWaitSeconds,
        [int]$RetryDelaySeconds = $KubeApiRetryDelaySeconds
    )

    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
    do {
        if (Test-KubeApiReachable) {
            return $true
        }

        Write-Host "Kubernetes API not ready yet during $Context. Retrying in ${RetryDelaySeconds}s..." -ForegroundColor Yellow
        Start-Sleep -Seconds ([Math]::Max(1, $RetryDelaySeconds))
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Assert-KubeApiReachable {
    param([string]$Context)

    if (-not (Wait-KubeApiReachable -Context $Context)) {
        throw "Kubernetes API is unreachable during: $Context. Check that k8s-master (192.168.148.153) is running and reachable."
    }
}

function Get-CnpgCluster {
    Assert-KubeApiReachable -Context "fetching CNPG cluster status"
    $json = Invoke-Kubectl @("-n", $Namespace, "get", "cluster", $ClusterName, "-o", "json") | ConvertFrom-Json
    if ($null -eq $json -or -not ($json.PSObject.Properties.Name -contains "status")) {
        throw "CNPG cluster payload is empty or missing status."
    }
    return $json
}

function Test-TcpFromPod {
    param(
        [Parameter(Mandatory = $true)][string]$Pod,
        [Parameter(Mandatory = $true)][string]$HostName,
        [Parameter(Mandatory = $true)][int]$Port
    )

    $cmd = "timeout 5 bash -lc '</dev/tcp/$HostName/$Port'"
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & kubectl --kubeconfig $KubeconfigPath -n $Namespace exec $Pod -- bash -lc $cmd *> $null
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    return ($exitCode -eq 0)
}

function Wait-TcpFromPod {
    param(
        [Parameter(Mandatory = $true)][string]$Pod,
        [Parameter(Mandatory = $true)][string]$HostName,
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutSeconds = $PodTcpWaitSeconds,
        [int]$RetryDelaySeconds = $PodTcpRetryDelaySeconds
    )

    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
    do {
        if (Test-TcpFromPod -Pod $Pod -HostName $HostName -Port $Port) {
            return $true
        }

        Write-Host "Pod $Pod still cannot reach ${HostName}:$Port. Retrying in ${RetryDelaySeconds}s..." -ForegroundColor Yellow
        Start-Sleep -Seconds ([Math]::Max(1, $RetryDelaySeconds))
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Wait-CnpgHealthy {
    param([int]$Minutes)

    $deadline = (Get-Date).AddMinutes($Minutes)
    do {
        $cluster = Get-CnpgCluster
        $readyInstances = 0
        if ($cluster.status.PSObject.Properties.Name -contains "readyInstances" -and $null -ne $cluster.status.readyInstances) {
            $readyInstances = [int]$cluster.status.readyInstances
        }
        $phase = "$($cluster.status.phase)"
        $primary = "$($cluster.status.currentPrimary)"
        Write-Host "CNPG: readyInstances=$readyInstances phase=$phase primary=$primary"

        if ($readyInstances -eq 2 -and $phase -eq "Cluster in healthy state") {
            return $true
        }

        Start-Sleep -Seconds 20
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Repair-CnpgReplica {
    $cluster = Get-CnpgCluster
    $primary = "$($cluster.status.currentPrimary)"
    if ([string]::IsNullOrWhiteSpace($primary)) {
        throw "CNPG has no currentPrimary. Refusing automated replica repair."
    }

    Write-Step "Confirming current primary $primary is writable"
    $recovery = Invoke-Kubectl @("-n", $Namespace, "exec", $primary, "--", "bash", "-lc", "psql -U postgres -d postgres -tAc 'select pg_is_in_recovery();'") 2>&1
    if (($recovery -join " ") -notmatch "\bf\b") {
        throw "Primary $primary is not confirmed writable. Refusing automated repair. Output: $($recovery -join ' ')"
    }

    Write-Step "Checking primary-to-MinIO reachability"
    if (-not (Wait-TcpFromPod -Pod $primary -HostName $MonitoringVmIp -Port 9000)) {
        throw "Primary pod cannot reach MinIO at ${MonitoringVmIp}:9000. Run with -RepairVms or repair monitoring VM routes first."
    }

    $pods = Invoke-Kubectl @("-n", $Namespace, "get", "pods", "-l", "cnpg.io/cluster=$ClusterName", "-o", "json") | ConvertFrom-Json
    $replicaPods = @($pods.items | Where-Object { $_.metadata.name -ne $primary })

    foreach ($pod in $replicaPods) {
        $name = "$($pod.metadata.name)"
        $readyCondition = @($pod.status.conditions | Where-Object { $_.type -eq "Ready" } | Select-Object -First 1)
        $isReady = ($readyCondition.Count -gt 0 -and "$($readyCondition[0].status)" -eq "True")
        if ($isReady) {
            continue
        }

        Write-Step "Recycling non-ready replica pod $name without deleting PVC"
        Invoke-Kubectl @("-n", $Namespace, "delete", "pod", $name, "--ignore-not-found=true") | Out-Host
    }

    if (Wait-CnpgHealthy -Minutes 5) {
        return
    }

    $pods = Invoke-Kubectl @("-n", $Namespace, "get", "pods", "-l", "cnpg.io/cluster=$ClusterName", "-o", "json") | ConvertFrom-Json
    $cluster = Get-CnpgCluster
    $primary = "$($cluster.status.currentPrimary)"
    $badReplicas = @($pods.items | Where-Object {
        $_.metadata.name -ne $primary -and
        @($_.status.conditions | Where-Object { $_.type -eq "Ready" -and $_.status -eq "True" }).Count -eq 0
    })

    foreach ($pod in $badReplicas) {
        $name = "$($pod.metadata.name)"
        Write-Step "Rebuilding non-ready replica $name and its PVC"
        Invoke-Kubectl @("-n", $Namespace, "delete", "pod", $name, "--ignore-not-found=true") | Out-Host
        Invoke-Kubectl @("-n", $Namespace, "delete", "pvc", $name, "--ignore-not-found=true") | Out-Host
    }
}

function Repair-PersistentVms {
    Write-Step "Repairing persistent VM routes/services"

    $previousPythonEncoding = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = "utf-8"
    try {
        & python (Join-Path $PSScriptRoot "repair-five-vm-services.py") `
            --username $SshUser `
            --password $VmPassword `
            --monitoring-ip $MonitoringVmIp `
            --ai-camunda-ip $AiCamundaVmIp `
            --worker1-ip $Worker1Ip `
            --worker2-ip $Worker2Ip `
            --repo-root $repoRoot
        if ($LASTEXITCODE -ne 0) {
            throw "Persistent VM repair command failed with exit code $LASTEXITCODE"
        }
    } finally {
        $env:PYTHONIOENCODING = $previousPythonEncoding
    }
}

Write-Step "Checking workstation-to-VM ports"
$requiredPorts = @(
    @{ Host = $MonitoringVmIp; Port = 9000; Name = "MinIO" },
    @{ Host = $MonitoringVmIp; Port = 9090; Name = "Prometheus" },
    @{ Host = $MonitoringVmIp; Port = 3100; Name = "Loki API" },
    @{ Host = $AiCamundaVmIp; Port = 8000; Name = "AI service" },
    @{ Host = $AiCamundaVmIp; Port = 8088; Name = "Camunda REST proxy" },
    @{ Host = $AiCamundaVmIp; Port = 26500; Name = "Camunda gRPC" }
)

foreach ($target in $requiredPorts) {
    $test = Test-NetConnection -ComputerName $target.Host -Port $target.Port -WarningAction SilentlyContinue
    if (-not $test.TcpTestSucceeded) {
        Write-Warning "$($target.Name) is not reachable at $($target.Host):$($target.Port)"
    } else {
        Write-Host "OK $($target.Name) $($target.Host):$($target.Port)"
    }
}

if ($RepairVms) {
    Repair-PersistentVms
}

Write-Step "Checking Kubernetes nodes and pfe workload"
Assert-KubeApiReachable -Context "listing nodes and workloads"
Invoke-Kubectl @("get", "nodes", "-o", "wide") | Out-Host
Invoke-Kubectl @("-n", $Namespace, "get", "pods,pvc", "-o", "wide") | Out-Host

Write-Step "Checking pod-to-MinIO connectivity"
$cluster = Get-CnpgCluster
$primaryPod = "$($cluster.status.currentPrimary)"
if ([string]::IsNullOrWhiteSpace($primaryPod)) {
    throw "No CNPG primary found."
}
if (-not (Wait-TcpFromPod -Pod $primaryPod -HostName $MonitoringVmIp -Port 9000)) {
    Write-Warning "Primary pod $primaryPod cannot reach MinIO at ${MonitoringVmIp}:9000 right now. Continuing because the cluster can still be healthy enough for the demo test."
} else {
    Write-Host "OK $primaryPod can reach MinIO at ${MonitoringVmIp}:9000"
}

Write-Step "Waiting for CNPG healthy state"
if (-not (Wait-CnpgHealthy -Minutes $CnpgWaitMinutes)) {
    if (-not $RepairCnpgReplica) {
        throw "CNPG did not become healthy. Re-run with -RepairCnpgReplica after confirming the primary is safe."
    }

    Repair-CnpgReplica
    if (-not (Wait-CnpgHealthy -Minutes $CnpgWaitMinutes)) {
        throw "CNPG is still not healthy after automated replica repair."
    }
}

Write-Step "Final readiness"
Invoke-Kubectl @("-n", $Namespace, "get", "pods,pvc,endpoints", "-o", "wide") | Out-Host
Invoke-Kubectl @("-n", $Namespace, "get", "cluster", $ClusterName) | Out-Host
Write-Host ""
Write-Host "Five-VM lab is stable enough for the final demo test." -ForegroundColor Green
