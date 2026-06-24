param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("1","2","3","4","5","6","7","8","9","10","11","nginx","cleanup")]
    [string]$Test,
    [string]$NodeIP   = "192.168.148.158",
    [string]$NodeName = "k8s-worker1"
)

function Sep  { Write-Host ("=" * 60) -ForegroundColor DarkGray }
function OK   { param($m) Write-Host "  [OK] $m"   -ForegroundColor Green  }
function INFO { param($m) Write-Host "  [..] $m"   -ForegroundColor Cyan   }
function WARN { param($m) Write-Host "  [!!] $m"   -ForegroundColor Yellow }
function WAIT {
    Write-Host ""
    Write-Host "  -> Attendre detection HELIOS (~2 min)" -ForegroundColor Yellow
    Write-Host "  -> Ouvrir http://192.168.148.153:30880/incidents" -ForegroundColor Cyan
}

Sep

if ($Test -eq "1") {
    Write-Host "TEST 1 -- Pod Kill (detection + auto-resolution HELIOS)" -ForegroundColor Magenta
    Sep

    # Prereqs
    $pod = kubectl get pods -n pfe -l app=podinfo -o jsonpath='{.items[0].metadata.name}' 2>$null
    if (-not $pod) {
        WARN "Aucun pod podinfo trouve dans le namespace pfe."
        Write-Host "  Verifier : kubectl get pods -n pfe" -ForegroundColor DarkCyan
        exit 1
    }

    $bp1 = kubectl get pods -n pfe -l app=backend -o jsonpath='{.items[0].metadata.name}' 2>$null
    $bp2 = kubectl get pods -n pfe -l app=backend -o jsonpath='{.items[1].metadata.name}' 2>$null
    $backendPods = @($bp1, $bp2) | Where-Object { $_ }
    if (-not $backendPods) {
        WARN "Aucun pod backend HELIOS trouve dans le namespace pfe."
        exit 1
    }

    INFO "Pod podinfo cible  : $pod"
    INFO "Pods backend HELIOS: $($backendPods -join ', ')"
    Write-Host ""

    # Kill
    Write-Host "  >>> SUPPRESSION FORCEE DU POD <<<" -ForegroundColor Red
    kubectl delete pod $pod -n pfe --grace-period=0 --force 2>$null
    $killTime = Get-Date
    OK "Pod $pod supprime - K8s va recreer en ~6s"

    Start-Sleep -Seconds 4
    $newPod = kubectl get pods -n pfe -l app=podinfo -o jsonpath='{.items[0].metadata.name}' 2>$null
    if ($newPod -and $newPod -ne $pod) { INFO "Nouveau pod : $newPod" }

    Write-Host ""
    INFO "Polling HELIOS toutes les 10s (detection max 3 min)..."

    # Phase 1 : attente creation incident
    $incidentId   = $null
    $incidentType = $null
    $deadline1    = $killTime.AddMinutes(3)

    while ((Get-Date) -lt $deadline1 -and -not $incidentId) {
        Start-Sleep -Seconds 10
        $elapsed = [int]((Get-Date) - $killTime).TotalSeconds
        $since   = ($elapsed + 15).ToString() + "s"

        foreach ($bp in $backendPods) {
            $logs = kubectl logs -n pfe $bp --since=$since 2>$null
            $line = ($logs | Where-Object { $_ -match "Incident created id=" }) | Select-Object -Last 1
            if ($line -and ($line -match 'id=(\d+)')) {
                $incidentId = $Matches[1]
                if ($line -match 'type=(\S+)') { $incidentType = $Matches[1] }
                break
            }
        }

        if (-not $incidentId) {
            Write-Host ("  [..] +{0}s - en attente detection HELIOS..." -f $elapsed) -ForegroundColor DarkGray
        }
    }

    if (-not $incidentId) {
        Write-Host ""
        Write-Host "  [FAIL] Aucun incident cree en 3 minutes." -ForegroundColor Red
        WARN "Debug :"
        Write-Host "    kubectl logs -n pfe $($backendPods[0]) --since=5m | findstr replacement" -ForegroundColor DarkCyan
        Write-Host "    kubectl logs -n pfe $($backendPods[0]) --since=5m | findstr override" -ForegroundColor DarkCyan
        exit 1
    }

    $elapsed1 = [int]((Get-Date) - $killTime).TotalSeconds
    Write-Host ""
    Write-Host ("  [OK] INCIDENT #{0} DETECTE en +{1}s  (type={2})" -f $incidentId, $elapsed1, $incidentType) -ForegroundColor Green

    # Phase 2 : attente auto-resolution
    Write-Host ""
    INFO "Attente auto-resolution (max 5 min)..."

    $resolved  = $false
    $deadline2 = $killTime.AddMinutes(5)

    while ((Get-Date) -lt $deadline2 -and -not $resolved) {
        Start-Sleep -Seconds 15
        $elapsed = [int]((Get-Date) - $killTime).TotalSeconds
        $since   = ($elapsed + 15).ToString() + "s"

        foreach ($bp in $backendPods) {
            $logs    = kubectl logs -n pfe $bp --since=$since 2>$null
            $resLine = $logs | Where-Object { $_ -match "Auto-resolved incident id=$incidentId" }
            if ($resLine) { $resolved = $true; break }
        }

        if (-not $resolved) {
            Write-Host ("  [..] +{0}s - incident #{1} IN_PROGRESS..." -f $elapsed, $incidentId) -ForegroundColor DarkGray
        }
    }

    Write-Host ""
    Sep
    if ($resolved) {
        $elapsed2 = [int]((Get-Date) - $killTime).TotalSeconds
        Write-Host ("  [PASS] Incident #{0} RESOLVED en {1}s total" -f $incidentId, $elapsed2) -ForegroundColor Green
        Write-Host ("  Dashboard : http://192.168.148.153:30880/incidents/{0}" -f $incidentId) -ForegroundColor Cyan
    } else {
        Write-Host ("  [FAIL] Incident #{0} non resolu apres 5 min" -f $incidentId) -ForegroundColor Red
        WARN "Verifier Camunda : http://192.168.148.161:8080/camunda/app/cockpit"
        WARN "Verifier logs    : kubectl logs -n pfe $($backendPods[0]) --since=10m | findstr $incidentId"
        exit 1
    }
}

