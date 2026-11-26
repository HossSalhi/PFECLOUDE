<#
.SYNOPSIS
  HELIOS - met a jour le SCRIPT du watchdog hote (table de politique) et relance la tache HELIOS-Watchdog. DRY-RUN PAR DEFAUT.

.DESCRIPTION
  Cas d'usage : le script du depot a change (6 nov. 2026 : k8s-monitoring passe de AUTO a DETECT_ONLY, consigne finale de
  l'utilisateur) alors que le watchdog en marche porte encore l'ancienne table. Install-HeliosWatchdog.ps1 ne convient pas : il
  refuse d'ecraser une tache existante.

  Sans -Apply, ce script ne modifie RIEN : il verifie les prerequis, compare les empreintes, lit la table de politique du script
  source et dit s'il est sur de relancer. Avec -Apply il :
    1. REFUSE si un demarrage de VM est en cours (processus vmrun.exe, ou VMRUN_START plus recent que START_DONE dans le journal
       depuis moins de 10 min) : on ne coupe jamais le watchdog pendant un demarrage ;
    2. sauvegarde le script deploye (helios-vm-watchdog.py.bak-<horodatage>) puis copie le script du depot ;
    3. arrete puis relance la tache planifiee (l'etat persistant vm-power-state.json et les cooldowns survivent) ;
    4. attend que 192.168.148.1:9099 reponde, verifie GET /health (ouvert) et `--check` (modes de la table, jeton present) ;
    5. en cas d'echec : RETOUR ARRIERE automatique (script sauvegarde restaure, tache relancee).
  Pendant la coupure (quelques secondes) le backend voit WATCHDOG_UNREACHABLE : il ne prend AUCUNE decision de demarrage.
  Il NE touche NI au pare-feu, NI au jeton (jamais lu ni affiche), NI aux VM, NI au cluster.

  Retour arriere manuel :
    .\Update-HeliosWatchdog.ps1 -RollbackTo C:\Users\houss\helios-vm-watchdog.py.bak-<horodatage> -Apply

.EXAMPLE
  .\Update-HeliosWatchdog.ps1                 # dry-run : rien n'est ecrit
  .\Update-HeliosWatchdog.ps1 -Apply          # deploie la table definitive et relance la tache
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'HELIOS-Watchdog',
    [string]$Python = 'C:\Users\houss\AppData\Local\Programs\Python\Python312\python.exe',
    [string]$ScriptPath = 'C:\Users\houss\helios-vm-watchdog.py',
    [string]$SourceScript = '',
    [string]$LogPath = 'C:\Users\houss\helios-state\helios-watchdog.log',
    [string]$RollbackTo = '',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $PSCommandPath
if (-not $SourceScript) { $SourceScript = [System.IO.Path]::GetFullPath((Join-Path $here '..\watchdog\helios-vm-watchdog.py')) }
# Table attendue du script SOURCE (depot). 2026-11-17 : k8s-master (.153) passe de DENY_RESTART a AUTO sur demande de l'utilisateur
# (HA + auto-rallumage) ; k8s-monitoring (.160) reste DETECT_ONLY. Garde-fou : un script source qui diverge de cette table n'est pas deploye.
$Expected = '{"k8s-lb2": "AUTO", "k8s-master": "AUTO", "k8s-monitoring": "DETECT_ONLY", "k8s-worker1": "AUTO", "k8s-worker2": "AUTO"}'
$problems = New-Object System.Collections.Generic.List[string]
function Check($ok, $msg) { if ($ok) { Write-Host "  [OK]   $msg" } else { Write-Host "  [KO]   $msg" -ForegroundColor Red; $problems.Add($msg) } }

function Get-Modes([string]$path) {
    $code = "import importlib.util,sys,json; s=importlib.util.spec_from_file_location('w', sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps({k:v['mode'] for k,v in m.VM_POLICY.items()}, sort_keys=True))"
    return ((& $Python -I -c $code $path) | Select-Object -Last 1)
}
function Test-Tcp($ip, $port) {
    try { $c = New-Object Net.Sockets.TcpClient; $iar = $c.BeginConnect($ip, $port, $null, $null)
          $ok = $iar.AsyncWaitHandle.WaitOne(2500, $false) -and $c.Connected; $c.Close(); return [bool]$ok } catch { return $false }
}
function Get-WatchdogProcs {
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'helios-vm-watchdog\.py' })
}
function Restart-Watchdog {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 20; $i++) { if ((Get-WatchdogProcs).Count -eq 0) { break }; Start-Sleep -Seconds 1 }
    foreach ($p in (Get-WatchdogProcs)) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-ScheduledTask -TaskName $TaskName
    for ($i = 0; $i -lt 45; $i++) { if (Test-Tcp '192.168.148.1' 9099) { return $true }; Start-Sleep -Seconds 2 }
    return $false
}
function Test-Healthy {
    try {
        $h = Invoke-WebRequest -UseBasicParsing -Uri 'http://192.168.148.1:9099/health' -TimeoutSec 8
        $j = $h.Content | ConvertFrom-Json
        Write-Host ("  /health : HTTP {0} status={1} auth_configured={2} version={3}" -f $h.StatusCode, $j.status, $j.auth_configured, $j.version)
        return ($h.StatusCode -eq 200 -and $j.status -eq 'ok' -and $j.auth_configured -eq $true)
    } catch { Write-Host "  /health : echec ($($_.Exception.Message))" -ForegroundColor Red; return $false }
}

