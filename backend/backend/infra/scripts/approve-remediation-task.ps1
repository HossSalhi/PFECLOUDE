param(
    [Parameter(Mandatory = $true)]
    [long]$TaskId,
    [long]$WorkflowId = 0,
    [long]$IncidentId = 0,
    [string]$KubeconfigPath = "",
    [string]$Namespace = "pfe",
    [string]$App = "backend",
    [int]$ControlPort = 18180,
    [int]$PollAttempts = 12,
    [int]$PollSeconds = 20
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
if ([string]::IsNullOrWhiteSpace($KubeconfigPath)) {
    $KubeconfigPath = Join-Path $env:TEMP "pfe-kubeconfig-master"
}

$timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$outputDir = Join-Path $repoRoot "investigation_outputs\\$timestamp-approve-remediation-task"
$null = New-Item -ItemType Directory -Force -Path $outputDir

$pair = "operator:operator-change-me"
$token = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
$headers = @{ Authorization = "Basic $token" }
$controlPf = $null

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

function Invoke-BackendJson {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [ValidateSet("Get", "Post")][string]$Method = "Get",
        [int]$TimeoutSec = 60
    )

    return Invoke-RestMethod -Uri $Uri -Headers $headers -Method $Method -TimeoutSec $TimeoutSec
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
            Start-Sleep -Seconds $SleepSeconds
        }
    }

    throw "Timed out waiting for $Uri"
}

try {
    $podsJson = kubectl --kubeconfig $KubeconfigPath -n $Namespace get pods -l "app=$App" --sort-by=.metadata.creationTimestamp -o json
    $pods = $podsJson | ConvertFrom-Json
    if ($pods.items.Count -lt 1) {
        throw "No $App pod found in namespace $Namespace."
    }

    $controlPod = $pods.items[-1].metadata.name
    $controlPf = Start-Process -FilePath "kubectl" -ArgumentList @(
        "--kubeconfig", $KubeconfigPath,
        "-n", $Namespace,
        "port-forward",
        "pod/$controlPod",
        "$ControlPort`:8080",
        "--address", "127.0.0.1"
    ) -PassThru

    $health = Wait-HttpReady -Uri "http://127.0.0.1:$ControlPort/actuator/health"
    Save-Json -Object $health -Path (Join-Path $outputDir "00-health.json")

    $approveResponse = Invoke-BackendJson -Uri "http://127.0.0.1:$ControlPort/api/tasks/$TaskId/approve" -Method Post
    Save-Json -Object $approveResponse -Path (Join-Path $outputDir "01-approve-response.json")

    $result = [ordered]@{
        outputDir = $outputDir
        controlPod = $controlPod
        taskId = $TaskId
        workflowId = $WorkflowId
        incidentId = $IncidentId
        finalStatus = "UNKNOWN"
    }

    for ($poll = 1; $poll -le $PollAttempts; $poll++) {
        Start-Sleep -Seconds $PollSeconds

        $workflowList = Invoke-BackendJson -Uri "http://127.0.0.1:$ControlPort/api/workflows"
        $incidentPage = Invoke-BackendJson -Uri "http://127.0.0.1:$ControlPort/api/incidents?page=0&size=200"
        $actions = if ($IncidentId -gt 0) {
            Invoke-BackendJson -Uri "http://127.0.0.1:$ControlPort/api/actions/incident/$IncidentId"
        } else {
            @()
        }
        $pendingTasks = Invoke-BackendJson -Uri "http://127.0.0.1:$ControlPort/api/tasks/pending"
        $podsSnapshot = kubectl --kubeconfig $KubeconfigPath -n $Namespace get pods -l "app=$App" -o wide

        Save-Json -Object $workflowList -Path (Join-Path $outputDir ("10-workflows-poll-{0}.json" -f $poll))
        Save-Json -Object $incidentPage -Path (Join-Path $outputDir ("11-incidents-poll-{0}.json" -f $poll))
        Save-Json -Object $actions -Path (Join-Path $outputDir ("12-actions-poll-{0}.json" -f $poll))
        Save-Json -Object $pendingTasks -Path (Join-Path $outputDir ("13-pending-tasks-poll-{0}.json" -f $poll))
        $podsSnapshot | Set-Content -Path (Join-Path $outputDir ("14-backend-pods-poll-{0}.txt" -f $poll))

        $workflowItems = Get-Collection -Object $workflowList
        $incidentItems = Get-Collection -Object $incidentPage -CandidateProperties @("content", "value", "items")

        $workflow = if ($WorkflowId -gt 0) {
            @($workflowItems | Where-Object {
                [string](Get-FirstValue -Object $_ -Names @("id")) -eq [string]$WorkflowId
            })[0]
        } else {
            $null
        }
        $incident = if ($IncidentId -gt 0) {
            @($incidentItems | Where-Object {
                [string](Get-FirstValue -Object $_ -Names @("id")) -eq [string]$IncidentId
            })[0]
        } else {
            $null
        }

        $workflowStatus = if ($workflow) { Get-FirstValue -Object $workflow -Names @("status") } else { $null }
        $orchestrationState = if ($workflow) { Get-FirstValue -Object $workflow -Names @("orchestrationState", "orchestration_state") } else { $null }
        $incidentStatus = if ($incident) { Get-FirstValue -Object $incident -Names @("status") } else { $null }

        $result.workflowStatus = $workflowStatus
        $result.orchestrationState = $orchestrationState
        $result.incidentStatus = $incidentStatus

        if ($workflowStatus -eq "COMPLETED" -and $orchestrationState -eq "RESOLVED") {
            $result.finalStatus = "SUCCESS"
            break
        }

        if ($workflowStatus -eq "FAILED" -or $workflowStatus -eq "CANCELLED") {
            $result.finalStatus = "FAILED"
            break
        }

        if ($orchestrationState -eq "WAITING_FOR_APPROVAL") {
            $result.finalStatus = "STILL_WAITING_APPROVAL"
            break
        }
    }

    if ($result.finalStatus -eq "UNKNOWN") {
        $result.finalStatus = "TIMEOUT"
    }

    Save-Json -Object $result -Path (Join-Path $outputDir "99-result.json")

    Write-Host "OUTPUT_DIR=$outputDir"
    Write-Host "TASK_ID=$TaskId"
    Write-Host "WORKFLOW_ID=$WorkflowId"
    Write-Host "INCIDENT_ID=$IncidentId"
    Write-Host "FINAL_STATUS=$($result.finalStatus)"
    Write-Host "WORKFLOW_STATUS=$($result.workflowStatus)"
    Write-Host "ORCHESTRATION_STATE=$($result.orchestrationState)"
    Write-Host "INCIDENT_STATUS=$($result.incidentStatus)"

    if ($result.finalStatus -ne "SUCCESS") {
        exit 1
    }
}
finally {
    if ($controlPf -and -not $controlPf.HasExited) {
        Stop-Process -Id $controlPf.Id -Force
    }
}
