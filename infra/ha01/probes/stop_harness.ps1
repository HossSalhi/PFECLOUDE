# HA-01 — arrête proprement le harnais (fichiers STOP-*, puis kill de secours).
# Usage : .\stop_harness.ps1 -Label baseline-p0 [-ResultsDir ..\results\2026-11-03]
param(
  [Parameter(Mandatory=$true)][string]$Label,
  [string]$ResultsDir = (Join-Path $PSScriptRoot '..\results\2026-11-03')
)
$ResultsDir = (Resolve-Path $ResultsDir).Path
foreach ($f in "probe-$Label.jsonl", "samples-$Label.jsonl") { New-Item -ItemType File -Force -Path (Join-Path $ResultsDir "STOP-$f") | Out-Null }
Start-Sleep -Seconds 14
$pidFile = Join-Path $ResultsDir "harness-$Label.pids"
if (Test-Path $pidFile) {
  foreach ($line in Get-Content $pidFile) {
    $id = [int]($line.Split(' ')[0])
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -like 'python*') { Stop-Process -Id $id -Force; "kill de secours PID $id" } else { "PID $id terminé proprement" }
  }
}
Get-ChildItem $ResultsDir -Filter 'STOP-*' | Remove-Item -Force -ErrorAction SilentlyContinue
