param(
    [string]$KubeconfigPath = "investigation_outputs/live-kubeconfig-master",
    [string]$Namespace = "pfe",
    [ValidateSet("VmDown", "AvailabilityDrop", "CpuStress", "MemoryStress", "RestartLoop", "ApplicationErrorStorm", "DatabaseConnectivity", "GeneralDegradation", "CrashOnDemand")]
    [string]$FaultStrategy = "RestartLoop",
    [ValidateSet("Low", "Medium", "High")]
    [string]$Intensity = "Medium",
    [switch]$SkipVmRepair,
    [switch]$RepairCnpgReplica,
    [string]$VmPassword = "kube",
    [int]$DemoCooldownMinutes = 0,
    [int]$RestoreCooldownMinutes = 30,
    [double]$DefaultDemoRestartConfidenceThreshold = 0.45,
    [double]$MemoryDemoRestartConfidenceThreshold = 0.40,
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
        throw "Kubernetes API is unreachable during: $Context. Check that k8s-master (192.168.148.153) is up and reachable before retrying."
    }
}

if (-not $SkipVmRepair) {
    Write-Host "Repairing persistent VM routes/services before the demo..." -ForegroundColor Cyan
    $previousPythonEncoding = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = "utf-8"
    try {
        & python (Join-Path $PSScriptRoot "repair-five-vm-services.py") --password $VmPassword
        if ($LASTEXITCODE -ne 0) {
            throw "VM repair command failed with exit code $LASTEXITCODE"
        }
    } finally {
        $env:PYTHONIOENCODING = $previousPythonEncoding
    }

    Write-Host "Waiting for VM services and route reconciliation..." -ForegroundColor Cyan
    Start-Sleep -Seconds 8
}

try {
    Assert-KubeApiReachable -Context "initial demo preparation"

    $demoRestartConfidenceThreshold = switch ($FaultStrategy) {
        "VmDown" { 0.30 }
        "AvailabilityDrop" { 0.35 }
        "MemoryStress" { $MemoryDemoRestartConfidenceThreshold }
        "DatabaseConnectivity" { 0.35 }
        "GeneralDegradation" { 0.35 }
        default { $DefaultDemoRestartConfidenceThreshold }
    }

    Write-Host "Preparing backend for repeatable demo cooldown=$DemoCooldownMinutes confidenceThreshold=$demoRestartConfidenceThreshold..." -ForegroundColor Cyan
    & kubectl --kubeconfig $KubeconfigPath -n $Namespace set env deployment/backend `
        "REMEDIATION_COOLDOWN_MINUTES=$DemoCooldownMinutes" `
        "REMEDIATION_RESTART_CONFIDENCE_THRESHOLD=$demoRestartConfidenceThreshold" | Out-Host
    & kubectl --kubeconfig $KubeconfigPath -n $Namespace rollout status deployment/backend --timeout=240s | Out-Host
    Assert-KubeApiReachable -Context "post-backend rollout"

    & (Join-Path $PSScriptRoot "stabilize-five-vm-lab.ps1") `
        -KubeconfigPath $KubeconfigPath `
        -Namespace $Namespace `
        -VmPassword $VmPassword `
        -RepairVms:(-not $SkipVmRepair) `
        -RepairCnpgReplica:$RepairCnpgReplica

    Write-Host ""
    Write-Host "Starting final AIOps self-healing scenario..." -ForegroundColor Cyan

    & (Join-Path $PSScriptRoot "run-auto-remediation-final-test.ps1") `
        -KubeconfigPath $KubeconfigPath `
        -Namespace $Namespace `
        -FaultStrategy $FaultStrategy `
        -Intensity $Intensity
} finally {
    Write-Host ""
    Write-Host "Restoring backend remediation cooldown=$RestoreCooldownMinutes confidenceThreshold=$DefaultDemoRestartConfidenceThreshold..." -ForegroundColor Cyan
    if (Test-KubeApiReachable) {
        & kubectl --kubeconfig $KubeconfigPath -n $Namespace set env deployment/backend `
            "REMEDIATION_COOLDOWN_MINUTES=$RestoreCooldownMinutes" `
            "REMEDIATION_RESTART_CONFIDENCE_THRESHOLD=$DefaultDemoRestartConfidenceThreshold" | Out-Host
        & kubectl --kubeconfig $KubeconfigPath -n $Namespace rollout status deployment/backend --timeout=240s | Out-Host
    } else {
        Write-Warning "Skipping cooldown restore because the Kubernetes API is unreachable."
    }
}
