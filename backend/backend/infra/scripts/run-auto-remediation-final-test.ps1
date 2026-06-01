param(
    [string]$KubeconfigPath = "",
    [string]$Namespace = "pfe",
    [string]$App = "backend",
    [int]$WindowMinutes = 5,
    [ValidateSet("VmDown", "AvailabilityDrop", "CpuStress", "MemoryStress", "RestartLoop", "ApplicationErrorStorm", "DatabaseConnectivity", "GeneralDegradation", "CrashOnDemand")]
    [string]$FaultStrategy = "RestartLoop",
    [ValidateSet("Low", "Medium", "High")]
    [string]$Intensity = "Medium",
    [int]$CpuStressSeconds = 240,
    [int]$MemoryStressMb = 1024,
    [int]$MemoryHoldSeconds = 300,
    [int]$AvailabilityDropHoldSeconds = 150,
    [int]$RestartLoopCycles = 2,
    [int]$TrafficBurstRequests = 30,
    [int]$TrafficBurstDelayMs = 250,
    [string]$VmDownTargetNode = "k8s-worker1",
    [string]$VmDownTargetIp = "192.168.148.158",
    [string]$VmDownUsername = "kube",
    [string]$VmDownPassword = "kube",
    [int]$FirstPredictionDelaySeconds = 45,
    [int]$PredictionRetryDelaySeconds = 30,
    [int]$PredictionAttempts = 8,
    [int]$MonitorPollSeconds = 30,
    [int]$MonitorAttempts = 20
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
if ([string]::IsNullOrWhiteSpace($KubeconfigPath)) {
    $KubeconfigPath = Join-Path $repoRoot "investigation_outputs\\2026-04-11-manual-steps-automation\\kubeconfig-master"
}

$timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$outputDir = Join-Path $repoRoot "investigation_outputs\\$timestamp-auto-remediation-final-test"
$null = New-Item -ItemType Directory -Force -Path $outputDir

$pair = "operator:operator-change-me"
$token = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
$headers = @{ Authorization = "Basic $token" }
$headersJson = @{
    Authorization = "Basic $token"
    "Content-Type" = "application/json"
}

$controlPort = 18180
$injectPort = 18181
$controlPf = $null
$injectPf = $null
$workerOutageStarted = $false
$autoApprovedTaskIds = New-Object 'System.Collections.Generic.HashSet[string]'

if ($FaultStrategy -eq "CrashOnDemand") {
    $FaultStrategy = "RestartLoop"
}

function Save-Json {
    param(
        [Parameter(Mandatory = $true)] $Object,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $Object | ConvertTo-Json -Depth 40 | Set-Content -Path $Path
}

function Get-FirstValue {
    param(
        [Parameter(Mandatory = $true)] $Object,
        [Parameter(Mandatory = $true)][string[]]$Names
    )

    foreach ($name in $Names) {
        if ($Object -and $Object.PSObject.Properties.Name -contains $name) {
            return $Object.$name
        }
    }

    return $null
}

function Get-Collection {
    param(
        [Parameter(Mandatory = $true)] $Object,
        [string[]]$CandidateProperties = @("value", "content", "items")
    )

    if ($null -eq $Object) {
        return @()
    }

    if ($Object -is [System.Array]) {
        return @($Object)
    }

    foreach ($property in $CandidateProperties) {
        if ($Object.PSObject.Properties.Name -contains $property -and $null -ne $Object.$property) {
            return @($Object.$property)
        }
    }

    return @($Object)
}

function Select-FirstMatch {
    param(
        [Parameter(Mandatory = $true)] $Collection,
        [Parameter(Mandatory = $true)][scriptblock]$Predicate
    )

    $matches = @($Collection | Where-Object $Predicate)
    if ($matches.Count -gt 0) {
        return $matches[0]
    }

    return $null
}

function Get-IncidentPendingTaskIds {
    param(
        [Parameter(Mandatory = $true)] $PendingTasks,
        [Parameter(Mandatory = $true)][long]$IncidentId
    )

    $json = $PendingTasks | ConvertTo-Json -Depth 20
    $incidentPattern = [regex]::Escape("incident #$IncidentId")
    $regex = [regex]::new('(?s)\{[^{}]*"id"\s*:\s*(\d+)[^{}]*"taskName"\s*:\s*"[^"]*' + $incidentPattern + '[^"]*"[^{}]*"decision"\s*:\s*"PENDING"[^{}]*\}')

    $taskIds = @()
    foreach ($match in $regex.Matches($json)) {
        $taskIds += [long]$match.Groups[1].Value
    }

    return @($taskIds | Select-Object -Unique)
}

function Approve-IncidentPendingTasks {
    param(
        [Parameter(Mandatory = $true)] $PendingTasks,
        [Parameter(Mandatory = $true)][long]$IncidentId,
        [Parameter(Mandatory = $true)][int]$PollIndex,
        [Parameter(Mandatory = $true)][string]$OutputDir
    )

    $approvedTaskIds = @()
    $incidentTaskIds = Get-IncidentPendingTaskIds -PendingTasks $PendingTasks -IncidentId $IncidentId

    foreach ($incidentTaskId in $incidentTaskIds) {
        $taskId = [string]$incidentTaskId
        if ([string]::IsNullOrWhiteSpace($taskId) -or $autoApprovedTaskIds.Contains($taskId)) {
            continue
        }

        $approveResponse = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/tasks/$taskId/approve" -Method Post -RetryCount 3 -RetryDelaySeconds 3
        Save-Json -Object $approveResponse -Path (Join-Path $OutputDir ("33-approved-task-poll-{0}-task-{1}.json" -f $PollIndex, $taskId))

        $null = $autoApprovedTaskIds.Add($taskId)
        $approvedTaskIds += [long]$taskId
    }

    return @($approvedTaskIds)
}

function Invoke-BackendJson {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [ValidateSet("Get", "Post")][string]$Method = "Get",
        $Body = $null,
        [int]$TimeoutSec = 180,
        [int]$RetryCount = 1,
        [int]$RetryDelaySeconds = 3
    )

    $attempts = [Math]::Max($RetryCount, 1)
    $lastError = $null

    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        try {
            if ($null -ne $Body) {
                return Invoke-RestMethod -Uri $Uri -Headers $headersJson -Method $Method -Body ($Body | ConvertTo-Json) -TimeoutSec $TimeoutSec
            }

            return Invoke-RestMethod -Uri $Uri -Headers $headers -Method $Method -TimeoutSec $TimeoutSec
        } catch {
            $lastError = $_
            if ($attempt -lt $attempts) {
                if ($Uri -like "http://127.0.0.1:$controlPort/*" -and (Get-Command Restart-ControlPortForward -ErrorAction SilentlyContinue)) {
                    Restart-ControlPortForward
                }
                Start-Sleep -Seconds $RetryDelaySeconds
                continue
            }
        }
    }

    $exception = $lastError.Exception
    $responseBody = $null

    try {
        if ($exception.Response -and $exception.Response.GetResponseStream()) {
            $reader = New-Object System.IO.StreamReader($exception.Response.GetResponseStream())
            $responseBody = $reader.ReadToEnd()
            $reader.Dispose()
        }
    } catch {
        $responseBody = $null
    }

    if (-not [string]::IsNullOrWhiteSpace($responseBody)) {
        throw "HTTP request failed for $Method $Uri. Response body: $responseBody"
    }

    throw $lastError
}

