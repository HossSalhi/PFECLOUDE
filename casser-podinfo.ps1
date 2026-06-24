# =============================================================
# casser-podinfo.ps1
# Casse podinfo de facon REPARABLE (panne de readiness) pour la demo
# "Demarrer -> Camunda repare -> RESOLVED".
# Usage : .\casser-podinfo.ps1
# =============================================================
$ns = "pfe"
$port = 19898

Write-Host "1) podinfo a 1 replique (demo propre)..." -ForegroundColor Cyan
kubectl scale deploy podinfo -n $ns --replicas=1 | Out-Null

Write-Host "2) readinessProbe sur /readyz (idempotent, via fichier)..." -ForegroundColor Cyan
$json = '{"spec":{"template":{"spec":{"containers":[{"name":"podinfo","readinessProbe":{"httpGet":{"path":"/readyz","port":9898},"periodSeconds":5,"failureThreshold":2,"initialDelaySeconds":3}}]}}}}'
$tmp = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($tmp, $json)
kubectl patch deploy podinfo -n $ns --type strategic --patch-file $tmp | Out-Null
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
kubectl rollout status deploy podinfo -n $ns --timeout=90s | Out-Null

$pod = (kubectl get pod -n $ns -l app=podinfo -o jsonpath='{.items[0].metadata.name}')
Write-Host "   pod cible : $pod" -ForegroundColor DarkGray

Write-Host "3) ouverture du tunnel vers le pod..." -ForegroundColor Cyan
$pf = Start-Process kubectl -ArgumentList "port-forward","-n",$ns,"pod/$pod","${port}:9898" -PassThru -WindowStyle Hidden
# attendre que le port ecoute vraiment
$listening = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    if (Test-NetConnection -ComputerName localhost -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue) { $listening = $true; break }
}

$ok = $false
if ($listening) {
    Write-Host "4) desactivation de readyz..." -ForegroundColor Cyan
    for ($i = 0; $i -lt 6; $i++) {
        try {
            Invoke-WebRequest -Method POST -Uri "http://localhost:$port/readyz/disable" -UseBasicParsing -TimeoutSec 5 | Out-Null
            $ok = $true; break
        } catch { Start-Sleep -Seconds 2 }
    }
}
if ($pf -and -not $pf.HasExited) { Stop-Process -Id $pf.Id -Force -ErrorAction SilentlyContinue }

if ($ok) {
    Write-Host "   readyz DESACTIVE (HTTP 503)" -ForegroundColor Green
    Write-Host ""
    Write-Host ">>> podinfo passe 0/1 NotReady dans ~10s." -ForegroundColor Yellow
    Write-Host ">>> HELIOS cree un incident DEPLOYMENT_UNAVAILABLE dans ~2 min." -ForegroundColor Yellow
    Write-Host ">>> Page Incidents -> clique DEMARRER sur l'incident podinfo." -ForegroundColor Yellow
} else {
    Write-Host "   ECHEC tunnel. Relance : .\casser-podinfo.ps1" -ForegroundColor Red
}
