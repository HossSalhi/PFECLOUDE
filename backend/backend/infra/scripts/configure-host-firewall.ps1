param(
    [string[]]$RemoteSubnets = @("192.168.148.0/24", "192.168.126.0/24", "192.168.194.0/24"),
    [int[]]$Ports = @(9000, 9001, 26500, 8081, 8082, 8088),
    [string]$RulePrefix = "PFE-AIOps-Lab"
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell session so it can create inbound firewall rules."
}

foreach ($port in $Ports) {
    $displayName = "$RulePrefix-$port"
    $existing = Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Firewall rule already exists: $displayName"
        continue
    }

    New-NetFirewallRule `
        -DisplayName $displayName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $port `
        -RemoteAddress $RemoteSubnets | Out-Null

    Write-Host "Created firewall rule: $displayName for $($RemoteSubnets -join ', ')"
}