function Wait-HttpReady {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$Attempts = 30,
        [int]$SleepSeconds = 2
    )

    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            return Invoke-RestMethod -Uri $Uri -Headers $headers -Method Get -TimeoutSec 5
        } catch {
            if ($Uri -like "http://127.0.0.1:$controlPort/*" -and (Get-Command Restart-ControlPortForward -ErrorAction SilentlyContinue)) {
                if ($i -ge 2) {
                    Restart-ControlPortForward
                }
            }
            Start-Sleep -Seconds $SleepSeconds
        }
    }

    throw "Timed out waiting for $Uri"
}

function Test-PodReadyStatus {
    param(
        [Parameter(Mandatory = $true)]$Pod
    )

    if ($null -eq $Pod -or $Pod.status.phase -ne "Running") {
        return $false
    }

    $readyCondition = @($Pod.status.conditions | Where-Object { $_.type -eq "Ready" } | Select-Object -First 1)
    return ($readyCondition.Count -gt 0 -and "$($readyCondition[0].status)" -eq "True")
}

function Wait-PodReadyState {
    param(
        [Parameter(Mandatory = $true)][string]$PodName,
        [Parameter(Mandatory = $true)][bool]$DesiredReady,
        [int]$Attempts = 24,
        [int]$SleepSeconds = 5
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $podJson = kubectl --kubeconfig $KubeconfigPath -n $Namespace get pod $PodName -o json
        $pod = ConvertFrom-Json ($podJson -join "`n")
        $isReady = Test-PodReadyStatus -Pod $pod
        if ($isReady -eq $DesiredReady) {
            return $pod
        }
        Start-Sleep -Seconds $SleepSeconds
    }

    throw "Pod $PodName did not reach DesiredReady=$DesiredReady within the allotted time."
}

function Get-AppPods {
    $podsJson = kubectl --kubeconfig $KubeconfigPath -n $Namespace get pods -l "app=$App" --sort-by=.metadata.creationTimestamp -o json
    return (ConvertFrom-Json ($podsJson -join "`n")).items
}

function Restart-CalicoNodeOnWorker {
    param(
        [Parameter(Mandatory = $true)][string]$NodeName
    )

    $calicoPods = (kubectl --kubeconfig $KubeconfigPath -n kube-system get pods -l "k8s-app=calico-node" -o json | ConvertFrom-Json).items
    $targetCalicoPod = @($calicoPods | Where-Object { $_.spec.nodeName -eq $NodeName } | Select-Object -First 1)
    if ($targetCalicoPod.Count -eq 0) {
        return $false
    }

    kubectl --kubeconfig $KubeconfigPath -n kube-system delete pod $targetCalicoPod[0].metadata.name | Out-Null
    kubectl --kubeconfig $KubeconfigPath -n kube-system rollout status ds/calico-node --timeout=240s | Out-Null
    return $true
}

function Wait-AppReplicasReady {
    param(
        [int]$MinimumReadyPods = 2,
        [int]$TimeoutSeconds = 300,
        [string]$PreferredNodeForRepair = ""
    )

    $deadline = (Get-Date).AddSeconds([Math]::Max(30, $TimeoutSeconds))
    $calicoRestartAttempted = $false
    $forcedDeleteAttempted = $false

    while ((Get-Date) -lt $deadline) {
        $pods = @(Get-AppPods)
        $readyPods = @($pods | Where-Object { Test-PodReadyStatus -Pod $_ })
        if ($readyPods.Count -ge $MinimumReadyPods) {
            return $true
        }

        if (-not $forcedDeleteAttempted) {
            $terminatingPods = @($pods | Where-Object {
                $_.metadata.PSObject.Properties.Name -contains "deletionTimestamp" -and $null -ne $_.metadata.deletionTimestamp
            })
            foreach ($terminatingPod in $terminatingPods) {
                kubectl --kubeconfig $KubeconfigPath -n $Namespace delete pod $terminatingPod.metadata.name --grace-period=0 --force | Out-Null
            }
            if ($terminatingPods.Count -gt 0) {
                $forcedDeleteAttempted = $true
            }
        }

        if (-not $calicoRestartAttempted -and -not [string]::IsNullOrWhiteSpace($PreferredNodeForRepair)) {
            $pendingOnPreferredNode = @($pods | Where-Object {
                $_.spec.nodeName -eq $PreferredNodeForRepair -and -not (Test-PodReadyStatus -Pod $_)
            })

            foreach ($candidatePod in $pendingOnPreferredNode) {
                $describeText = kubectl --kubeconfig $KubeconfigPath -n $Namespace describe pod $candidatePod.metadata.name 2>$null | Out-String
                if ($describeText -match "FailedCreatePodSandBox" -and $describeText -match "ClusterInformation: connection is unauthorized") {
                    if (Restart-CalicoNodeOnWorker -NodeName $PreferredNodeForRepair) {
                        $calicoRestartAttempted = $true
                    }
                    break
                }
            }
        }

        Start-Sleep -Seconds 10
    }

    return $false
}

