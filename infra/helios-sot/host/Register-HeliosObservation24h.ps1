<#
.SYNOPSIS
  Supervision PERSISTANTE de 24 h : enregistre (ou retire) une tâche planifiée qui lance un relevé d'observation toutes les N minutes.

.DESCRIPTION
  DRY-RUN PAR DÉFAUT (rien n'est écrit). -Apply enregistre la tâche ; -Remove la retire ; -Status montre l'état.
  Les relevés sont strictement PASSIFS : lecture seule du cluster, de la base, de l'hôte et du Bridge ; aucun arrêt/démarrage de VM, aucun
  incident ni aucune charge injectés (preuve : scripts/helios-ops/test_observe_passive.py). Chaque exécution est un processus court
  (--once implicite) : un échec isolé ne tue pas l'observation, et la tâche survit à un redémarrage de la session (S4U) ou du PC.

  Outils : shadow_armed = infra/helios-sot/tools/shadow_observe.py --armed ; stabilite = infra/helios-sot/tools/passive_observe.py.
  Retour arrière : .\Register-HeliosObservation24h.ps1 -Tool <outil> -Remove -Apply   (les relevés déjà écrits sont conservés).

.EXAMPLE
  .\Register-HeliosObservation24h.ps1 -Tool shadow_armed                 # dry-run
  .\Register-HeliosObservation24h.ps1 -Tool shadow_armed -Apply
  .\Register-HeliosObservation24h.ps1 -Tool shadow_armed -Status
#>
[CmdletBinding()]
param(
  [ValidateSet('shadow_armed', 'stabilite')][string]$Tool = 'shadow_armed',
  [string]$Since,
  [int]$Hours = 24,
  [int]$EveryMinutes = 15,
  [ValidateSet('S4U', 'Interactive')][string]$Logon = 'S4U',
  [switch]$Apply,
  [switch]$Remove,
  [switch]$Status
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$py = 'C:\Users\houss\AppData\Local\Programs\Python\Python312\python.exe'
if (-not (Test-Path $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
$state = 'C:\Users\houss\helios-state\observation'
$name = "HELIOS-Observe24h-$Tool"
if (-not $Since) { $Since = Get-Date -Format 'yyyy-MM-dd HH:mm:ss' }
$sinceDt = [datetime]::ParseExact($Since, 'yyyy-MM-dd HH:mm:ss', $null)
$untilDt = $sinceDt.AddHours($Hours)

switch ($Tool) {
  'shadow_armed' { $script = Join-Path $repo 'infra\helios-sot\tools\shadow_observe.py'; $toolArgs = '--armed --since "{0}"' -f $Since }
  'stabilite'    { $script = Join-Path $repo 'infra\helios-sot\tools\passive_observe.py'; $toolArgs = '--since "{0}" --until "{1}"' -f $Since, $untilDt.ToString('yyyy-MM-dd HH:mm:ss') }
}
if (-not (Test-Path $script)) { throw "outil introuvable : $script" }
$argument = '-I "{0}" {1}' -f $script, $toolArgs

if ($Status) {
  $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if (-not $t) { "tâche $name : ABSENTE"; return }
  $i = Get-ScheduledTaskInfo -TaskName $name
  "tâche $name : état=$($t.State) | dernier lancement=$($i.LastRunTime) | dernier résultat=$($i.LastTaskResult) | prochain lancement=$($i.NextRunTime) | manqués=$($i.NumberOfMissedRuns)"
  return
}

"=== $name — $(if ($Remove) { 'RETRAIT' } else { 'ENREGISTREMENT' }) — $(if ($Apply) { 'APPLICATION' } else { 'DRY-RUN (rien n''est écrit)' }) ==="
if ($Remove) {
  $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if (-not $t) { "tâche absente : rien à retirer"; return }
  if ($Apply) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; "tâche retirée (relevés conservés)" } else { "serait retirée : $name" }
  return
}
"exécutable : $py"
"arguments  : $argument"
"fenêtre    : de $Since à $($untilDt.ToString('yyyy-MM-dd HH:mm:ss')) ($Hours h), un relevé toutes les $EveryMinutes min"
"compte     : $env:USERDOMAIN\$env:USERNAME ($Logon, niveau Limited : aucun droit supplémentaire)"
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { throw "la tâche $name existe déjà : la retirer d'abord (-Remove -Apply)" }
if (-not $Apply) { "DRY-RUN terminé : ajouter -Apply pour enregistrer"; return }

$action = New-ScheduledTaskAction -Execute $py -Argument $argument -WorkingDirectory $repo
$first = (Get-Date).AddMinutes(1)
$trigger = New-ScheduledTaskTrigger -Once -At $first -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) -RepetitionDuration (New-TimeSpan -Hours $Hours -Minutes 20)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 12)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType $Logon -RunLevel Limited
$desc = "HELIOS observation passive 24 h ($Tool) du $Since au $($untilDt.ToString('yyyy-MM-dd HH:mm:ss')) — LECTURE SEULE, aucune action opérationnelle"
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $desc | Out-Null
New-Item -ItemType Directory -Force -Path $state | Out-Null
[pscustomobject]@{ task = $name; tool = $Tool; since = $Since; until = $untilDt.ToString('yyyy-MM-dd HH:mm:ss'); every_min = $EveryMinutes
  registered = (Get-Date).ToString('o'); logon = $Logon; argument = $argument } | ConvertTo-Json | Set-Content -Path (Join-Path $state "registration-$Tool.json") -Encoding UTF8
$i = Get-ScheduledTaskInfo -TaskName $name
"tâche enregistrée : prochain lancement $($i.NextRunTime)"