elseif ($Test -eq "2") {
    Write-Host "TEST 2 -- CrashLoopBackOff (pod instable)" -ForegroundColor Magenta
    INFO "Deploie 3 apps defectueuses. HELIOS detecte POD_CRASH_LOOP + DEPLOYMENT_UNAVAILABLE."
    kubectl apply -f demo-incident-apps.yaml
    OK "Apps creees"
    WAIT
    WARN "Nettoyage manuel apres observation :"
    Write-Host "  kubectl delete -f demo-incident-apps.yaml" -ForegroundColor DarkCyan
}

elseif ($Test -eq "3") {
    Write-Host "TEST 3 -- Scale a 0 (service totalement indisponible)" -ForegroundColor Magenta
    INFO "Reduit podinfo a 0 replicas. HELIOS detecte DEPLOYMENT_UNAVAILABLE."
    kubectl scale deployment podinfo -n pfe --replicas=0
    OK "podinfo scale a 0"
    WAIT
    WARN "Si HELIOS ne scale pas auto, forcer manuellement :"
    Write-Host "  kubectl scale deployment podinfo -n pfe --replicas=2" -ForegroundColor DarkCyan
}

elseif ($Test -eq "4") {
    Write-Host "TEST 4 -- CPU Stress sur worker (saturation CPU)" -ForegroundColor Magenta
    INFO "Lance 4 processus CPU sur ${NodeIP} via SSH (60 secondes)"
    INFO "stress-ng absent sur les noeuds -> utilise yes > /dev/null"
    $bashCmd = 'for i in 1 2 3 4; do yes > /dev/null & done; sleep 60; kill $(jobs -p) 2>/dev/null; echo DONE'
    Start-Job -ScriptBlock { param($ip,$cmd) ssh kube@$ip $cmd } -ArgumentList $NodeIP,$bashCmd | Out-Null
    OK "CPU stress demarre sur ${NodeIP} (60s)"
    INFO "Observer CPU dans Grafana : http://192.168.148.160:3000"
    WAIT
}

elseif ($Test -eq "5") {
    Write-Host "TEST 5 -- Memory Exhaustion (OOMKill)" -ForegroundColor Magenta
    INFO "Reduit la limite memoire de podinfo a 16Mi -> OOMKilled deterministe"
    $patch = '[{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/memory","value":"16Mi"}]'
    kubectl patch deployment podinfo -n pfe --type=json -p $patch
    OK "Limite memoire reduite a 16Mi -- pod va OOMKill et redemarrer"
    WAIT
    WARN "Restaurer apres le test :"
    Write-Host "  .\scripts\helios-tests\run-all-tests.ps1 -Test cleanup" -ForegroundColor DarkCyan
}