function Wait-NodeCondition {
    param(
        [Parameter(Mandatory = $true)][string]$NodeName,
        [Parameter(Mandatory = $true)][ValidateSet("Ready", "NotReady")][string]$DesiredState,
        [int]$Attempts = 30,
        [int]$SleepSeconds = 5
    )

    for ($i = 1; $i -le $Attempts; $i++) {
        $node = kubectl --kubeconfig $KubeconfigPath get node $NodeName -o json | ConvertFrom-Json
        $readyCondition = @($node.status.conditions | Where-Object { $_.type -eq "Ready" } | Select-Object -First 1)
        if ($readyCondition.Count -eq 0) {
            Start-Sleep -Seconds $SleepSeconds
            continue
        }

        $status = "$($readyCondition[0].status)"
        if ($DesiredState -eq "Ready" -and $status -eq "True") {
            return $true
        }
        if ($DesiredState -eq "NotReady" -and $status -ne "True") {
            return $true
        }

        Start-Sleep -Seconds $SleepSeconds
    }

    return $false
}

function Invoke-WorkerOutageControl {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("stop", "start", "status")][string]$Action
    )

    & python (Join-Path $PSScriptRoot "control-worker-services.py") `
        --host $VmDownTargetIp `
        --username $VmDownUsername `
        --password $VmDownPassword `
        --action $Action | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Worker outage control action '$Action' failed for $VmDownTargetNode ($VmDownTargetIp)."
    }
}

function Get-EligiblePredictionThreshold {
    param(
        [Parameter(Mandatory = $true)][string]$Strategy
    )

    switch ($Strategy) {
        "VmDown" { return 0.30 }
        "AvailabilityDrop" { return 0.35 }
        "RestartLoop" { return 0.39 }
        "MemoryStress" { return 0.40 }
        "ApplicationErrorStorm" { return 0.35 }
        "DatabaseConnectivity" { return 0.35 }
        "GeneralDegradation" { return 0.35 }
        default { return 0.40 }
    }
}

function Test-PredictionMatchesStrategy {
    param(
        [Parameter(Mandatory = $true)][string]$Strategy,
        [Parameter(Mandatory = $false)]$Incident,
        [Parameter(Mandatory = $false)][string]$IncidentType,
        [Parameter(Mandatory = $false)][string]$RecommendedAction,
        [Parameter(Mandatory = $false)]$Confidence,
        [Parameter(Mandatory = $true)][double]$Threshold
    )

    if ($Incident -ne $true) {
        return $false
    }

    $confidenceValue = 0.0
    if (-not [double]::TryParse([string]$Confidence, [ref]$confidenceValue)) {
        return $false
    }

    if ($confidenceValue -lt $Threshold) {
        return $false
    }

    switch ($Strategy) {
        "VmDown" {
            return ([string]$IncidentType -eq "VM_DOWN" -and [string]$RecommendedAction -match "ESCALATE")
        }
        "AvailabilityDrop" {
            return (
                (
                    [string]$IncidentType -eq "AVAILABILITY_DROP" `
                    -and [string]$RecommendedAction -match "REBALANCE|SCALE|RESTART|INVESTIGATE|DIAGNOSTIC|LATENCY"
                ) -or (
                    [string]$IncidentType -eq "GENERAL_DEGRADATION" `
                    -and [string]$RecommendedAction -match "INVESTIGATE|DIAGNOSTIC|LATENCY"
                )
            )
        }
        "CpuStress" {
            return ([string]$IncidentType -eq "CPU_STRESS" -and [string]$RecommendedAction -match "CPU_PROFILE|RESTART|SCALE")
        }
        "MemoryStress" {
            return ([string]$IncidentType -eq "MEMORY_STRESS" -and [string]$RecommendedAction -match "MEMORY|RESTART|SCALE")
        }
        "RestartLoop" {
            return (
                ([string]$IncidentType -eq "RESTART_LOOP" -and [string]$RecommendedAction -match "RESTART|ROLLBACK|TRAFFIC|INVESTIGATE") -or (
                    [string]$IncidentType -eq "GENERAL_DEGRADATION" -and [string]$RecommendedAction -match "RESTART|TRAFFIC|INVESTIGATE"
                )
            )
        }
        "ApplicationErrorStorm" {
            return ([string]$IncidentType -eq "APPLICATION_ERROR_STORM" -and [string]$RecommendedAction -match "ROLLBACK|INVESTIGATE|TRAFFIC")
        }
        "DatabaseConnectivity" {
            return (
                ([string]$IncidentType -eq "DATABASE_CONNECTIVITY" -and [string]$RecommendedAction -match "DB|DATABASE|RESTART|INVESTIGATE|DIAGNOSTIC") -or (
                    [string]$IncidentType -eq "GENERAL_DEGRADATION" -and [string]$RecommendedAction -match "DB|DATABASE|RESTART|INVESTIGATE|DIAGNOSTIC"
                )
            )
        }
        "GeneralDegradation" {
            return (
                ([string]$IncidentType -eq "GENERAL_DEGRADATION" -and [string]$RecommendedAction -match "INVESTIGATE|DIAGNOSTIC|LATENCY|RESTART|TRAFFIC") -or (
                    [string]$IncidentType -eq "APPLICATION_ERROR_STORM" -and [string]$RecommendedAction -match "RESTART|INVESTIGATE|TRAFFIC|ROLLBACK"
                )
            )
        }
        default {
            return [string]$RecommendedAction -match "RESTART"
        }
    }
}

function Get-PreferredControlPodName {
    $pods = (kubectl --kubeconfig $KubeconfigPath -n $Namespace get pods -l "app=$App" -o json | ConvertFrom-Json).items
    $readyPods = @($pods | Where-Object { Test-PodReadyStatus -Pod $_ })
    $excludedPodName = ""
    if (Get-Variable -Name injectPod -Scope Script -ErrorAction SilentlyContinue) {
        $excludedPodName = [string]$script:injectPod
    }

    if (-not [string]::IsNullOrWhiteSpace($script:controlPod)) {
        $existingControl = @($readyPods | Where-Object { $_.metadata.name -eq $script:controlPod })
        if ($existingControl.Count -gt 0 -and $script:controlPod -ne $excludedPodName) {
            return $script:controlPod
        }
    }

    if ($FaultStrategy -eq "VmDown") {
        $preferred = @($readyPods | Where-Object {
            $_.spec.nodeName -ne $VmDownTargetNode -and $_.metadata.name -ne $excludedPodName
        })
        if ($preferred.Count -gt 0) {
            return $preferred[0].metadata.name
        }
    }

    $distinctControlCandidates = @($readyPods | Where-Object { $_.metadata.name -ne $excludedPodName })
    if ($distinctControlCandidates.Count -gt 0) {
        return $distinctControlCandidates[0].metadata.name
    }

    if ($readyPods.Count -gt 0 -and [string]::IsNullOrWhiteSpace($excludedPodName)) {
        return $readyPods[0].metadata.name
    }

    throw "No ready backend pod is available for the control port-forward."
}

