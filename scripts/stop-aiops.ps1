# ============================================================================
#  HELIOS AIOps — Cluster graceful shutdown
# ----------------------------------------------------------------------------
#  Sends ACPI soft-stop to all 5 VMs in parallel. Force-stops any that fail to
#  shut down within 90 s. Use BEFORE starting Docker Desktop on this host.
# ============================================================================
[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 90
)
$ErrorActionPreference = 'Continue'

$vmrun = 'C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe'
$vmDir = "$env:USERPROFILE\Documents\Virtual Machines"
$vms = @('k8s-master','k8s-worker1','k8s-worker2','k8s-monitoring','k8s-aiCamunda')

function Write-Step($msg) { Write-Host "[$([DateTime]::Now.ToString('HH:mm:ss'))] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  $msg" -ForegroundColor Yellow }

if (-not (Test-Path $vmrun)) { Write-Host "vmrun.exe not found: $vmrun" -ForegroundColor Red; exit 1 }

Write-Step "Soft-stop signaled to $($vms.Count) VMs in parallel (timeout=${TimeoutSeconds}s)"
$jobs = foreach ($vm in $vms) {
    Start-Job -ScriptBlock {
        param($vmrun, $vmDir, $vm)
        & $vmrun -T ws stop "$vmDir\$vm\$vm.vmx" soft 2>&1
        "$vm done"
    } -ArgumentList $vmrun, $vmDir, $vm
}
$null = Wait-Job -Job $jobs -Timeout $TimeoutSeconds
$jobs | ForEach-Object { Receive-Job -Job $_ } | Out-Null
$jobs | Remove-Job -Force -ErrorAction SilentlyContinue

# Force-stop survivors
$running = & $vmrun list | Select-String '\.vmx$'
foreach ($line in $running) {
    $vmxPath = $line.ToString().Trim()
    Write-Warn "Force-stopping: $(Split-Path -Leaf $vmxPath)"
    & $vmrun -T ws stop $vmxPath hard 2>&1 | Out-Null
}
Start-Sleep -Seconds 2

Write-Step "Final state:"
& $vmrun list

$os = Get-CimInstance Win32_OperatingSystem
$freeGB = [math]::Round($os.FreePhysicalMemory/1MB, 1)
Write-Ok "All VMs stopped. Host free RAM: $freeGB GB"
Write-Host "It is now safe to start Docker Desktop." -ForegroundColor Gray
