param(
    [long]$WorkflowId = 0,
    [long]$IncidentId = 0,
    [string]$KubeconfigPath = "",
    [string]$Namespace = "pfe",
    [int]$ControlPort = 18180
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
if ([string]::IsNullOrWhiteSpace($KubeconfigPath)) {
    $KubeconfigPath = Join-Path $env:TEMP "pfe-kubeconfig-master"
}

$timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$outputDir = Join-Path $repoRoot "investigation_outputs\\$timestamp-check-remediation-status"
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
        [int]$TimeoutSec = 60
    )

    return Invoke-RestMethod -Uri $Uri -Headers $headers -Method Get -TimeoutSec $TimeoutSec
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
    $controlPf = Start-Process -FilePath "kubectl" -ArgumentList @(
        "--kubeconfig", $KubeconfigPath,
        "-n", $Namespace,
        "port-forward",
        "svc/backend",
        "$ControlPort`:8080",
        "--address", "127.0.0.1"
    ) -PassThru

    $health = Wait-HttpReady -Uri "http://127.0.0.1:$ControlPort/actuator/health"
    Save-Json -Object $health -Path (Join-Path $outputDir "00-health.json")

    $workflowList = Invoke-BackendJson -Uri "http://127.0.0.1:$ControlPort/api/workflows"
    $incidentPage = Invoke-BackendJson -Uri "http://127.0.0.1:$ControlPort/api/incidents?page=0&size=200"
    $actions = if ($IncidentId -gt 0) {
        Invoke-BackendJson -Uri "http://127.0.0.1:$ControlPort/api/actions/incident/$IncidentId"
    } else {
        @()
    }
    $pendingTasks = Invoke-BackendJson -Uri "http://127.0.0.1:$ControlPort/api/tasks/pending"

    Save-Json -Object $workflowList -Path (Join-Path $outputDir "10-workflows.json")
    Save-Json -Object $incidentPage -Path (Join-Path $outputDir "11-incidents.json")
    Save-Json -Object $actions -Path (Join-Path $outputDir "12-actions.json")
    Save-Json -Object $pendingTasks -Path (Join-Path $outputDir "13-pending-tasks.json")

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

    $summary = [ordered]@{
        outputDir = $outputDir
        workflowId = $WorkflowId
        workflowStatus = if ($workflow) { Get-FirstValue -Object $workflow -Names @("status") } else { $null }
        orchestrationState = if ($workflow) { Get-FirstValue -Object $workflow -Names @("orchestrationState", "orchestration_state") } else { $null }
        incidentId = $IncidentId
        incidentStatus = if ($incident) { Get-FirstValue -Object $incident -Names @("status") } else { $null }
        pendingTaskCount = @($pendingTasks).Count
        latestActionStatus = if (@($actions).Count -gt 0) { Get-FirstValue -Object (@($actions)[-1]) -Names @("status") } else { $null }
        latestActionType = if (@($actions).Count -gt 0) { Get-FirstValue -Object (@($actions)[-1]) -Names @("type") } else { $null }
    }

    Save-Json -Object $summary -Path (Join-Path $outputDir "99-summary.json")
    $summary | ConvertTo-Json -Depth 20 | Write-Output
}
finally {
    if ($controlPf -and -not $controlPf.HasExited) {
        Stop-Process -Id $controlPf.Id -Force
    }
}