function Start-ControlPortForward {
    $script:controlPod = Get-PreferredControlPodName

    if ($script:controlPf -and -not $script:controlPf.HasExited) {
        Stop-Process -Id $script:controlPf.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }

    $script:controlPf = Start-Process -FilePath "kubectl" -ArgumentList @(
        "--kubeconfig", $KubeconfigPath,
        "-n", $Namespace,
        "port-forward",
        "pod/$script:controlPod",
        "$controlPort`:8080",
        "--address", "127.0.0.1"
    ) -PassThru

    $null = Wait-HttpReady -Uri "http://127.0.0.1:$controlPort/actuator/health" -Attempts 30 -SleepSeconds 2
}

function Restart-ControlPortForward {
    Start-ControlPortForward
}

function Wait-PodReady {
    param(
        [Parameter(Mandatory = $true)][string]$PodName,
        [int]$TimeoutSeconds = 180
    )

    & kubectl --kubeconfig $KubeconfigPath -n $Namespace wait --for=condition=Ready "pod/$PodName" "--timeout=${TimeoutSeconds}s" | Out-Null
}

function Start-InjectPortForward {
    if ($script:injectPf -and -not $script:injectPf.HasExited) {
        Stop-Process -Id $script:injectPf.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }

    $script:injectPf = Start-Process -FilePath "kubectl" -ArgumentList @(
        "--kubeconfig", $KubeconfigPath,
        "-n", $Namespace,
        "port-forward",
        "pod/$script:injectPod",
        "$injectPort`:8080",
        "--address", "127.0.0.1"
    ) -PassThru

    $null = Wait-HttpReady -Uri "http://127.0.0.1:$injectPort/actuator/health" -Attempts 30 -SleepSeconds 2
}

function Restart-InjectPortForward {
    Wait-PodReady -PodName $script:injectPod
    Start-InjectPortForward
}

function Invoke-FaultToggle {
    param(
        [Parameter(Mandatory = $true)][string]$Scenario,
        [Parameter(Mandatory = $true)][bool]$Enabled
    )

    return Invoke-BackendJson -Uri "http://127.0.0.1:$injectPort/api/test/faults" -Method Post -Body @{
        scenario = $Scenario
        enabled = $Enabled
    }
}

function Invoke-TrafficBurst {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$Requests = 20,
        [int]$DelayMs = 200
    )

    for ($i = 0; $i -lt $Requests; $i++) {
        try {
            Invoke-RestMethod -Uri $Uri -Headers $headers -Method Get -TimeoutSec 15 | Out-Null
        } catch {
            # Fault scenarios intentionally generate failures.
        }

        if ($DelayMs -gt 0) {
            Start-Sleep -Milliseconds $DelayMs
        }
    }
}

function Get-ScenarioProfile {
    param(
        [Parameter(Mandatory = $true)][string]$Strategy,
        [Parameter(Mandatory = $true)][string]$RequestedIntensity
    )

    $profile = @{
        intensity = $RequestedIntensity
        cpuStressSeconds = $CpuStressSeconds
        memoryStressMb = $MemoryStressMb
        memoryHoldSeconds = $MemoryHoldSeconds
        availabilityHoldSeconds = $AvailabilityDropHoldSeconds
        restartLoopCycles = $RestartLoopCycles
        trafficBurstRequests = $TrafficBurstRequests
        trafficBurstDelayMs = $TrafficBurstDelayMs
    }

    switch ($RequestedIntensity) {
        "Low" {
            $profile["cpuStressSeconds"] = [Math]::Max(120, [int]($CpuStressSeconds * 0.6))
            $profile["memoryStressMb"] = [Math]::Max(640, [int]($MemoryStressMb * 0.7))
            $profile["memoryHoldSeconds"] = [Math]::Max(180, [int]($MemoryHoldSeconds * 0.75))
            $profile["availabilityHoldSeconds"] = [Math]::Max(90, [int]($AvailabilityDropHoldSeconds * 0.75))
            $profile["restartLoopCycles"] = [Math]::Max(2, $RestartLoopCycles)
            $profile["trafficBurstRequests"] = [Math]::Max(18, [int]($TrafficBurstRequests * 0.6))
            $profile["trafficBurstDelayMs"] = [Math]::Max(350, $TrafficBurstDelayMs)
        }
        "High" {
            $profile["cpuStressSeconds"] = [Math]::Max($CpuStressSeconds, 360)
            $profile["memoryStressMb"] = [Math]::Max($MemoryStressMb, 1280)
            $profile["memoryHoldSeconds"] = [Math]::Max($MemoryHoldSeconds, 360)
            $profile["availabilityHoldSeconds"] = [Math]::Max($AvailabilityDropHoldSeconds, 210)
            $profile["restartLoopCycles"] = [Math]::Max($RestartLoopCycles, 3)
            $profile["trafficBurstRequests"] = [Math]::Max($TrafficBurstRequests, 48)
            $profile["trafficBurstDelayMs"] = [Math]::Min($TrafficBurstDelayMs, 120)
        }
    }

    if ($Strategy -eq "GeneralDegradation") {
        $profile["trafficBurstDelayMs"] = [Math]::Max($profile["trafficBurstDelayMs"], 150)
    }
    if ($Strategy -eq "ApplicationErrorStorm") {
        $profile["trafficBurstRequests"] = [Math]::Max($profile["trafficBurstRequests"], 120)
        $profile["trafficBurstDelayMs"] = [Math]::Min($profile["trafficBurstDelayMs"], 100)
    }

    return $profile
}

