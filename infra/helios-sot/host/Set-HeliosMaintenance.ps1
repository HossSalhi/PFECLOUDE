<#
.SYNOPSIS
  HELIOS - pose / retire / liste les drapeaux de MAINTENANCE du watchdog hote. DRY-RUN PAR DEFAUT.

.DESCRIPTION
  Un drapeau  <dossier>\<vm>.flag  EMPECHE tout demarrage automatique de la VM par le watchdog (423 MAINTENANCE) et
  bloque l'autorisation cote backend (motif MAINTENANCE). C'est le mecanisme a utiliser pendant un essai HA-01 ou une
  intervention : il remplace la pause de l'ancien agent :9999 (decision D7), qui n'existe plus.
  Seuls les 5 noms EXACTS de la politique VM_DOWN sont acceptes ; tout autre nom est refuse.
  Sans -Apply, rien n'est ecrit. -Action List est toujours en lecture seule.

.EXAMPLE
  .\Set-HeliosMaintenance.ps1 -Action List
  .\Set-HeliosMaintenance.ps1 -Action Set   -Vm k8s-lb2            # dry-run
  .\Set-HeliosMaintenance.ps1 -Action Set   -Vm k8s-lb2 -Apply     # pose le drapeau
  .\Set-HeliosMaintenance.ps1 -Action Clear -Vm k8s-lb2 -Apply     # retire le drapeau
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Set', 'Clear', 'List')][string]$Action,
    [string]$Vm,
    [string]$Dir = 'C:\Users\houss\helios-maintenance',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
# Table fermee (parite avec infra/helios-vm-policy.yaml) : noms EXACTS, casse comprise.
$known = @('k8s-worker2', 'k8s-worker1', 'k8s-lb2', 'k8s-monitoring', 'k8s-master')

if ($Action -eq 'List') {
    foreach ($n in $known) {
        $f = Join-Path $Dir "$n.flag"
        '{0,-16} {1}' -f $n, $(if (Test-Path -LiteralPath $f) { 'MAINTENANCE (drapeau present)' } else { 'normal' })
    }
    exit 0
}

if (-not $Vm) { throw "-Vm est obligatoire pour $Action" }
if ($known -cnotcontains $Vm) { throw "VM '$Vm' refusee : noms exacts autorises = $($known -join ', ')" }
$flag = Join-Path $Dir "$Vm.flag"

Write-Host "Action : $Action $Vm -> $flag"
if (-not $Apply) {
    Write-Host 'DRY-RUN : rien n''a ete ecrit. Relancer avec -Apply.' -ForegroundColor Yellow
    exit 0
}
if ($Action -eq 'Set') {
    if (-not (Test-Path -LiteralPath $Dir)) { New-Item -ItemType Directory -Path $Dir | Out-Null }
    if (-not (Test-Path -LiteralPath $flag)) { New-Item -ItemType File -Path $flag | Out-Null }
    Write-Host "Drapeau pose : $Vm est en MAINTENANCE (aucun demarrage automatique)."
} else {
    if (Test-Path -LiteralPath $flag) { Remove-Item -LiteralPath $flag -Force }
    Write-Host "Drapeau retire : $Vm n'est plus en maintenance."
}
