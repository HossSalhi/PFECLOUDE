# =============================================================
# reparer-podinfo.ps1
# Remet podinfo sain (reactive readyz + 2 repliques).
# A utiliser pour REINITIALISER entre 2 repetitions de la demo.
# Usage : .\reparer-podinfo.ps1
# =============================================================
$ns = "pfe"
$port = 19898

$pod = (kubectl get pod -n $ns -l app=podinfo -o jsonpath='{.items[0].metadata.name}')
Write-Host "Reactivation de readyz sur $pod..." -ForegroundColor Cyan
$pf = Start-Process kubectl -ArgumentList "port-forward","-n",$ns,"pod/$pod","${port}:9898" -PassThru -WindowStyle Hidden
$listening = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    if (Test-NetConnection -ComputerName localhost -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue) { $listening = $true; break }
}
if ($listening) {
    try {
        Invoke-WebRequest -Method POST -Uri "http://localhost:$port/readyz/enable" -UseBasicParsing -TimeoutSec 5 | Out-Null
        Write-Host "readyz REACTIVE (HTTP 200)" -ForegroundColor Green
    } catch { Write-Host "(le pod va etre recree de toute facon)" -ForegroundColor DarkGray }
}
if ($pf -and -not $pf.HasExited) { Stop-Process -Id $pf.Id -Force -ErrorAction SilentlyContinue }

Write-Host "Retour a 2 repliques..." -ForegroundColor Cyan
kubectl scale deploy podinfo -n $ns --replicas=2 | Out-Null
kubectl rollout status deploy podinfo -n $ns --timeout=90s | Out-Null
Write-Host ">>> podinfo est sain (2/2)." -ForegroundColor Green
