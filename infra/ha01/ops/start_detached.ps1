# HA-01 — lance un script d'ops Python en processus Windows DÉTACHÉ (fenêtre cachée), sortie suivie dans results\2026-11-03\<Label>.out
# Pourquoi détaché : une commande `run_in_background` de Claude Code peut être TUÉE par le gestionnaire de mémoire de la session
# (la panne injectée resterait en place) ; un processus Start-Process n'est pas concerné. Les scripts posent de toute façon
# une minuterie de sécurité côté machine qui rétablit tout.
#
# Usage :  .\start_detached.ps1 -Script p2b_inject.py -Label i160 --mode isolate --lb 160 --label i160
#          .\start_detached.ps1 -Script p2b_enforce.py -Label enf162 --lb 162
# (tout ce qui suit -Label est passé tel quel au script Python)
# Suivi   : Get-Content results\2026-11-03\<Label>.out -Tail 30 ; PID dans <Label>.pid
param(
  [Parameter(Mandatory=$true)][string]$Script,
  [Parameter(Mandatory=$true)][string]$Label,
  [Parameter(ValueFromRemainingArguments=$true)][string[]]$ScriptArgs
)
$ErrorActionPreference = 'Stop'
$ResultsDir = (Resolve-Path (Join-Path $PSScriptRoot '..\results\2026-11-03')).Path
$py = (Get-Command python).Source
$env:PYTHONIOENCODING = 'utf-8'
$argList = @("`"$PSScriptRoot\$Script`"")
if ($ScriptArgs) { $argList += $ScriptArgs }      # sans arguments supplémentaires, `+ $null` rendait ArgumentList invalide
$out = Join-Path $ResultsDir "$Label.out"
$err = Join-Path $ResultsDir "$Label.err"
$p = Start-Process -FilePath $py -ArgumentList $argList -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
"$($p.Id)" | Set-Content -Path (Join-Path $ResultsDir "$Label.pid") -Encoding ascii
"Processus détaché '$Label' : $Script $($ScriptArgs -join ' ') -> PID $($p.Id)"
"Suivi : $out"
