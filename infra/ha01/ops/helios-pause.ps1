# HELIOS HA-01 - suspend / retablit le REDEMARRAGE AUTOMATIQUE de la VM visee pendant un test d'arret.
#
# VMDOWN-POLICY-20261105 : REECRITURE de la pause D7. L'ancien mecanisme arretait l'agent hote :9999 (non
# authentifie, retire par la politique VM_DOWN). Le redemarrage est desormais porte par le watchdog hote (:9099) et
# decide par le backend ; la pause d'une VM = un DRAPEAU DE MAINTENANCE  <dossier>\<vm>.flag  :
#   - le watchdog refuse tout demarrage de cette VM (423 MAINTENANCE) ;
#   - le backend refuse de l'autoriser (motif MAINTENANCE).
# Le Bridge/Camunda (detection) et le reste du backend RESTENT actifs : leurs reactions sont journalisees.
#
# PRECONDITION de securite : l'agent legacy :9999 ne doit PLUS repondre. S'il repond, 'pause' echoue (code != 0) et le
# test d'arret n'a pas lieu (retirer l'agent avec infra/helios-sot/host/Retire-HeliosLegacyAgent.ps1).
# NB : Get-NetTCPConnection est peu fiable sur ce poste : on teste la connexion TCP reelle.
#
# Usage :  .\helios-pause.ps1 -Action status [-Vm 162]
#          .\helios-pause.ps1 -Action pause  -Vm 162      (162 | 160 | 153 | 155 | 158 | nom exact k8s-...)
#          .\helios-pause.ps1 -Action resume -Vm 162
param(
  [Parameter(Mandatory=$true)][ValidateSet('status','pause','resume')][string]$Action,
  [string]$Vm,
  [string]$Dir = 'C:\Users\houss\helios-maintenance',
  [int]$LegacyPort = 9999,
  [int]$WatchdogPort = 9099
)
$names = [ordered]@{ '155' = 'k8s-worker2'; '158' = 'k8s-worker1'; '162' = 'k8s-lb2'; '160' = 'k8s-monitoring'; '153' = 'k8s-master' }

function Test-Port($port) {
  try { $c = New-Object Net.Sockets.TcpClient; $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(1500) -and $c.Connected; $c.Close(); return $ok } catch { return $false }
}
function Resolve-VmName([string]$v) {
  if ($names.Contains($v)) { return $names[$v] }
  if ($names.Values -ccontains $v) { return $v }
  throw "VM '$v' inconnue : suffixes $($names.Keys -join ', ') ou noms exacts $($names.Values -join ', ')"
}
function Flag-Path([string]$name) { Join-Path $Dir "$name.flag" }

switch ($Action) {
  'status' {
    "agent legacy :$LegacyPort : " + $(if (Test-Port $LegacyPort) { 'REPOND (a retirer avant tout test)' } else { 'ne repond pas (attendu)' })
    "watchdog :$WatchdogPort : " + $(if (Test-Port $WatchdogPort) { 'REPOND' } else { 'ne repond pas' })
    foreach ($n in $names.Values) { '{0,-16} {1}' -f $n, $(if (Test-Path -LiteralPath (Flag-Path $n)) { 'MAINTENANCE (drapeau present)' } else { 'normal' }) }
  }
  'pause' {
    if (-not $Vm) { throw '-Vm est obligatoire pour pause' }
    $name = Resolve-VmName $Vm
    if (Test-Port $LegacyPort) { throw "l'agent legacy :$LegacyPort repond encore : pause NON effective (le retirer d'abord)" }
    if (-not (Test-Path -LiteralPath $Dir)) { New-Item -ItemType Directory -Path $Dir | Out-Null }
    $f = Flag-Path $name
    if (-not (Test-Path -LiteralPath $f)) { New-Item -ItemType File -Path $f | Out-Null }
    if (-not (Test-Path -LiteralPath $f)) { throw "drapeau de maintenance non pose : $f" }
    "PAUSE effective : $name en MAINTENANCE (drapeau $f) - aucun demarrage automatique de cette VM."
  }
  'resume' {
    if (-not $Vm) { throw '-Vm est obligatoire pour resume' }
    $name = Resolve-VmName $Vm
    $f = Flag-Path $name
    if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force }
    if (Test-Path -LiteralPath $f) { throw "drapeau de maintenance non retire : $f" }
    "REPRISE effective : $name n'est plus en maintenance (drapeau retire)."
  }
}
