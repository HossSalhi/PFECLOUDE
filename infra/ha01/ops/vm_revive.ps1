# HA-01 Phase 4 — FILET DE SURVIE d'un arrêt réel de VM : processus Windows DÉTACHÉ qui RALLUME la VM si l'orchestrateur
# (p4_vmstop.py) ou la session disparaît pendant que la VM est éteinte. Indépendant de Claude Code et de Python.
#   - attend -Seconds s ; si le fichier-témoin -StopFile existe (posé par l'orchestrateur en fin normale) : sort sans rien faire ;
#   - sinon, si la VM est arrêtée : `vmrun start <vmx> nogui` ; si elle tourne déjà : ne fait rien.
# Lancé par p4_vmstop.py via Start-Process (fenêtre cachée). Journal : -LogFile.
param(
  [Parameter(Mandatory=$true)][string]$Vmx,
  [Parameter(Mandatory=$true)][int]$Seconds,
  [Parameter(Mandatory=$true)][string]$StopFile,
  [Parameter(Mandatory=$true)][string]$LogFile
)
$vmrun = 'C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe'
function Log($m) { Add-Content -Path $LogFile -Value ("{0:o} {1}" -f (Get-Date).ToUniversalTime(), $m) }
Log "filet armé : rallumage de '$Vmx' dans $Seconds s, sauf si le fichier-témoin '$StopFile' existe"
$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
  if (Test-Path $StopFile) { Log "désarmé (fichier-témoin présent) : fin sans action"; exit 0 }
  Start-Sleep -Seconds 5
}
if (Test-Path $StopFile) { Log "désarmé (fichier-témoin présent à l'échéance) : fin sans action"; exit 0 }
$running = (& $vmrun -T ws list) -join "`n"
if ($running -match [regex]::Escape($Vmx)) { Log "ÉCHÉANCE : la VM tourne déjà, rien à faire"; exit 0 }
Log "ÉCHÉANCE : la VM est arrêtée et personne n'a désarmé le filet -> vmrun start"
& $vmrun -T ws start $Vmx nogui 2>&1 | ForEach-Object { Log "vmrun: $_" }
Log "terminé"
