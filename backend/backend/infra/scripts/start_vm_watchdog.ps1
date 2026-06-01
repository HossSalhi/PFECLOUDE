# Start the VM Watchdog as a background job on Windows
# This script:
#   1. Finds the VMX paths for each K8s node automatically
#   2. Refreshes the kubeconfig
#   3. Starts the watchdog in a new minimized terminal

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)

Write-Host "=== PFE-Orange VM Watchdog Launcher ===" -ForegroundColor Cyan

# Step 1: Refresh kubeconfig
Write-Host "Refreshing kubeconfig..." -ForegroundColor Yellow
python "$ScriptDir\_fetch_kubeconfig.py"

# Step 2: Auto-discover VMX paths and update config
Write-Host "Discovering VMX paths..." -ForegroundColor Yellow
python - << 'PYEOF'
import json, os
from pathlib import Path

config_path = Path(__file__).parent / "vm_watchdog_config.json" if False else Path(os.path.join(os.path.dirname(os.path.abspath(".")), "infra", "scripts", "vm_watchdog_config.json"))

# Load existing config
try:
    with open(config_path) as f:
        cfg = json.load(f)
except FileNotFoundError:
    cfg = {}

nodes_needing_discovery = []
for node, ncfg in cfg.get("nodes", {}).items():
    if "PATH\\TO" in ncfg.get("vmx_path", ""):
        nodes_needing_discovery.append(node)

if nodes_needing_discovery:
    print(f"Nodes needing VMX discovery: {nodes_needing_discovery}")
    print("Searching common VMware directories...")
    search_roots = [
        Path.home() / "Documents" / "Virtual Machines",
        Path("C:/VMs"),
        Path("D:/VMs"),
        Path("C:/VMware"),
        Path("D:/VMware"),
    ]
    found = {}
    for root in search_roots:
        if not root.exists():
            continue
        for vmx in root.rglob("*.vmx"):
            for node in nodes_needing_discovery:
                if node.lower() in vmx.stem.lower() or node.replace("k8s-", "").lower() in vmx.stem.lower():
                    found[node] = str(vmx)
                    print(f"  Found {node}: {vmx}")

    if found:
        for node, path in found.items():
            cfg["nodes"][node]["vmx_path"] = path
        with open(config_path, "w") as f:
            json.dump(cfg, f, indent=2)
        print("Config updated with discovered paths.")
    else:
        print("WARNING: Could not auto-discover VMX files.")
        print(f"Please manually edit: {config_path}")
        print("Set vmx_path for each node to the .vmx file location.")
else:
    print("VMX paths already configured.")
PYEOF

# Step 3: Start watchdog in a new terminal window
Write-Host "Starting VM Watchdog..." -ForegroundColor Green

$watchdogScript = Join-Path $ScriptDir "vm_watchdog.py"
$logFile = Join-Path $ScriptDir "vm_watchdog.log"

# Check if already running
$existing = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*vm_watchdog*"
}
if ($existing) {
    Write-Host "Watchdog is already running (PID: $($existing.Id))" -ForegroundColor Yellow
    exit 0
}

# Start in new window
$proc = Start-Process -FilePath "python" `
    -ArgumentList "`"$watchdogScript`"" `
    -WorkingDirectory $ScriptDir `
    -WindowStyle Minimized `
    -PassThru

Write-Host "Watchdog started (PID: $($proc.Id))" -ForegroundColor Green
Write-Host "HTTP API: http://localhost:9099/health" -ForegroundColor Cyan
Write-Host "Status:   http://localhost:9099/status" -ForegroundColor Cyan
Write-Host "Logs:     $logFile" -ForegroundColor Cyan
Write-Host ""
Write-Host "To manually restart a node:" -ForegroundColor White
Write-Host "  Invoke-RestMethod -Method POST http://localhost:9099/restart/k8s-worker1" -ForegroundColor Gray
