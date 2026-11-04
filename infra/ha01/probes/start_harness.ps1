# HA-01 — démarre la sonde de disponibilité + l'échantillonneur d'état, détachés (fenêtre cachée).
# Usage : .\start_harness.ps1 -Label baseline-p0 [-ResultsDir ..\results\2026-11-03]
# Fichiers : <ResultsDir>\probe-<Label>.jsonl, samples-<Label>.jsonl, harness-<Label>.pids
param(
  [Parameter(Mandatory=$true)][string]$Label,
  [string]$ResultsDir = (Join-Path $PSScriptRoot '..\results\2026-11-03'),
  [double]$SampleInterval = 10
)
$ErrorActionPreference = 'Stop'
$ResultsDir = (Resolve-Path $ResultsDir).Path
$py = (Get-Command python).Source
$env:PYTHONIOENCODING = 'utf-8'
$probeOut   = Join-Path $ResultsDir "probe-$Label.jsonl"
$samplesOut = Join-Path $ResultsDir "samples-$Label.jsonl"
$p1 = Start-Process -FilePath $py -ArgumentList @("`"$PSScriptRoot\ha01_probe.py`"", '--out', "`"$probeOut`"") -WindowStyle Hidden -PassThru
$p2 = Start-Process -FilePath $py -ArgumentList @("`"$PSScriptRoot\ha01_sampler.py`"", '--out', "`"$samplesOut`"", '--interval', $SampleInterval) -WindowStyle Hidden -PassThru
"$($p1.Id) probe`n$($p2.Id) sampler" | Set-Content -Path (Join-Path $ResultsDir "harness-$Label.pids") -Encoding ascii
"Sonde PID $($p1.Id) -> $probeOut"
"Echantillonneur PID $($p2.Id) -> $samplesOut"