try {
    $scenarioProfile = Get-ScenarioProfile -Strategy $FaultStrategy -RequestedIntensity $Intensity
    if (-not (Wait-AppReplicasReady -MinimumReadyPods 2 -TimeoutSeconds 300 -PreferredNodeForRepair $VmDownTargetNode)) {
        throw "Backend deployment is not stable enough to start the final test."
    }

    $podsJson = kubectl --kubeconfig $KubeconfigPath -n $Namespace get pods -l "app=$App" --sort-by=.metadata.creationTimestamp -o json
    $pods = $podsJson | ConvertFrom-Json
    $readyPods = @($pods.items | Where-Object { Test-PodReadyStatus -Pod $_ })
    if ($readyPods.Count -lt 2) {
        throw "Need at least two $App pods to run the final test."
    }

    if ($FaultStrategy -eq "VmDown") {
        $controlCandidate = @($readyPods | Where-Object { $_.spec.nodeName -ne $VmDownTargetNode } | Select-Object -First 1)
        if ($controlCandidate.Count -eq 0) {
            throw "No surviving backend pod found outside target node $VmDownTargetNode."
        }

        $injectCandidate = @($readyPods | Where-Object { $_.spec.nodeName -eq $VmDownTargetNode } | Select-Object -First 1)
        if ($injectCandidate.Count -eq 0) {
            $injectCandidate = @($readyPods | Select-Object -First 1)
        }

        $injectPod = $injectCandidate[0].metadata.name
        $controlPod = $controlCandidate[0].metadata.name
    } else {
        $injectPod = $readyPods[0].metadata.name
        $controlPod = $readyPods[1].metadata.name
    }

    $podsJson | Set-Content -Path (Join-Path $outputDir "00-backend-pods-before.json")
    "injectPod=$injectPod`r`ncontrolPod=$controlPod" | Set-Content -Path (Join-Path $outputDir "01-target-pods.txt")

    Start-ControlPortForward
    Start-InjectPortForward

    $health = Wait-HttpReady -Uri "http://127.0.0.1:$controlPort/actuator/health"
    Save-Json -Object $health -Path (Join-Path $outputDir "02-control-health.json")

    if ($FaultStrategy -eq "VmDown") {
        Invoke-WorkerOutageControl -Action "status"
        Invoke-WorkerOutageControl -Action "stop"
        $workerOutageStarted = $true
        if (-not (Wait-NodeCondition -NodeName $VmDownTargetNode -DesiredState "NotReady" -Attempts 36 -SleepSeconds 5)) {
            throw "Node $VmDownTargetNode did not become NotReady after simulated outage."
        }

        kubectl --kubeconfig $KubeconfigPath get node $VmDownTargetNode -o wide | Set-Content -Path (Join-Path $outputDir "03-node-after-stop.txt")
        Restart-ControlPortForward
        $controlHealthAfterOutage = Wait-HttpReady -Uri "http://127.0.0.1:$controlPort/actuator/health" -Attempts 20 -SleepSeconds 3
        Save-Json -Object $controlHealthAfterOutage -Path (Join-Path $outputDir "03-control-health-after-stop.json")
    } else {
        $reset = Invoke-BackendJson -Uri "http://127.0.0.1:$injectPort/api/test/faults/reset" -Method Post
        Save-Json -Object $reset -Path (Join-Path $outputDir "03-faults-reset.json")

        switch ($FaultStrategy) {
            "AvailabilityDrop" {
                $enabled = Invoke-FaultToggle -Scenario "READINESS_FAILURE" -Enabled $true
                Save-Json -Object $enabled -Path (Join-Path $outputDir "04-readiness-enabled.json")
                try {
                    $injectPodAfterReadinessFailure = Wait-PodReadyState -PodName $injectPod -DesiredReady $false -Attempts 24 -SleepSeconds 5
                    Save-Json -Object $injectPodAfterReadinessFailure -Path (Join-Path $outputDir "04a-inject-pod-not-ready.json")
                } catch {
                    $_.Exception.Message | Set-Content -Path (Join-Path $outputDir "04a-inject-pod-not-ready-timeout.txt")
                }
                $backendEndpointsAfterReadinessFailure = kubectl --kubeconfig $KubeconfigPath -n $Namespace get endpoints backend -o json | ConvertFrom-Json
                Save-Json -Object $backendEndpointsAfterReadinessFailure -Path (Join-Path $outputDir "04b-backend-endpoints-after-readiness.json")
                Invoke-TrafficBurst -Uri "http://127.0.0.1:$injectPort/api/test/health-readiness" -Requests ([Math]::Max(12, [int]($scenarioProfile['trafficBurstRequests'] / 4))) -DelayMs ([Math]::Max(250, $scenarioProfile['trafficBurstDelayMs']))
                Start-Sleep -Seconds 70
            }
            "CpuStress" {
                $enabled = Invoke-FaultToggle -Scenario "CPU_STRESS" -Enabled $true
                Save-Json -Object $enabled -Path (Join-Path $outputDir "04-cpu-enabled.json")
                $faultStart = Invoke-BackendJson -Uri "http://127.0.0.1:$injectPort/api/test/cpu?seconds=$($scenarioProfile['cpuStressSeconds'])"
                Save-Json -Object $faultStart -Path (Join-Path $outputDir "05-cpu-stress-start.json")
            }
            "MemoryStress" {
                $enabled = Invoke-FaultToggle -Scenario "MEMORY_STRESS" -Enabled $true
                Save-Json -Object $enabled -Path (Join-Path $outputDir "04-memory-enabled.json")
                $faultStart = Invoke-BackendJson -Uri "http://127.0.0.1:$injectPort/api/test/memory?mb=$($scenarioProfile['memoryStressMb'])&holdSeconds=$($scenarioProfile['memoryHoldSeconds'])"
                Save-Json -Object $faultStart -Path (Join-Path $outputDir "05-memory-stress-start.json")
            }
            "RestartLoop" {
                $enabled = Invoke-FaultToggle -Scenario "CRASH_ON_DEMAND" -Enabled $true
                Save-Json -Object $enabled -Path (Join-Path $outputDir "04-restart-loop-enabled.json")
                for ($cycle = 1; $cycle -le $scenarioProfile['restartLoopCycles']; $cycle++) {
                    $faultStart = Invoke-BackendJson -Uri "http://127.0.0.1:$injectPort/api/test/crash"
                    Save-Json -Object $faultStart -Path (Join-Path $outputDir ("05-restart-loop-cycle-{0}.json" -f $cycle))
                    Start-Sleep -Seconds 15
                    Restart-InjectPortForward
                }
            }
            "ApplicationErrorStorm" {
                Invoke-TrafficBurst -Uri "http://127.0.0.1:$injectPort/api/test/error-storm?message=simulated-app-error-storm" -Requests $scenarioProfile['trafficBurstRequests'] -DelayMs $scenarioProfile['trafficBurstDelayMs']
            }
            "DatabaseConnectivity" {
                $enabled = Invoke-FaultToggle -Scenario "DB_POOL_EXHAUSTION" -Enabled $true
                Save-Json -Object $enabled -Path (Join-Path $outputDir "04-db-pool-enabled.json")
                $faultStart = Invoke-BackendJson -Uri "http://127.0.0.1:$injectPort/api/test/db-pool-exhaust/start?connections=5" -Method Post
                Save-Json -Object $faultStart -Path (Join-Path $outputDir "05-db-pool-start.json")
                Invoke-TrafficBurst -Uri "http://127.0.0.1:$injectPort/api/test/db" -Requests ([Math]::Max(6, [int]($scenarioProfile['trafficBurstRequests'] / 3))) -DelayMs ([Math]::Max(200, $scenarioProfile['trafficBurstDelayMs']))
            }
            "GeneralDegradation" {
                $enabled = Invoke-FaultToggle -Scenario "HIGH_LATENCY" -Enabled $true
                Save-Json -Object $enabled -Path (Join-Path $outputDir "04-latency-enabled.json")
                # Use a quiet probe endpoint so latency pressure does not also
                # manufacture synthetic error-storm log signatures.
                Invoke-TrafficBurst -Uri "http://127.0.0.1:$injectPort/api/test/cpu/status" -Requests $scenarioProfile['trafficBurstRequests'] -DelayMs $scenarioProfile['trafficBurstDelayMs']
            }
        }
    }

    $effectiveWindowMinutes = switch ($FaultStrategy) {
        "ApplicationErrorStorm" { [Math]::Min([Math]::Max($WindowMinutes, 1), 2) }
        "CpuStress" { [Math]::Min([Math]::Max($WindowMinutes, 2), 3) }
        "MemoryStress" { [Math]::Min([Math]::Max($WindowMinutes, 2), 3) }
        "RestartLoop" { [Math]::Min([Math]::Max($WindowMinutes, 2), 3) }
        "AvailabilityDrop" { [Math]::Min([Math]::Max($WindowMinutes, 2), 3) }
        "GeneralDegradation" { [Math]::Min([Math]::Max($WindowMinutes, 2), 3) }
        default { $WindowMinutes }
    }

    $predictionRequest = @{
        namespace = $Namespace
        app = $App
        windowMinutes = $effectiveWindowMinutes
    }

    $eligiblePrediction = $null
    $eligibleConfidenceThreshold = Get-EligiblePredictionThreshold -Strategy $FaultStrategy
    $effectiveFirstPredictionDelaySeconds = switch ($FaultStrategy) {
        "CpuStress" { [Math]::Max($FirstPredictionDelaySeconds, 75) }
        "MemoryStress" { [Math]::Max($FirstPredictionDelaySeconds, 60) }
        "AvailabilityDrop" { [Math]::Max($FirstPredictionDelaySeconds, 45) }
        "VmDown" { [Math]::Max($FirstPredictionDelaySeconds, 35) }
        "RestartLoop" { [Math]::Max($FirstPredictionDelaySeconds, 40) }
        "ApplicationErrorStorm" { [Math]::Max($FirstPredictionDelaySeconds, 20) }
        "DatabaseConnectivity" { [Math]::Max($FirstPredictionDelaySeconds, 25) }
        "GeneralDegradation" { [Math]::Max($FirstPredictionDelaySeconds, 30) }
        default { $FirstPredictionDelaySeconds }
    }
    $effectivePredictionAttempts = switch ($FaultStrategy) {
        "CpuStress" { [Math]::Max($PredictionAttempts, 10) }
        "MemoryStress" { [Math]::Max($PredictionAttempts, 10) }
        "VmDown" { [Math]::Max($PredictionAttempts, 8) }
        "AvailabilityDrop" { [Math]::Max($PredictionAttempts, 10) }
        "RestartLoop" { [Math]::Max($PredictionAttempts, 10) }
        "ApplicationErrorStorm" { [Math]::Max($PredictionAttempts, 7) }
        "DatabaseConnectivity" { [Math]::Max($PredictionAttempts, 7) }
        "GeneralDegradation" { [Math]::Max($PredictionAttempts, 7) }
        default { $PredictionAttempts }
    }
    $effectiveMonitorAttempts = switch ($FaultStrategy) {
        "CpuStress" { [Math]::Max($MonitorAttempts, 30) }
        default { $MonitorAttempts }
    }

    for ($attempt = 1; $attempt -le $effectivePredictionAttempts; $attempt++) {
        if ($attempt -eq 1) {
            Start-Sleep -Seconds $effectiveFirstPredictionDelaySeconds
        } else {
            Start-Sleep -Seconds $PredictionRetryDelaySeconds
        }

        $readonlyResponse = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/ai/predict" -Method Post -Body (@{
            namespace = $Namespace
            app = $App
            windowMinutes = $effectiveWindowMinutes
            createIncident = $false
        })
        Save-Json -Object $readonlyResponse -Path (Join-Path $outputDir ("10-readonly-predict-attempt-{0}.json" -f $attempt))

        $prediction = if ($readonlyResponse.PSObject.Properties.Name -contains "prediction") {
            $readonlyResponse.prediction
        } else {
            $readonlyResponse
        }

        $predIncident = Get-FirstValue -Object $prediction -Names @("incident")
        $predIncidentType = Get-FirstValue -Object $prediction -Names @("incidentType", "incident_type")
        $predConfidence = Get-FirstValue -Object $prediction -Names @("confidence")
        $predAction = Get-FirstValue -Object $prediction -Names @("recommendedAction", "recommended_action")

        if (Test-PredictionMatchesStrategy `
            -Strategy $FaultStrategy `
            -Incident $predIncident `
            -IncidentType $predIncidentType `
            -RecommendedAction $predAction `
            -Confidence $predConfidence `
            -Threshold $eligibleConfidenceThreshold) {
            $eligiblePrediction = $prediction
            break
        }
    }

    if ($null -eq $eligiblePrediction) {
        throw "$FaultStrategy never produced an auto-remediation-eligible prediction."
    }

    $createResponse = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/ai/predict" -Method Post -Body (@{
        namespace = $Namespace
        app = $App
        windowMinutes = $effectiveWindowMinutes
        createIncident = $true
    })
    Save-Json -Object $createResponse -Path (Join-Path $outputDir "20-create-incident.json")

    $incidentId = $createResponse.incidentId
    $workflowId = $createResponse.workflowId
    if ($null -eq $incidentId -or $null -eq $workflowId) {
        throw "Incident/workflow creation did not return identifiers."
    }

    $result = [ordered]@{
        outputDir = $outputDir
        faultStrategy = $FaultStrategy
        intensity = $Intensity
        injectPod = $injectPod
        controlPod = $controlPod
        incidentId = $incidentId
        workflowId = $workflowId
        predictionType = Get-FirstValue -Object $eligiblePrediction -Names @("incidentType", "incident_type")
        predictionAction = Get-FirstValue -Object $eligiblePrediction -Names @("recommendedAction", "recommended_action")
        predictionConfidence = Get-FirstValue -Object $eligiblePrediction -Names @("confidence")
        executionMode = Get-FirstValue -Object $eligiblePrediction -Names @("executionMode", "execution_mode")
        recommendationOnlyReason = Get-FirstValue -Object $eligiblePrediction -Names @("recommendationOnlyReason", "recommendation_only_reason")
        autoApprovedTaskIds = @()
        finalStatus = "UNKNOWN"
    }

    $initialPendingTasks = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/tasks/pending" -RetryCount 3 -RetryDelaySeconds 2
    $initialApprovedTaskIds = @(Approve-IncidentPendingTasks -PendingTasks $initialPendingTasks -IncidentId $incidentId -PollIndex 0 -OutputDir $outputDir)
    if (@($initialApprovedTaskIds).Count -gt 0) {
        $result.autoApprovedTaskIds = @($autoApprovedTaskIds)
    }

    if ($result.executionMode -eq "RECOMMEND_ONLY") {
        $workflowList = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/workflows" -RetryCount 6 -RetryDelaySeconds 5
        $incidentPage = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/incidents" -RetryCount 6 -RetryDelaySeconds 5
        $actions = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/actions/incident/$incidentId" -RetryCount 6 -RetryDelaySeconds 5

        Save-Json -Object $workflowList -Path (Join-Path $outputDir "35-workflows-final.json")
        Save-Json -Object $incidentPage -Path (Join-Path $outputDir "36-incidents-final.json")
        Save-Json -Object $actions -Path (Join-Path $outputDir "37-actions-final.json")

        $workflowItems = Get-Collection -Object $workflowList
        $incidentItems = Get-Collection -Object $incidentPage -CandidateProperties @("content", "value", "items")
        $workflow = Select-FirstMatch -Collection $workflowItems -Predicate {
            [string](Get-FirstValue -Object $_ -Names @("id")) -eq [string]$workflowId
        }
        $incident = Select-FirstMatch -Collection $incidentItems -Predicate {
            [string](Get-FirstValue -Object $_ -Names @("id")) -eq [string]$incidentId
        }

        if ($null -ne $workflow) {
            $result.workflowStatus = Get-FirstValue -Object $workflow -Names @("status")
            $result.orchestrationState = Get-FirstValue -Object $workflow -Names @("orchestrationState", "orchestration_state")
        }
        if ($null -ne $incident) {
            $result.incidentStatus = Get-FirstValue -Object $incident -Names @("status")
        }

        if ($result.orchestrationState -eq "MANUAL_FOLLOW_UP") {
            $result.finalStatus = "SUCCESS"
        }
    } else {
        for ($poll = 1; $poll -le $effectiveMonitorAttempts; $poll++) {
            Start-Sleep -Seconds $MonitorPollSeconds

            $workflowList = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/workflows" -RetryCount 6 -RetryDelaySeconds 5
            $incidentPage = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/incidents" -RetryCount 6 -RetryDelaySeconds 5
            $actions = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/actions/incident/$incidentId" -RetryCount 6 -RetryDelaySeconds 5
            $pendingTasks = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/tasks/pending" -RetryCount 6 -RetryDelaySeconds 5
            $podsSnapshot = kubectl --kubeconfig $KubeconfigPath -n $Namespace get pods -l "app=$App" -o wide

            $workflowItems = Get-Collection -Object $workflowList
            $incidentItems = Get-Collection -Object $incidentPage -CandidateProperties @("content", "value", "items")

            $workflow = Select-FirstMatch -Collection $workflowItems -Predicate {
                [string](Get-FirstValue -Object $_ -Names @("id")) -eq [string]$workflowId
            }
            $incident = Select-FirstMatch -Collection $incidentItems -Predicate {
                [string](Get-FirstValue -Object $_ -Names @("id")) -eq [string]$incidentId
            }

            Save-Json -Object $workflowList -Path (Join-Path $outputDir ("30-workflows-poll-{0}.json" -f $poll))
            Save-Json -Object $incidentPage -Path (Join-Path $outputDir ("31-incidents-poll-{0}.json" -f $poll))
            Save-Json -Object $actions -Path (Join-Path $outputDir ("32-actions-poll-{0}.json" -f $poll))
            Save-Json -Object $pendingTasks -Path (Join-Path $outputDir ("33-pending-tasks-poll-{0}.json" -f $poll))
            $podsSnapshot | Set-Content -Path (Join-Path $outputDir ("34-backend-pods-poll-{0}.txt" -f $poll))

            $approvedTaskIds = @(Approve-IncidentPendingTasks -PendingTasks $pendingTasks -IncidentId $incidentId -PollIndex $poll -OutputDir $outputDir)
            if (@($approvedTaskIds).Count -gt 0) {
                $result.autoApprovedTaskIds = @($autoApprovedTaskIds)
                continue
            }

            if ($null -eq $workflow -or $null -eq $incident) {
                continue
            }

            $workflowStatus = Get-FirstValue -Object $workflow -Names @("status")
            $orchestrationState = Get-FirstValue -Object $workflow -Names @("orchestrationState", "orchestration_state")
            $incidentStatus = Get-FirstValue -Object $incident -Names @("status")

            if ($workflowStatus -eq "COMPLETED" -and $orchestrationState -eq "RESOLVED") {
                $result.finalStatus = "SUCCESS"
                $result.workflowStatus = $workflowStatus
                $result.orchestrationState = $orchestrationState
                $result.incidentStatus = $incidentStatus
                break
            }

            if ($FaultStrategy -eq "VmDown" -and $orchestrationState -in @("WAITING_FOR_APPROVAL", "MANUAL_FOLLOW_UP")) {
                $result.finalStatus = "SUCCESS"
                $result.workflowStatus = $workflowStatus
                $result.orchestrationState = $orchestrationState
                $result.incidentStatus = $incidentStatus
                break
            }

            if ($orchestrationState -eq "WAITING_FOR_APPROVAL") {
                $result.finalStatus = "NEEDS_APPROVAL"
                $result.workflowStatus = $workflowStatus
                $result.orchestrationState = $orchestrationState
                $result.incidentStatus = $incidentStatus
                break
            }

            if ($workflowStatus -eq "CANCELLED") {
                $result.finalStatus = "NEEDS_APPROVAL"
                $result.workflowStatus = $workflowStatus
                $result.orchestrationState = $orchestrationState
                $result.incidentStatus = $incidentStatus
                break
            }
        }
    }

    if ($result.finalStatus -eq "UNKNOWN") {
        # One final reconciliation avoids false TIMEOUT results when the backend
        # resolves the workflow at the edge of the polling window.
        $workflowList = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/workflows" -RetryCount 6 -RetryDelaySeconds 5
        $incidentPage = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/incidents" -RetryCount 6 -RetryDelaySeconds 5
        $actions = Invoke-BackendJson -Uri "http://127.0.0.1:$controlPort/api/actions/incident/$incidentId" -RetryCount 6 -RetryDelaySeconds 5

        Save-Json -Object $workflowList -Path (Join-Path $outputDir "35-workflows-final.json")
        Save-Json -Object $incidentPage -Path (Join-Path $outputDir "36-incidents-final.json")
        Save-Json -Object $actions -Path (Join-Path $outputDir "37-actions-final.json")

        $workflowItems = Get-Collection -Object $workflowList
        $incidentItems = Get-Collection -Object $incidentPage -CandidateProperties @("content", "value", "items")
        $workflow = Select-FirstMatch -Collection $workflowItems -Predicate {
            [string](Get-FirstValue -Object $_ -Names @("id")) -eq [string]$workflowId
        }
        $incident = Select-FirstMatch -Collection $incidentItems -Predicate {
            [string](Get-FirstValue -Object $_ -Names @("id")) -eq [string]$incidentId
        }

        if ($null -ne $workflow) {
            $workflowStatus = Get-FirstValue -Object $workflow -Names @("status")
            $orchestrationState = Get-FirstValue -Object $workflow -Names @("orchestrationState", "orchestration_state")
            $incidentStatus = if ($null -ne $incident) {
                Get-FirstValue -Object $incident -Names @("status")
            } else {
                $null
            }

            if ($workflowStatus -eq "COMPLETED" -and $orchestrationState -eq "RESOLVED") {
                $result.finalStatus = "SUCCESS"
                $result.workflowStatus = $workflowStatus
                $result.orchestrationState = $orchestrationState
                $result.incidentStatus = $incidentStatus
            } elseif ($result.executionMode -eq "RECOMMEND_ONLY" -and $orchestrationState -eq "MANUAL_FOLLOW_UP") {
                $result.finalStatus = "SUCCESS"
                $result.workflowStatus = $workflowStatus
                $result.orchestrationState = $orchestrationState
                $result.incidentStatus = $incidentStatus
            } elseif ($FaultStrategy -eq "VmDown" -and $orchestrationState -in @("WAITING_FOR_APPROVAL", "MANUAL_FOLLOW_UP")) {
                $result.finalStatus = "SUCCESS"
                $result.workflowStatus = $workflowStatus
                $result.orchestrationState = $orchestrationState
                $result.incidentStatus = $incidentStatus
            }
        }
    }

    if ($result.finalStatus -eq "UNKNOWN") {
        $result.finalStatus = "TIMEOUT"
    }

    Save-Json -Object $result -Path (Join-Path $outputDir "99-final-result.json")

    Write-Host "OUTPUT_DIR=$outputDir"
    Write-Host "INJECT_POD=$injectPod"
    Write-Host "CONTROL_POD=$controlPod"
    Write-Host "INCIDENT_ID=$incidentId"
    Write-Host "WORKFLOW_ID=$workflowId"
    Write-Host "FINAL_STATUS=$($result.finalStatus)"
    if (@($result.autoApprovedTaskIds).Count -gt 0) {
        Write-Host "AUTO_APPROVED_TASK_IDS=$([string]::Join(',', @($result.autoApprovedTaskIds)))"
    }
    Write-Host "PREDICTION_TYPE=$($result.predictionType)"
    Write-Host "PREDICTION_ACTION=$($result.predictionAction)"
    Write-Host "PREDICTION_CONFIDENCE=$($result.predictionConfidence)"
    Write-Host "EXECUTION_MODE=$($result.executionMode)"

    if ($result.finalStatus -ne "SUCCESS") {
        exit 1
    }
}
finally {
    try {
        if ($injectPf -and -not $injectPf.HasExited) {
            Invoke-BackendJson -Uri "http://127.0.0.1:$injectPort/api/test/faults/reset" -Method Post -RetryCount 1 -RetryDelaySeconds 1 | Out-Null
            try {
                Invoke-BackendJson -Uri "http://127.0.0.1:$injectPort/api/test/db-pool-exhaust/stop" -Method Post -RetryCount 1 -RetryDelaySeconds 1 | Out-Null
            } catch {
                # ignore cleanup errors if DB pool exhaustion was never started
            }
        }
    } catch {
        Write-Warning "Failed to reset backend fault injection state: $($_.Exception.Message)"
    }
    if ($workerOutageStarted) {
        try {
            Invoke-WorkerOutageControl -Action "start"
            $null = Wait-NodeCondition -NodeName $VmDownTargetNode -DesiredState "Ready" -Attempts 36 -SleepSeconds 5
            kubectl --kubeconfig $KubeconfigPath get node $VmDownTargetNode -o wide | Set-Content -Path (Join-Path $outputDir "98-node-after-recovery.txt")
        } catch {
            Write-Warning "Failed to restore simulated worker outage on ${VmDownTargetNode}: $($_.Exception.Message)"
        }
    }
    try {
        $preferredRepairNode = if ($workerOutageStarted) { $VmDownTargetNode } else { "" }
        if (-not (Wait-AppReplicasReady -MinimumReadyPods 2 -TimeoutSeconds 360 -PreferredNodeForRepair $preferredRepairNode)) {
            Write-Warning "Backend deployment did not return to a stable 2-pod state before script exit."
        }
    } catch {
        Write-Warning "Failed to stabilize backend deployment before exit: $($_.Exception.Message)"
    }
    if ($injectPf -and -not $injectPf.HasExited) {
        Stop-Process -Id $injectPf.Id -Force
    }
    if ($controlPf -and -not $controlPf.HasExited) {
        Stop-Process -Id $controlPf.Id -Force
    }
}
