# HELIOS HA-01 (D7) — suspend / rétablit les automatismes capables de RALLUMER une VM pendant un test d'arrêt.
#   - agent hôte  C:\Users\houss\helios-vmpower-agent.py  (port 9999) : appelé par le backend (REMEDIATION_WATCHDOG_BASE_URL)
#   - watchdog HTTP (port 9099)                                       : appelé par le Bridge (RESTART_NODE_VM) — absent aujourd'hui
# Le Bridge/Camunda (détection toutes les 5 min) et le backend RESTENT actifs : leurs réactions sont journalisées
# (journalctl -u helios-ssh-bridge sur .161) pour documenter ce que HELIOS aurait fait.
# NB : Get-NetTCPConnection est peu fiable sur ce poste (n'affiche pas l'agent alors qu'il répond) : on teste la
#      connexion TCP réelle et on identifie les processus par leur ligne de commande.
# Usage :  .\helios-pause.ps1 -Action status|pause|resume
param([Parameter(Mandatory=$true)][ValidateSet('status','pause','resume')][string]$Action)
$stateFile   = Join-Path $PSScriptRoot '..\results\2026-11-03\helios-pause-state.json'
$agentScript = 'C:\Users\houss\helios-vmpower-agent.py'
$pyw         = 'C:\Users\houss\AppData\Local\Programs\Python\Python312\pythonw.exe'
function Test-Port($port) {
  try { $c = New-Object Net.Sockets.TcpClient; $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(1500) -and $c.Connected; $c.Close(); return $ok } catch { return $false }
}
function Get-AgentProcs { Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'helios-vmpower-agent\.py' } }
switch ($Action) {
  'status' {
    "port 9999 (agent vmpower) : " + $(if (Test-Port 9999) { 'REPOND' } else { 'ne repond pas' })
    "port 9099 (watchdog HTTP) : " + $(if (Test-Port 9099) { 'REPOND' } else { 'ne repond pas' })
    Get-AgentProcs | ForEach-Object { "processus agent : pid $($_.ProcessId) | $($_.CommandLine)" }
  }
  'pause' {
    $procs = @(Get-AgentProcs)
    $saved = @($procs | ForEach-Object { [pscustomobject]@{ pid = $_.ProcessId; cmd = $_.CommandLine } })
    $saved | ConvertTo-Json -AsArray | Set-Content -Path $stateFile -Encoding utf8
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force; "agent vmpower pid $($p.ProcessId) arrêté" }
    if (-not $procs) { 'aucun processus agent à arrêter' }
    Start-Sleep -Seconds 2
    if (Test-Port 9999) { throw 'port 9999 répond encore : pause NON effective' }
    if (Test-Port 9099) { throw 'port 9099 répond : un watchdog est actif, à traiter manuellement' }
    'PAUSE effective : 9999 et 9099 ne répondent plus — aucun automatisme hôte ne peut rallumer une VM.'
  }
  'resume' {
    if (-not (Test-Port 9999)) { Start-Process -FilePath $pyw -ArgumentList "`"$agentScript`"" -WindowStyle Hidden; Start-Sleep -Seconds 3 }
    if (Test-Path $stateFile) { Remove-Item $stateFile -Force }
    "port 9999 (agent vmpower) : " + $(if (Test-Port 9999) { 'RÉTABLI' } else { 'ATTENTION : ne répond pas' })
  }
}
