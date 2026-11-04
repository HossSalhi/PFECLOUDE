# HA-01 Phase 2a — lance une répétition (p2a_rehearsal.py) en processus Windows DÉTACHÉ (fenêtre cachée).
# Pourquoi détaché : une commande `run_in_background` de Claude Code peut être TUÉE par le gestionnaire de mémoire de la session
# (le taint resterait posé) ; un processus Start-Process n'est pas concerné. Le filet systemd côté nœud (minuterie qui retire
# le taint) reste de toute façon armé par le script lui-même.
#
# Usage : .\start_p2a.ps1 -Node k8s-worker2 -Label w2 [-Switchover] [-Duration 600] [-Rps 20] [-DryRun]
#   -DryRun : préflight seul, aucune action sur le cluster.
# Sorties : results\2026-11-03\p2a-<Label>.out / .err / .pid  (suivi en direct)
#           results\2026-11-03\p2a-<Label>-<horodatage>\{run.log, preflight.json, timeline.jsonl, summary.md, summary.json, ...}
param(
  [Parameter(Mandatory=$true)][ValidateSet('k8s-worker1','k8s-worker2')][string]$Node,
  [Parameter(Mandatory=$true)][string]$Label,
  [switch]$Switchover,
  [switch]$DryRun,
  [int]$Duration = 600,
  [double]$Rps = 20,
  [string]$ResultsDir = (Join-Path $PSScriptRoot '..\results\2026-11-03')
)
$ErrorActionPreference = 'Stop'
$ResultsDir = (Resolve-Path $ResultsDir).Path
$py = (Get-Command python).Source
$env:PYTHONIOENCODING = 'utf-8'
$argList = @("`"$PSScriptRoot\p2a_rehearsal.py`"", '--node', $Node, '--label', $Label, '--duration', $Duration, '--rps', $Rps)
if ($Switchover) { $argList += '--switchover' }
if ($DryRun)     { $argList += '--dry-run' }
$out = Join-Path $ResultsDir "p2a-$Label.out"
$err = Join-Path $ResultsDir "p2a-$Label.err"
$p = Start-Process -FilePath $py -ArgumentList $argList -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
"$($p.Id)" | Set-Content -Path (Join-Path $ResultsDir "p2a-$Label.pid") -Encoding ascii
"Répétition '$Label' ($Node)$(if ($Switchover) {' + switchover'})$(if ($DryRun) {' [DRY-RUN]'}) lancée en processus détaché, PID $($p.Id)"
"Suivi : $out"