elseif ($Test -eq "6") {
    Write-Host "TEST 6 -- Disk Pressure (remplissage disque)" -ForegroundColor Magenta
    INFO "Cree un fichier de 3 GB sur ${NodeIP} -> pression disque -> eviction kubelet"
    WARN "Nettoyage automatique apres 120s"
    $bashCmd = 'fallocate -l 3G /tmp/bigfile && echo Fichier cree && sleep 120 && rm -f /tmp/bigfile && echo Nettoye'
    Start-Job -ScriptBlock { param($ip,$cmd) ssh kube@$ip $cmd } -ArgumentList $NodeIP,$bashCmd | Out-Null
    OK "Remplissage disque lance sur ${NodeIP}"
    WAIT
    Write-Host "  Critere : pods evicted sur ${NodeName} replannifies sur l'autre worker" -ForegroundColor Green
}

elseif ($Test -eq "7") {
    Write-Host "TEST 7 -- HTTP Stress Nginx (charge excessive)" -ForegroundColor Magenta
    INFO "Lance stress_nginx.py : 200 workers, 3 minutes"
    kubectl get deployment nginx-test -n pfe 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        WARN "nginx-test non deploye -- deploiement en cours..."
        kubectl apply -f scripts\helios-tests\nginx-test.yaml
        Start-Sleep -Seconds 20
    }
    $url = "http://${NodeIP}:31080"
    INFO "URL cible : $url"
    INFO "Ouvrir http://192.168.148.153:30880/incidents dans un autre onglet"
    python scripts\helios-tests\stress_nginx.py --url $url --workers 200 --duration 180 --output stress_results.csv
    OK "Stress test termine -- voir stress_results.csv"
    Write-Host "  Critere : HELIOS detecte degradation -> SCALE_DEPLOYMENT ou RESTART_POD -> RESOLVED" -ForegroundColor Green
}

elseif ($Test -eq "8") {
    Write-Host "TEST 8 -- Network Failure (30% packet loss)" -ForegroundColor Magenta
    INFO "Injecte 30% packet loss sur interface eth0 de ${NodeIP} via tc netem (120s)"
    WARN "Restauration automatique apres 120s"
    $bashCmd = 'iface=$(ip route | grep default | awk "{print \$5}"); echo Interface: $iface; sudo tc qdisc add dev $iface root netem loss 30%; sleep 120; sudo tc qdisc del dev $iface root 2>/dev/null; echo Restaure'
    Start-Job -ScriptBlock { param($ip,$cmd) ssh kube@$ip $cmd } -ArgumentList $NodeIP,$bashCmd | Out-Null
    OK "Packet loss 30% injecte sur ${NodeIP} (120s)"
    WAIT
}

elseif ($Test -eq "9") {
    Write-Host "TEST 9 -- Database Down (CNPG failover)" -ForegroundColor Magenta
    $primary = kubectl get cluster postgres -n pfe -o jsonpath='{.status.currentPrimary}' 2>$null
    INFO "Primary actuel : $primary"
    WARN "Verifier CNPG Running AVANT de continuer :"
    Write-Host "  kubectl get pods -n cnpg-system" -ForegroundColor DarkCyan
    $confirm = Read-Host "  CNPG pods Running ? (o/n)"
    if ($confirm -ne "o") { Write-Host "Test annule" -ForegroundColor Red; exit 0 }
    kubectl delete pod $primary -n pfe --grace-period=0 --force
    OK "Pod primary $primary supprime -> CNPG failover automatique"
    Write-Host "  Verifier nouveau primary :"
    Write-Host "  kubectl get cluster postgres -n pfe -o jsonpath='{.status.currentPrimary}'" -ForegroundColor DarkCyan
    WAIT
    Write-Host "  Critere : 0 coupure observable, nouveau primary elu, backend reconnecte" -ForegroundColor Green
}

