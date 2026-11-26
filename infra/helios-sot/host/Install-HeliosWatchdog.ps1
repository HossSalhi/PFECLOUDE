<#
.SYNOPSIS
  HELIOS - installe le watchdog hote (tache planifiee HELIOS-Watchdog). DRY-RUN PAR DEFAUT.

.DESCRIPTION
  Sans -Apply, ce script ne modifie RIEN : il verifie les prerequis, rend le XML de la tache et affiche
  le plan d'installation. Avec -Apply il :
    1. sauvegarde puis copie helios-vm-watchdog.py vers -ScriptPath ;
    2. cree le dossier d'etat et le dossier de maintenance s'ils manquent ;
    3. enregistre la tache HELIOS-Watchdog DESACTIVEE (S4U, boot + ouverture de session + relance toutes les 5 min,
       aucune restriction batterie, relance sur echec 999 x 1 min) ; -EnableTask l'enregistre activee.
  4. DESACTIVE (ne supprime pas) l'ancienne tache HELIOS-Watchdog-HTTP : elle se declenche au demarrage SANS delai
     et lierait le port 9099 avant la nouvelle tache (delai 60 s) ; il ne doit y avoir qu'UN watchdog.
  Il NE demarre PAS la tache (sauf -StartNow), NE touche PAS au pare-feu, NE supprime AUCUNE tache
  (HELIOS-NodeAutoheal est deja desactivee et n'est pas modifiee), n'ecrit aucun jeton.
  Retour arriere : Unregister-ScheduledTask HELIOS-Watchdog ; Enable-ScheduledTask HELIOS-Watchdog-HTTP.
  Chaque etape de production (installation, pare-feu, demarrage) exige son autorisation distincte.

.EXAMPLE
  .\Install-HeliosWatchdog.ps1                  # dry-run : rien n'est ecrit
  .\Install-HeliosWatchdog.ps1 -Apply           # installe la tache (sans la demarrer)
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'HELIOS-Watchdog',
    [string]$OldHttpTask = 'HELIOS-Watchdog-HTTP',
    [string]$User = "$env:USERDOMAIN\$env:USERNAME",
    [string]$Python = 'C:\Users\houss\AppData\Local\Programs\Python\Python312\python.exe',
    [string]$ScriptPath = 'C:\Users\houss\helios-vm-watchdog.py',
    [string]$SourceScript = '',
    [string]$WorkDir = 'C:\Users\houss',
    [string]$StateDir = 'C:\Users\houss\helios-state',
    [string]$MaintenanceDir = 'C:\Users\houss\helios-maintenance',
    [string]$Template = '',
    [switch]$Apply,
    [switch]$EnableTask,
    [switch]$StartNow
)
$ErrorActionPreference = 'Stop'
# $PSScriptRoot n'est pas fiable dans les valeurs par defaut de param() sous Windows PowerShell 5.1.
$here = Split-Path -Parent $PSCommandPath
if (-not $SourceScript) { $SourceScript = [System.IO.Path]::GetFullPath((Join-Path $here '..\watchdog\helios-vm-watchdog.py')) }
if (-not $Template) { $Template = Join-Path $here 'tache-HELIOS-Watchdog.xml.template' }
$problems = New-Object System.Collections.Generic.List[string]

function Check($ok, $msg) { if ($ok) { Write-Host "  [OK]   $msg" } else { Write-Host "  [KO]   $msg" -ForegroundColor Red; $problems.Add($msg) } }

Write-Host "== Prerequis (lecture seule) =="
Check (Test-Path $Python) "Python present: $Python"
Check (Test-Path $SourceScript) "Script source present: $SourceScript"
Check (Test-Path $Template) "Modele de tache present: $Template"
Check (Test-Path 'C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe') 'vmrun.exe present'
Check (Test-Path 'C:\Users\houss\.helios-watchdog-token') 'Fichier de jeton present (contenu NON lu ici)'
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Check ($null -eq $existing) "La tache $TaskName n'existe pas encore (aucun ecrasement)"
if ((Test-Path $Python) -and (Test-Path $SourceScript)) {
    # Compilation EN MEMOIRE (py_compile ecrirait un .pyc dans le depot, meme en dry-run).
    & $Python -c "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1], 'exec')" $SourceScript
    Check ($LASTEXITCODE -eq 0) 'Le script se compile'
}

