param(
    [string]$Kubeconfig = "investigation_outputs/2026-04-11-manual-steps-automation/kubeconfig-master",
    [int]$ObservationMinutes = 30,
    [string]$OutputRoot = "investigation_outputs"
)

$ErrorActionPreference = "Stop"

$timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$outputDir = Join-Path $OutputRoot "$timestamp-stability-validation"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

function Save-Text {
    param(
        [string]$Name,
        [string[]]$Content
    )

    $path = Join-Path $outputDir $Name
    $Content | Set-Content -Path $path
    return $path
}

function Capture-Kubectl {
    param(
        [string]$Name,
        [string[]]$Arguments
    )

    $content = & kubectl --kubeconfig $Kubeconfig @Arguments
    Save-Text -Name $Name -Content $content | Out-Null
    return $content
}

function Get-EventTimestamp {
    param($Event)

    $candidates = @(
        $Event.lastTimestamp
        $Event.eventTime
        $Event.series.lastObservedTime
        $Event.metadata.creationTimestamp
    )

    foreach ($candidate in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace("$candidate")) {
            return [datetime]$candidate
        }
    }

    return $null
}

function Get-RestartSnapshot {
    $json = & kubectl --kubeconfig $Kubeconfig get pods -A -o json | ConvertFrom-Json
    $result = @{}
    foreach ($item in $json.items) {
        foreach ($container in $item.status.containerStatuses) {
            $key = "$($item.metadata.namespace)/$($item.metadata.name)/$($container.name)"
            $result[$key] = [int]$container.restartCount
        }
    }
    return $result
}

function Get-BackupFreshnessLine {
    try {
        $backupsJson = & kubectl --kubeconfig $Kubeconfig -n pfe get backups.postgresql.cnpg.io -o json 2>$null
        if (-not $backupsJson) {
            return "backupFreshness=no-backups-found"
        }

        $backups = $backupsJson | ConvertFrom-Json
        $completed = @($backups.items | Where-Object { $_.status.phase -eq "completed" })
        if (-not $completed -or $completed.Count -eq 0) {
            return "backupFreshness=no-completed-backups"
        }

        $latest = $completed | Sort-Object { [datetime]$_.status.stoppedAt } -Descending | Select-Object -First 1
        $age = (Get-Date).ToUniversalTime() - ([datetime]$latest.status.stoppedAt)
        return "backupFreshness=latestCompleted=$($latest.metadata.name); ageHours=$([math]::Round($age.TotalHours, 2))"
    }
    catch {
        return "backupFreshness=unavailable ($($_.Exception.Message))"
    }
}

$startTime = Get-Date
$startRestarts = Get-RestartSnapshot

Capture-Kubectl -Name "00-nodes-wide.txt" -Arguments @("get", "nodes", "-o", "wide") | Out-Null
Capture-Kubectl -Name "01-pods-wide.txt" -Arguments @("get", "pods", "-A", "-o", "wide") | Out-Null
Capture-Kubectl -Name "02-events-start.txt" -Arguments @("get", "events", "-A", "--sort-by=.lastTimestamp") | Out-Null
Capture-Kubectl -Name "03-control-plane-restarts.txt" -Arguments @("get", "pods", "-n", "kube-system", "-o", "custom-columns=NAME:.metadata.name,NODE:.spec.nodeName,RESTARTS:.status.containerStatuses[*].restartCount") | Out-Null

Write-Host "Observing cluster stability for $ObservationMinutes minute(s)..."
Start-Sleep -Seconds ($ObservationMinutes * 60)

$endRestarts = Get-RestartSnapshot
$eventsEnd = Capture-Kubectl -Name "04-events-end.txt" -Arguments @("get", "events", "-A", "--sort-by=.lastTimestamp")
$nodesEnd = Capture-Kubectl -Name "05-nodes-end.txt" -Arguments @("get", "nodes", "-o", "wide")

$eventsJson = & kubectl --kubeconfig $Kubeconfig get events -A -o json | ConvertFrom-Json
$windowEvents = foreach ($item in $eventsJson.items) {
    $eventTime = Get-EventTimestamp -Event $item
    if ($null -ne $eventTime -and $eventTime -ge $startTime.ToUniversalTime()) {
        $eventTimeUtc = $eventTime.ToUniversalTime()
        if ($eventTimeUtc -le (Get-Date).ToUniversalTime()) {
            [pscustomobject]@{
                Namespace = $item.metadata.namespace
                LastSeen = $eventTimeUtc.ToString("o")
                Type = $item.type
                Reason = $item.reason
                Object = "$($item.involvedObject.kind)/$($item.involvedObject.name)"
                Message = $item.message
            }
        }
    }
}
$interestingEvents = @($windowEvents | Where-Object {
    $_.Message -match "Failed to update lease|Readiness probe failed|Liveness probe failed|Registered Node|NodeNotReady|NodeReady"
})
$restartDeltaLines = foreach ($key in ($endRestarts.Keys | Sort-Object)) {
    $start = if ($startRestarts.ContainsKey($key)) { $startRestarts[$key] } else { 0 }
    $end = $endRestarts[$key]
    if ($end -ne $start) {
        "$key restartDelta=$($end - $start) (start=$start end=$end)"
    }
}

Save-Text -Name "06-interesting-events.txt" -Content ($interestingEvents | ForEach-Object {
    "$($_.Namespace) $($_.LastSeen) $($_.Type) $($_.Reason) $($_.Object) $($_.Message)"
}) | Out-Null
Save-Text -Name "07-restart-deltas.txt" -Content $restartDeltaLines | Out-Null

$summary = @(
    "startTime=$($startTime.ToString("o"))"
    "endTime=$((Get-Date).ToString("o"))"
    "observationMinutes=$ObservationMinutes"
    "nodeReRegistrationEvents=$((@($interestingEvents | Where-Object { $_.Message -match 'Registered Node' })).Count)"
    "nodeReadinessStormEvents=$((@($interestingEvents | Where-Object { $_.Message -match 'NodeNotReady|NodeReady' })).Count)"
    "leaseErrorEvents=$((@($interestingEvents | Where-Object { $_.Message -match 'Failed to update lease' })).Count)"
    "probeFailureEvents=$((@($interestingEvents | Where-Object { $_.Message -match 'Readiness probe failed|Liveness probe failed' })).Count)"
    "restartDeltas=$($restartDeltaLines.Count)"
    (Get-BackupFreshnessLine)
)

Save-Text -Name "08-summary.txt" -Content $summary | Out-Null
Write-Host "Stability validation written to $outputDir"
