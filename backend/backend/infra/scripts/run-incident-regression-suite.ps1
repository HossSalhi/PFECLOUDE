param(
    [string]$KubeconfigPath = "investigation_outputs/live-kubeconfig-master",
    [string]$Namespace = "pfe",
    [string]$OutputRoot = "investigation_outputs",
    [ValidateSet("Low", "Medium", "High")]
    [string]$Intensity = "Medium",
    [int]$RepeatCount = 1,
    [switch]$SkipStabilize,
    [switch]$SkipBetweenRunStabilize,
    [switch]$SkipPretest,
    [switch]$RepairCnpgReplica,
    [string]$VmPassword = "kube",
    [string[]]$Strategies = @(
        "VmDown",
        "AvailabilityDrop",
        "CpuStress",
        "MemoryStress",
        "RestartLoop",
        "ApplicationErrorStorm",
        "DatabaseConnectivity",
        "GeneralDegradation"
    )
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not [System.IO.Path]::IsPathRooted($KubeconfigPath)) {
    $KubeconfigPath = Join-Path $repoRoot $KubeconfigPath
}
if (-not [System.IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $repoRoot $OutputRoot
}

function Normalize-Strategies {
    param(
        [AllowEmptyCollection()]
        [string[]]$RawStrategies
    )

    $validStrategies = @(
        "VmDown",
        "AvailabilityDrop",
        "CpuStress",
        "MemoryStress",
        "RestartLoop",
        "ApplicationErrorStorm",
        "DatabaseConnectivity",
        "GeneralDegradation"
    )

    $normalized = [System.Collections.Generic.List[string]]::new()
    foreach ($entry in ($RawStrategies | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        foreach ($candidate in ($entry -split ",")) {
            $trimmed = $candidate.Trim()
            if ([string]::IsNullOrWhiteSpace($trimmed)) {
                continue
            }
            if ($validStrategies -notcontains $trimmed) {
                throw "Unknown strategy '$trimmed'. Valid values: $($validStrategies -join ', ')"
            }
            $normalized.Add($trimmed) | Out-Null
        }
    }

    if ($normalized.Count -eq 0) {
        throw "At least one valid strategy must be provided."
    }

    return @($normalized)
}

$timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$suiteDir = Join-Path $OutputRoot "$timestamp-incident-regression-suite"
New-Item -ItemType Directory -Force -Path $suiteDir | Out-Null
$Strategies = Normalize-Strategies -RawStrategies $Strategies

function Get-LatestResultDir {
    Get-ChildItem $OutputRoot -Directory |
        Where-Object { $_.Name -like "*-auto-remediation-final-test" } |
        Sort-Object LastWriteTime |
        Select-Object -Last 1
}

function Get-NewResultDir {
    param(
        [string[]]$BeforeNames
    )

    $after = Get-ChildItem $OutputRoot -Directory |
        Where-Object { $_.Name -like "*-auto-remediation-final-test" } |
        Sort-Object LastWriteTime

    $newDir = $after |
        Where-Object { $BeforeNames -notcontains $_.Name } |
        Sort-Object LastWriteTime |
        Select-Object -Last 1

    if ($null -eq $newDir) {
        return ($after | Select-Object -Last 1)
    }

    return $newDir
}

function Get-PredictSnapshot {
    param(
        [string]$ResultDir
    )

    $predictPath = Join-Path $ResultDir "10-readonly-predict-attempt-1.json"
    if (-not (Test-Path $predictPath)) {
        return $null
    }

    try {
        return (Get-Content $predictPath -Raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Compress-Text {
    param(
        [AllowNull()]
        [string]$Value,
        [int]$MaxLength = 240
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }

    $singleLine = ($Value -replace "\s+", " ").Trim()
    if ($singleLine.Length -le $MaxLength) {
        return $singleLine
    }

    return ($singleLine.Substring(0, $MaxLength - 3) + "...")
}

function Format-NullableNumber {
    param(
        [AllowNull()]
        [double]$Value,
        [int]$Digits = 4
    )

    if ($null -eq $Value) {
        return ""
    }

    return ([Math]::Round([double]$Value, $Digits)).ToString()
}

$runRows = [System.Collections.Generic.List[object]]::new()

if (-not $SkipStabilize) {
    Write-Host "Stabilizing the five-VM lab before the regression suite..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "stabilize-five-vm-lab.ps1") `
        -KubeconfigPath $KubeconfigPath `
        -Namespace $Namespace `
        -VmPassword $VmPassword `
        -RepairVms `
        -RepairCnpgReplica:$RepairCnpgReplica
}

if (-not $SkipPretest) {
    Write-Host "Capturing a fresh pre-test readiness snapshot..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "pretest-readiness.ps1") `
        -Kubeconfig $KubeconfigPath `
        -Namespace $Namespace `
        -OutputRoot $OutputRoot
}

for ($repeatIndex = 1; $repeatIndex -le $RepeatCount; $repeatIndex++) {
    for ($strategyIndex = 0; $strategyIndex -lt $Strategies.Count; $strategyIndex++) {
        $strategy = $Strategies[$strategyIndex]
        Write-Host ""
        Write-Host "Running $strategy (repeat $repeatIndex/$RepeatCount)..." -ForegroundColor Cyan

        $beforeNames = @(
            Get-ChildItem $OutputRoot -Directory |
                Where-Object { $_.Name -like "*-auto-remediation-final-test" } |
                Sort-Object LastWriteTime |
                Select-Object -ExpandProperty Name
        )

        $nativeExitCode = 0
        try {
            & (Join-Path $PSScriptRoot "run-auto-remediation-final-test.ps1") `
                -KubeconfigPath $KubeconfigPath `
                -Namespace $Namespace `
                -FaultStrategy $strategy `
                -Intensity $Intensity
            if ($null -ne $LASTEXITCODE) {
                $nativeExitCode = $LASTEXITCODE
            }
        } catch {
            $nativeExitCode = 1
            Write-Warning "$strategy raised an exception: $($_.Exception.Message)"
        }

        $resultDir = Get-NewResultDir -BeforeNames $beforeNames
        $resultPath = if ($null -ne $resultDir) { Join-Path $resultDir.FullName "99-final-result.json" } else { "" }
        $result = $null
        if ($resultPath -and (Test-Path $resultPath)) {
            try {
                $result = Get-Content $resultPath -Raw | ConvertFrom-Json
            } catch {
                $result = $null
            }
        }

        $predict = if ($null -ne $resultDir) { Get-PredictSnapshot -ResultDir $resultDir.FullName } else { $null }
        $prediction = if ($null -ne $predict) { $predict.prediction } else { $null }
        $decisionContext = if ($null -ne $prediction) { $prediction.decision_context } else { $null }

        $runRows.Add([pscustomobject]@{
            suiteTimestamp = $timestamp
            repeatIndex = $repeatIndex
            strategy = $strategy
            resultDir = if ($null -ne $resultDir) { $resultDir.Name } else { "" }
            exitCode = $nativeExitCode
            finalStatus = if ($null -ne $result) { $result.finalStatus } else { "NO_RESULT" }
            predictionType = if ($null -ne $result) { $result.predictionType } else { "" }
            predictionAction = if ($null -ne $result) { $result.predictionAction } else { "" }
            predictionConfidence = if ($null -ne $result) { $result.predictionConfidence } else { "" }
            executionMode = if ($null -ne $result) { $result.executionMode } else { "" }
            incidentId = if ($null -ne $result) { $result.incidentId } else { "" }
            workflowId = if ($null -ne $result) { $result.workflowId } else { "" }
            chosenPlanId = if ($null -ne $prediction) { $prediction.chosen_plan_id } else { "" }
            topPlansCount = if ($null -ne $decisionContext) { $decisionContext.top_plans_count } else { "" }
            innovationMode = if ($null -ne $decisionContext) { $decisionContext.innovation_mode } else { "" }
            favoritePlanTitle = if ($null -ne $decisionContext) { $decisionContext.favorite_plan_title } else { "" }
            favoriteStrategy = if ($null -ne $decisionContext) { Compress-Text -Value $decisionContext.favorite_strategy -MaxLength 180 } else { "" }
            decisionExplanation = if ($null -ne $prediction) { Compress-Text -Value $prediction.decision_explanation } else { "" }
            recommendationOnlyReason = if ($null -ne $result) { Compress-Text -Value $result.recommendationOnlyReason } else { "" }
        }) | Out-Null

        $isLastRun = ($repeatIndex -eq $RepeatCount -and $strategyIndex -eq ($Strategies.Count - 1))
        if (-not $SkipBetweenRunStabilize -and -not $isLastRun) {
            Write-Host "Re-stabilizing the five-VM lab before the next regression case..." -ForegroundColor Cyan
            & (Join-Path $PSScriptRoot "stabilize-five-vm-lab.ps1") `
                -KubeconfigPath $KubeconfigPath `
                -Namespace $Namespace `
                -VmPassword $VmPassword `
                -RepairVms `
                -RepairCnpgReplica:$RepairCnpgReplica
        }
    }
}

$aggregateRows = foreach ($group in ($runRows | Group-Object strategy)) {
    $rows = @($group.Group)
    $distinctActions = @($rows | Where-Object { -not [string]::IsNullOrWhiteSpace($_.predictionAction) } | Select-Object -ExpandProperty predictionAction -Unique)
    $distinctTypes = @($rows | Where-Object { -not [string]::IsNullOrWhiteSpace($_.predictionType) } | Select-Object -ExpandProperty predictionType -Unique)
    $distinctFavoritePlans = @($rows | Where-Object { -not [string]::IsNullOrWhiteSpace($_.favoritePlanTitle) } | Select-Object -ExpandProperty favoritePlanTitle -Unique)
    $distinctFavoriteStrategies = @($rows | Where-Object { -not [string]::IsNullOrWhiteSpace($_.favoriteStrategy) } | Select-Object -ExpandProperty favoriteStrategy -Unique)
    $allSucceeded = (@($rows | Where-Object { $_.finalStatus -ne "SUCCESS" }).Count -eq 0)
    $confidenceSamples = @(
        $rows |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_.predictionConfidence) } |
            ForEach-Object { [double]$_.predictionConfidence }
    )
    $averageConfidence = if ($confidenceSamples.Count -gt 0) {
        ($confidenceSamples | Measure-Object -Average).Average
    } else {
        $null
    }

    [pscustomobject]@{
        strategy = $group.Name
        runs = $rows.Count
        allSucceeded = $allSucceeded
        successCount = @($rows | Where-Object { $_.finalStatus -eq "SUCCESS" }).Count
        distinctPredictionTypes = ($distinctTypes -join "; ")
        distinctActions = ($distinctActions -join "; ")
        distinctFavoritePlans = ($distinctFavoritePlans -join "; ")
        distinctFavoriteStrategies = ($distinctFavoriteStrategies -join " || ")
        actionVariabilityCount = $distinctActions.Count
        planVariabilityCount = $distinctFavoritePlans.Count
        strategyVariabilityCount = $distinctFavoriteStrategies.Count
        averageConfidence = if ($null -ne $averageConfidence) { [Math]::Round([double]$averageConfidence, 4) } else { $null }
    }
}

$runCsv = Join-Path $suiteDir "incident-runs.csv"
$aggregateCsv = Join-Path $suiteDir "incident-variability-summary.csv"
$aggregateJson = Join-Path $suiteDir "incident-variability-summary.json"
$reportMarkdown = Join-Path $suiteDir "incident-regression-report.md"

$runRows | Export-Csv -NoTypeInformation -Path $runCsv -Encoding UTF8
$aggregateRows | Export-Csv -NoTypeInformation -Path $aggregateCsv -Encoding UTF8
$aggregateRows | ConvertTo-Json -Depth 6 | Set-Content -Path $aggregateJson -Encoding UTF8

$reportLines = [System.Collections.Generic.List[string]]::new()
$reportLines.Add("# Incident Regression Report") | Out-Null
$reportLines.Add("") | Out-Null
$reportLines.Add("- Generated at: $(Get-Date -Format o)") | Out-Null
$reportLines.Add("- Suite timestamp: $timestamp") | Out-Null
$reportLines.Add("- Namespace: $Namespace") | Out-Null
$reportLines.Add("- Intensity: $Intensity") | Out-Null
$reportLines.Add("- Repeat count: $RepeatCount") | Out-Null
$reportLines.Add("- Strategies: $($Strategies -join ', ')") | Out-Null
$reportLines.Add("") | Out-Null
$reportLines.Add("## Aggregate Summary") | Out-Null
$reportLines.Add("") | Out-Null
$reportLines.Add("| Strategy | Runs | Successes | All Succeeded | Avg Confidence | Types | Actions |") | Out-Null
$reportLines.Add("| --- | ---: | ---: | :---: | ---: | --- | --- |") | Out-Null
foreach ($row in $aggregateRows) {
    $allSucceededLabel = if ($row.allSucceeded) { 'YES' } else { 'NO' }
    $reportLines.Add(
        "| $($row.strategy) | $($row.runs) | $($row.successCount) | $allSucceededLabel | $(Format-NullableNumber -Value $row.averageConfidence) | $($row.distinctPredictionTypes) | $($row.distinctActions) |"
    ) | Out-Null
}
$reportLines.Add("") | Out-Null
$reportLines.Add("## Run Details") | Out-Null
$reportLines.Add("") | Out-Null
foreach ($row in $runRows) {
    $reportLines.Add("### $($row.strategy) / repeat $($row.repeatIndex)") | Out-Null
    $reportLines.Add("") | Out-Null
    $reportLines.Add("- Final status: $($row.finalStatus)") | Out-Null
    $reportLines.Add("- Prediction type: $($row.predictionType)") | Out-Null
    $reportLines.Add("- Prediction action: $($row.predictionAction)") | Out-Null
    $reportLines.Add("- Confidence: $($row.predictionConfidence)") | Out-Null
    $reportLines.Add("- Execution mode: $($row.executionMode)") | Out-Null
    $reportLines.Add("- Chosen plan: $($row.chosenPlanId)") | Out-Null
    $reportLines.Add("- Favorite plan: $($row.favoritePlanTitle)") | Out-Null
    if (-not [string]::IsNullOrWhiteSpace($row.recommendationOnlyReason)) {
        $reportLines.Add("- Recommendation-only reason: $($row.recommendationOnlyReason)") | Out-Null
    }
    if (-not [string]::IsNullOrWhiteSpace($row.decisionExplanation)) {
        $reportLines.Add("- Decision explanation: $($row.decisionExplanation)") | Out-Null
    }
    if (-not [string]::IsNullOrWhiteSpace($row.resultDir)) {
        $reportLines.Add("- Result directory: investigation_outputs/$($row.resultDir)") | Out-Null
    }
    $reportLines.Add("") | Out-Null
}
$reportLines | Set-Content -Path $reportMarkdown -Encoding UTF8

Write-Host ""
Write-Host "Regression suite artifacts written to $suiteDir" -ForegroundColor Green
Write-Host "RUN_SUMMARY_CSV=$runCsv"
Write-Host "VARIABILITY_SUMMARY_CSV=$aggregateCsv"
Write-Host "VARIABILITY_SUMMARY_JSON=$aggregateJson"
Write-Host "REGRESSION_REPORT_MD=$reportMarkdown"
Write-Host ""
$aggregateRows | Format-Table -AutoSize