elseif ($Test -eq "10") {
    Write-Host "TEST 10 -- Node Drain (worker down)" -ForegroundColor Magenta
    WARN "Test critique -- 1 seul worker a la fois. JAMAIS les 2 en meme temps."
    WARN "Verifier CNPG avant : kubectl get pods -n cnpg-system"
    $confirm = Read-Host "  Continuer ? (o/n)"
    if ($confirm -ne "o") { Write-Host "Test annule" -ForegroundColor Red; exit 0 }
    INFO "Cordon + drain ${NodeName} (pods migres vers worker2)"
    kubectl cordon $NodeName
    kubectl drain $NodeName --ignore-daemonsets --delete-emptydir-data --force
    OK "${NodeName} cordonne et draine"
    Start-Sleep -Seconds 30
    kubectl get pods -n pfe -o wide
    WARN "Restaurer le noeud apres le test :"
    Write-Host "  kubectl uncordon $NodeName" -ForegroundColor DarkCyan
    WAIT
}

elseif ($Test -eq "11") {
    Write-Host "TEST 11 -- DNS Failure (simulation CoreDNS)" -ForegroundColor Magenta
    WARN "Ce test redefinit temporairement CoreDNS (60s) -> risque DNS interne"
    $confirm = Read-Host "  Confirmer ? (o/n)"
    if ($confirm -ne "o") { Write-Host "Test annule" -ForegroundColor Red; exit 0 }
    kubectl get configmap coredns -n kube-system -o yaml | Out-File -FilePath "$env:TEMP\coredns-backup.yaml" -Encoding utf8
    $badCorefile = ".:53 {`n    errors`n    health`n    forward . 192.0.2.1`n    cache 30`n    loop`n    reload`n    loadbalance`n}`n"
    kubectl patch configmap coredns -n kube-system --type=merge -p "{`"data`":{`"Corefile`":`"$badCorefile`"}}"
    kubectl rollout restart deployment/coredns -n kube-system
    OK "DNS failure injecte -- attente 60s"
    Start-Sleep -Seconds 60
    kubectl apply -f "$env:TEMP\coredns-backup.yaml"
    kubectl rollout restart deployment/coredns -n kube-system
    OK "CoreDNS restaure"
    WAIT
}

elseif ($Test -eq "nginx") {
    Write-Host "TEST NGINX -- Scenario complet (deploy + stress + detection)" -ForegroundColor Magenta
    INFO "Etape 1 : Deploiement nginx-test dans namespace pfe"
    kubectl apply -f scripts\helios-tests\nginx-test.yaml
    kubectl rollout status deployment/nginx-test -n pfe --timeout=60s
    OK "nginx-test deploye (2/2)"
    $url = "http://${NodeIP}:31080"
    INFO "Etape 2 : Stress test 200 workers / 3 min sur $url"
    INFO "Ouvrir http://192.168.148.153:30880/incidents dans un autre onglet"
    python scripts\helios-tests\stress_nginx.py --url $url --workers 200 --duration 180 --output stress_results.csv
    OK "Stress test termine -- voir stress_results.csv"
    Write-Host ""
    Write-Host "  Criteres de succes HELIOS :" -ForegroundColor Green
    Write-Host "    1. Incident nginx-test detecte dans l UI" -ForegroundColor White
    Write-Host "    2. Statut passe IN_PROGRESS automatiquement (sans clic)" -ForegroundColor White
    Write-Host "    3. Action RESTART_POD ou SCALE_DEPLOYMENT executee par Camunda" -ForegroundColor White
    Write-Host "    4. Statut RESOLVED apres verification" -ForegroundColor White
}

elseif ($Test -eq "cleanup") {
    Write-Host "CLEANUP -- Nettoyage de tous les tests" -ForegroundColor Magenta
    kubectl delete -f demo-incident-apps.yaml --ignore-not-found 2>$null
    kubectl delete deployment nginx-test   -n pfe --ignore-not-found 2>$null
    kubectl delete service    nginx-test   -n pfe --ignore-not-found 2>$null
    kubectl delete pdb        nginx-test-pdb -n pfe --ignore-not-found 2>$null
    kubectl scale deployment podinfo -n pfe --replicas=2 2>$null
    $patchMem = '[{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/memory","value":"128Mi"}]'
    kubectl patch deployment podinfo -n pfe --type=json -p $patchMem 2>$null
    kubectl uncordon k8s-worker1 2>$null
    kubectl uncordon k8s-worker2 2>$null
    ssh kube@192.168.148.158 'rm -f /tmp/bigfile 2>/dev/null; iface=$(ip route | grep default | awk "{print \$5}"); sudo tc qdisc del dev $iface root 2>/dev/null; echo worker1 OK'
    ssh kube@192.168.148.155 'rm -f /tmp/bigfile 2>/dev/null; echo worker2 OK'
    OK "Nettoyage complet"
    kubectl get pods -n pfe
}

Sep