$xml = (Get-Content -Raw -Encoding UTF8 $Template).Replace('{{USER}}', $User).Replace('{{PYTHON}}', $Python).Replace('{{SCRIPT}}', $ScriptPath).Replace('{{WORKDIR}}', $WorkDir).Replace('{{ENABLED}}', $(if ($EnableTask -or $StartNow) { 'true' } else { 'false' }))
try { [void][xml]$xml } catch { Check $false "XML de la tache invalide: $_" }

# Validation EN MEMOIRE par le Planificateur de taches (rien n'est enregistre) : detecte tout XML refuse.
try {
    $svc = New-Object -ComObject 'Schedule.Service'
    $svc.Connect()
    $def = $svc.NewTask(0)
    $def.XmlText = $xml
    Check $true 'Le Planificateur de taches accepte la definition (validation en memoire, rien d''enregistre)'
} catch {
    Check $false ("Definition refusee par le Planificateur de taches : " + $_.Exception.Message)
}
$oldHttp = Get-ScheduledTask -TaskName $OldHttpTask -ErrorAction SilentlyContinue
Write-Host ("  [info] ancienne tache {0} : {1}" -f $OldHttpTask, $(if ($oldHttp) { $oldHttp.State } else { 'absente' }))
$oldAuto = Get-ScheduledTask -TaskName 'HELIOS-NodeAutoheal' -ErrorAction SilentlyContinue
Write-Host ("  [info] ancienne tache HELIOS-NodeAutoheal : {0} (non modifiee)" -f $(if ($oldAuto) { $oldAuto.State } else { 'absente' }))

Write-Host ""
Write-Host "== Plan =="
Write-Host "  copie     : $SourceScript -> $ScriptPath (sauvegarde horodatee si le fichier existe)"
Write-Host "  dossiers  : $StateDir ; $MaintenanceDir (crees s'ils manquent)"
Write-Host "  tache     : $TaskName pour $User (S4U), action: `"$Python`" `"$ScriptPath`""
Write-Host "  etat      : $(if ($EnableTask -or $StartNow) { 'ACTIVEE' } else { 'DESACTIVEE : ne se declenche NI maintenant NI au prochain boot/session (l etape B l active et la demarre)' })"
Write-Host "  demarrage : $(if ($StartNow) { 'IMMEDIAT (-StartNow)' } else { 'NON' })"
Write-Host ("  desactive : {0} (conservee ; un seul watchdog doit lier le port 9099 au demarrage)" -f $OldHttpTask)
Write-Host "  inchange  : pare-feu, HELIOS-NodeAutoheal, agent :9999, jeton"

if (-not $Apply) {
    Write-Host ""
    Write-Host "DRY-RUN : rien n'a ete ecrit. Relancer avec -Apply apres autorisation." -ForegroundColor Yellow
    if ($problems.Count -gt 0) { exit 1 } else { exit 0 }
}
if ($problems.Count -gt 0) { Write-Host "Prerequis manquants : arret, rien n'a ete ecrit." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "== Application =="
if (Test-Path $ScriptPath) {
    $backup = "$ScriptPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item $ScriptPath $backup
    Write-Host "  sauvegarde: $backup"
}
Copy-Item $SourceScript $ScriptPath -Force
foreach ($d in @($StateDir, $MaintenanceDir)) { if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null } }
Register-ScheduledTask -TaskName $TaskName -Xml $xml | Out-Null
Write-Host "  tache enregistree: $TaskName"
if ($oldHttp -and $oldHttp.State -ne 'Disabled') {
    Disable-ScheduledTask -TaskName $OldHttpTask | Out-Null
    Write-Host "  tache desactivee : $OldHttpTask (conservee)"
}
if ($StartNow) { Start-ScheduledTask -TaskName $TaskName; Write-Host "  tache demarree" }
elseif (-not $EnableTask) { Write-Host "  tache DESACTIVEE : aucun declenchement avant Enable-ScheduledTask (etape B)" }
Write-Host "Termine. Verifier ensuite: python `"$ScriptPath`" --check ; puis GET /health depuis .153, .155 et .158 (apres ouverture du pare-feu)."
