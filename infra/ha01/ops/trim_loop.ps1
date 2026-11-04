# HA-01 — maintient la RAM hôte « disponible » au-dessus des seuils des préflights pendant une préparation : relance trim_host_ram.ps1
# toutes les -EverySeconds secondes pendant -Minutes minutes (processus détaché, aucune action sur les VM ni sur les processus VMware).
# Usage : .\trim_loop.ps1 -Minutes 40 -EverySeconds 240
param([int]$Minutes = 30, [int]$EverySeconds = 240)
$end = (Get-Date).AddMinutes($Minutes)
$log = Join-Path $PSScriptRoot '..\results\2026-11-03\trim_loop.log'
while ((Get-Date) -lt $end) {
  $r = & (Join-Path $PSScriptRoot 'trim_host_ram.ps1')
  Add-Content -Path $log -Value ("{0:o} {1}" -f (Get-Date).ToUniversalTime(), $r)
  Start-Sleep -Seconds $EverySeconds
}
Add-Content -Path $log -Value ("{0:o} fin de la boucle" -f (Get-Date).ToUniversalTime())