if ($RollbackTo) {
    Write-Host "== RETOUR ARRIERE vers $RollbackTo =="
    Check (Test-Path $RollbackTo) "sauvegarde presente"
    if ($problems.Count -gt 0) { exit 1 }
    if (-not $Apply) { Write-Host "DRY-RUN : rien n'est ecrit. Ajouter -Apply pour restaurer et relancer la tache."; exit 0 }
    Copy-Item $RollbackTo $ScriptPath -Force
    $up = Restart-Watchdog
    $ok = $up -and (Test-Healthy)
    Write-Host ("Retour arriere : " + $(if ($ok) { 'OK, watchdog en marche' } else { 'ECHEC : verifier la tache HELIOS-Watchdog et le journal' }))
    exit $(if ($ok) { 0 } else { 1 })
}

Write-Host "== Prerequis (lecture seule) =="
Check (Test-Path $Python) "Python present: $Python"
Check (Test-Path $ScriptPath) "Script deploye present: $ScriptPath"
Check (Test-Path $SourceScript) "Script source present: $SourceScript"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Check ($null -ne $task) "La tache $TaskName existe (etat: $($task.State))"
if ((Test-Path $Python) -and (Test-Path $SourceScript)) {
    & $Python -I -c "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1], 'exec')" $SourceScript
    Check ($LASTEXITCODE -eq 0) 'Le script source se compile (en memoire, aucun .pyc ecrit)'
    $srcModes = Get-Modes $SourceScript
    Check ($srcModes -eq $Expected) "Table de politique du script source = politique definitive ($srcModes)"
    $curModes = Get-Modes $ScriptPath
    Write-Host "  table du script deploye : $curModes"
}
$shaSrc = (Get-FileHash $SourceScript -Algorithm SHA256).Hash.Substring(0, 12).ToLower()
$shaCur = (Get-FileHash $ScriptPath -Algorithm SHA256).Hash.Substring(0, 12).ToLower()
Write-Host "  empreintes : source=$shaSrc deploye=$shaCur"
if ($shaSrc -eq $shaCur) { Write-Host "Le watchdog deploye est DEJA a jour : rien a faire."; exit 0 }

Write-Host "== Aucun demarrage de VM en cours (lecture seule) =="
$vmrunBusy = @(Get-Process -Name vmrun -ErrorAction SilentlyContinue).Count
Check ($vmrunBusy -eq 0) "aucun processus vmrun.exe en cours ($vmrunBusy)"
$inflight = $false
if (Test-Path $LogPath) {
    $last = @(Get-Content $LogPath -Tail 400 | Where-Object { $_ -match '"event": "(VMRUN_START|START_DONE)"' } | Select-Object -Last 2)
    if ($last.Count -gt 0) {
        $l = $last[-1] | ConvertFrom-Json
        if ($l.event -eq 'VMRUN_START') {
            $age = ((Get-Date).ToUniversalTime() - [datetime]::Parse($l.ts, $null, [Globalization.DateTimeStyles]::AdjustToUniversal)).TotalMinutes
            if ($age -lt 10) { $inflight = $true }
        }
    }
}
Check (-not $inflight) "aucun VMRUN_START recent sans START_DONE dans le journal"

if ($problems.Count -gt 0) { Write-Host "`nREFUS : $($problems.Count) prerequis en echec - rien n'est modifie." -ForegroundColor Red; exit 1 }
Write-Host "`nPLAN : sauvegarde de $ScriptPath ; copie de $SourceScript ; relance de la tache $TaskName ; verification /health et --check ; retour arriere automatique si KO."
if (-not $Apply) { Write-Host "DRY-RUN : rien n'est ecrit. Ajouter -Apply pour deployer."; exit 0 }

$bak = "$ScriptPath.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
Copy-Item $ScriptPath $bak
Write-Host "sauvegarde : $bak"
Copy-Item $SourceScript $ScriptPath -Force
$up = Restart-Watchdog
$healthy = $up -and (Test-Healthy)
$modes = $null
if ($healthy) {
    $chk = ((& $Python -I $ScriptPath --check) -join "`n") | ConvertFrom-Json
    $modes = ($chk.modes.PSObject.Properties | Sort-Object Name | ForEach-Object { '"' + $_.Name + '": "' + $_.Value + '"' }) -join ', '
    $modes = '{' + $modes + '}'
    Write-Host "  --check : modes=$modes token_configured=$($chk.token_configured) vmrun_found=$($chk.vmrun_found) appelants=$($chk.allowed_callers -join ',')"
    $healthy = ($modes -eq $Expected) -and $chk.token_configured -and $chk.vmrun_found
}
if ($healthy) {
    Write-Host "`nOK : watchdog mis a jour et en marche (table definitive). Retour arriere : .\Update-HeliosWatchdog.ps1 -RollbackTo $bak -Apply"
    exit 0
}
Write-Host "`nECHEC : retour arriere automatique vers $bak" -ForegroundColor Red
Copy-Item $bak $ScriptPath -Force
$back = Restart-Watchdog
Write-Host ("retour arriere : " + $(if ($back -and (Test-Healthy)) { 'watchdog precedent relance, OK' } else { 'ECHEC - verifier la tache HELIOS-Watchdog et le journal' }))
exit 1
