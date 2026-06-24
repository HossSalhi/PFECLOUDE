<#
=====================================================================
 HELIOS CHAOS TOOLKIT - point d'entree unique pour TOUS les tests
 (applicatifs + techniques). Cible : podinfo dans le namespace pfe.

 100% kubectl local + busybox embarque dans podinfo + NetworkPolicy
 Calico. AUCUNE image a telecharger, AUCUN Chaos Mesh, air-gap safe.

 USAGE :
   .\helios-chaos.ps1 <test>            # injecte la panne
   .\helios-chaos.ps1 <test> -Restore   # repare / nettoie
   .\helios-chaos.ps1 list              # liste les tests
   .\helios-chaos.ps1 status            # etat podinfo + incidents

 EXEMPLES :
   .\helios-chaos.ps1 cpu
   .\helios-chaos.ps1 network -Restore
=====================================================================
#>
param(
    [Parameter(Position=0)] [string]$Test = "list",
    [switch]$Restore
)

$ErrorActionPreference = "Stop"
$NS  = "pfe"
$APP = "podinfo"
$PORT = 9898

function Get-Pod { (kubectl get pod -n $NS -l "app=$APP" -o jsonpath='{.items[0].metadata.name}') }
function Get-AllPods { (kubectl get pod -n $NS -l "app=$APP" -o jsonpath='{.items[*].metadata.name}') -split '\s+' | Where-Object { $_ } }
function Get-NodeOf { param($pod) (kubectl get pod -n $NS $pod -o jsonpath='{.spec.nodeName}') }
function Info($m){ Write-Host $m -ForegroundColor Cyan }
function Ok($m){ Write-Host $m -ForegroundColor Green }
function Warn($m){ Write-Host $m -ForegroundColor Yellow }

function Exec-OnPod {
    param($pod, $shScript)
    kubectl exec -n $NS $pod -- sh -c $shScript
}
function Exec-OnAll {
    param($shScript)
    foreach ($p in Get-AllPods) { try { kubectl exec -n $NS $p -- sh -c $shScript 2>$null } catch {} }
}
function Apply-NetPol { param($yaml) $yaml | kubectl apply -f - }
function Del-NetPol { param($name) kubectl delete networkpolicy $name -n $NS --ignore-not-found | Out-Null }

# Programme une auto-restauration (processus detache qui survit a la fin du script) :
# leve la panne apres $delaySec, PENDANT la verification HELIOS -> HELIOS constate le
# retablissement et RESOUT l'incident lui-meme. -> une seule commande par test.
function Schedule-SelfRestore {
    param([string]$test, [int]$delaySec = 50)
    # DESACTIVE (integrite). Avant, ceci enlevait la panne PENDANT la fenetre de verification de
    # HELIOS -> HELIOS "constatait" un retablissement qu'il n'avait PAS provoque = faux RESOLVED.
    # Desormais la panne PERSISTE : HELIOS doit la corriger lui-meme (ex. rollback) ou escalader
    # honnetement. Le teardown (-Restore) est fait par le runner APRES le verdict de HELIOS, ou
    # manuellement : .\helios-chaos.ps1 $test -Restore
    Write-Host "  (panne PERSISTANTE -> HELIOS doit la corriger lui-meme ; teardown via -Restore APRES le verdict)" -ForegroundColor DarkGray
}

# PowerShell mange les guillemets du JSON passe en argument natif -> on ecrit dans un fichier temp.
function Patch-Deploy {
    param($type, $json)
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $json)
    kubectl patch deployment $APP -n $NS --type=$type --patch-file $tmp
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}

function Restore-Resources {
    # CPU limite a 1200m (baseline chaos) : permet au test cpu de saturer sans rollout.
    kubectl set resources deployment $APP -n $NS --limits=cpu=1200m,memory=128Mi --requests=cpu=20m,memory=32Mi | Out-Null
}
function Remove-CommandOverride {
    $patch = '[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]'
    Patch-Deploy "json" $patch 2>$null | Out-Null
}

switch ($Test.ToLower()) {

# ================= TESTS APPLICATIFS =================

  "podkill" {
    if ($Restore) { Ok "Rien a restaurer (K8s recree le pod automatiquement)."; break }
    Info "[APP] Pod kill - suppression d'un pod podinfo"
    kubectl delete pod -n $NS -l "app=$APP"
    Warn ">>> Attendu : incident POD_CRASH_LOOP (HIGH) -> RESTART_POD -> RESOLVED ~30s"
  }

  "crashloop" {
    if ($Restore) { Info "Restauration..."; Remove-CommandOverride; kubectl rollout status deploy/$APP -n $NS --timeout=90s; Ok "podinfo restaure."; break }
    Info "[APP] Crash loop - le conteneur sort en erreur immediatement (pods JAMAIS Ready)"
    $patch = '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","echo boot; exit 1"]}]'
    Patch-Deploy "json" $patch
    Schedule-SelfRestore "crashloop" 50
    Warn ">>> Attendu : CrashLoopBackOff -> POD_CRASH_LOOP -> RESOLVED (auto-restauration)."
  }

  "eviction" {
    $pod = Get-Pod; $node = Get-NodeOf $pod
    if ($Restore) { Info "Uncordon des workers"; kubectl uncordon k8s-worker1 k8s-worker2 2>$null; Ok "Workers de nouveau schedulables."; break }
    # IMPORTANT : on NE taint PAS le noeud (NoExecute evincerait TOUS les pods, dont postgres/backend
    # -> coupure DB/UI). On cible UNIQUEMENT podinfo : cordon (pas de nouveau pod sur ce noeud) +
    # suppression du pod podinfo de ce noeud -> il est reschedule sur l'autre worker.
    Info "[APP] Pod eviction - cordon $node + eviction du pod podinfo de ce noeud (reschedule cible)"
    kubectl cordon $node
    kubectl delete pod -n $NS -l "app=$APP" --field-selector "spec.nodeName=$node"
    Schedule-SelfRestore "eviction" 50
    Warn ">>> Attendu : podinfo migre -> RESOLVED, puis uncordon auto (auto-restauration)."
  }

  "container" {
    if ($Restore) { Ok "Rien a restaurer (le conteneur redemarre via restartPolicy)."; break }
    # Un seul kill = 1 restart, trop bref et < seuil (POD_CRASH_LOOP exige >=3 restarts).
    # On tue PID 1 trois fois (avec pause pour laisser redemarrer) -> 3 restarts dans la fenetre
    # -> POD_CRASH_LOOP detecte de facon fiable.
    Info "[APP] Container failure - 3 kills successifs du process principal (PID 1)"
    for ($i = 1; $i -le 3; $i++) {
        $pod = Get-Pod
        Write-Host "  kill #$i sur $pod" -ForegroundColor DarkGray
        kubectl exec -n $NS $pod -- kill 1 2>$null
        if ($i -lt 3) { Start-Sleep -Seconds 12 }
    }
    Warn ">>> Attendu : 3 restarts -> POD_CRASH_LOOP -> RESOLVED (~1-2min). (auto)"
  }

  "service" {
    if ($Restore) { Del-NetPol "helios-chaos-service"; Ok "NetworkPolicy supprimee - service de nouveau joignable."; break }
    Info "[APP] Service failure - podinfo n'est plus joignable par ses clients (deny ingress)"
    # Deny ingress => la sonde active du backend (backend->podinfo) echoue => DEPLOYMENT_UNAVAILABLE.
    Apply-NetPol @"
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: helios-chaos-service
  namespace: $NS
spec:
  podSelector:
    matchLabels:
      app: $APP
  policyTypes: [Ingress]
"@
    Schedule-SelfRestore "service" 50
    Warn ">>> Attendu : podinfo injoignable -> DEPLOYMENT_UNAVAILABLE -> RESOLVED (auto-restauration)."
  }

  "node" {
    $pod = Get-Pod; $node = Get-NodeOf $pod
    if ($Restore) { Info "Uncordon des workers"; kubectl uncordon k8s-worker1 k8s-worker2; Ok "Workers de nouveau schedulables."; break }
    # SECURISE : on NE drain PAS le noeud (un drain evince postgres/backend/frontend -> casse DB/UI,
    # car postgres a un volume LOCAL epingle au noeud). On cordon + on evince UNIQUEMENT podinfo ->
    # il migre sur l'autre worker. Demontre "noeud indisponible -> pod migre" sans toucher la DB.
    Info "[APP] Node failure (securise) - cordon $node + migration de podinfo (DB/backend intacts)"
    kubectl cordon $node
    kubectl delete pod -n $NS -l "app=$APP" --field-selector "spec.nodeName=$node"
    Schedule-SelfRestore "node" 50
    Warn ">>> Attendu : podinfo migre -> RESOLVED, puis uncordon auto (auto-restauration)."
  }

  "hpa" {
    if ($Restore) {
        Info "Suppression HPA + arret du stress CPU..."
        kubectl delete hpa $APP -n $NS --ignore-not-found | Out-Null
        Exec-OnAll "killall yes 2>/dev/null; true"
        kubectl scale deployment $APP -n $NS --replicas=2 | Out-Null
        Ok "HPA supprime, charge stoppee, podinfo a 2 repliques."
        break
    }
    Info "[APP] HPA scaling under load - autoscaler K8s (metrics-server) scale podinfo sous charge CPU"
    # HPA sur CPU : cible 60% de la request (20m) -> toute charge soutenue scale jusqu'a max=5.
    kubectl autoscale deployment $APP -n $NS --cpu-percent=60 --min=2 --max=5
    $pod = Get-Pod
    Exec-OnAll 'nohup yes >/dev/null 2>&1 & echo "charge CPU lancee"'
    Schedule-SelfRestore "hpa" 60
    Warn ">>> Attendu : HPA scale podinfo 2 -> N, CPU_STRESS detecte -> RESOLVED (auto-restauration)."
  }

# ================= TESTS TECHNIQUES =================

  "cpu" {
    if ($Restore) {
        Info "Arret du stress CPU (killall yes sur toutes les repliques)..."
        Exec-OnAll "killall yes 2>/dev/null; true"
        Ok "CPU libere (limite chaos 1200m conservee, pas de rollout)."
        break
    }
    Info "[TECH] CPU stress - saturation CPU auto-terminante (~110s) sur toutes les repliques"
    # PREREQUIS : limite CPU = 1200m (baseline chaos). Sinon lancer d'abord : helios-chaos.ps1 setup
    # Stress TRANSITOIRE : yes + watchdog qui le tue apres 110s -> HELIOS detecte CPU_STRESS
    # puis la verification voit le CPU redescendu -> RESOLVED (cycle complet).
    Exec-OnAll 'nohup yes >/dev/null 2>&1 & nohup sh -c "sleep 110; killall yes" >/dev/null 2>&1 & echo "stress lance (auto-stop 110s)"'
    Warn ">>> Attendu : cpu_usage_cores ~1.0 -> CPU_STRESS -> RESOLVED (~2min). Stop immediat : .\helios-chaos.ps1 cpu -Restore"
  }

  "memory" {
    if ($Restore) { Info "Arret du hog memoire..."; Exec-OnAll "killall tail 2>/dev/null; true"; Ok "Hog arrete (pod deja recree par K8s apres OOM)."; break }
    Info "[TECH] Memory exhaustion (OOM) - allocateur memoire (tail /dev/zero) depasse la limite 128Mi -> OOMKilled"
    # podinfo survit a une limite basse ; on NE change PAS la limite (ca declencherait un rollout
    # -> DEPLOYMENT_UNAVAILABLE avant l'OOM). On lance un allocateur non borne (tail bufferise
    # /dev/zero) dans les pods existants -> depasse 128Mi -> OOMKilled deterministe (sans rollout).
    Exec-OnAll 'nohup tail /dev/zero >/dev/null 2>&1 & echo "memory hog lance"'
    Warn ">>> Attendu : OOMKilled -> MEMORY_STRESS -> rollout restart."
  }

  "disk" {
    $pod = Get-Pod
    if ($Restore) { Info "Nettoyage du disque..."; Exec-OnAll "rm -f /tmp/helios-fill 2>/dev/null; true"; Ok "Fichier supprime (ou pod evince/recree)."; break }
    Info "[TECH] Disk pressure - remplissage de l emptyDir /tmp au-dela de sizeLimit (600Mo > 300Mi)"
    # PREREQUIS : emptyDir /tmp sizeLimit=300Mi (via 'setup'). Le depassement -> eviction kubelet.
    Exec-OnPod $pod "dd if=/dev/zero of=/tmp/helios-fill bs=1M count=600 2>&1 | tail -1"
    Warn ">>> Attendu : eviction kubelet (emptyDir) -> DEPLOYMENT_UNAVAILABLE -> RESOLVED (pod recree). Repare : .\helios-chaos.ps1 disk -Restore"
  }

  "http503" {
    if ($Restore) { Ok "Rien a restaurer (erreurs ponctuelles)."; break }
    $pod = Get-Pod
    Info "[TECH] HTTP 503 - 300 requetes en erreur depuis $pod vers lui-meme"
    Exec-OnPod $pod "i=0; while [ \`$i -lt 300 ]; do wget -q -O /dev/null http://localhost:$PORT/status/503 2>/dev/null; i=\`$((i+1)); done; echo done"
    Warn ">>> Attendu : http_requests{status=503} -> APPLICATION_ERROR_STORM / DEPLOYMENT_UNAVAILABLE"
  }

  "latency" {
    if ($Restore) { Ok "Rien a restaurer (latence ponctuelle)."; break }
    $pod = Get-Pod
    Info "[TECH] HTTP latency - 40 requetes lentes (/delay/5) depuis $pod"
    Exec-OnPod $pod "i=0; while [ \`$i -lt 40 ]; do wget -q -O /dev/null http://localhost:$PORT/delay/5 2>/dev/null; i=\`$((i+1)); done; echo done"
    Warn ">>> Attendu : latence p95 elevee -> HIGH_RESPONSE_TIME"
  }

  "network" {
    if ($Restore) { Del-NetPol "helios-chaos-network"; Ok "Partition reseau levee."; break }
    Info "[TECH] Network partition - deny TOTAL ingress+egress sur podinfo (Calico)"
    Apply-NetPol @"
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: helios-chaos-network
  namespace: $NS
spec:
  podSelector:
    matchLabels:
      app: $APP
  policyTypes: [Ingress, Egress]
"@
    Schedule-SelfRestore "network" 50
    Warn ">>> Attendu : pod isole -> DEPLOYMENT_UNAVAILABLE -> RESOLVED (auto-restauration)."
  }

  "dns" {
    if ($Restore) { Del-NetPol "helios-chaos-dns"; Ok "DNS retabli."; break }
    Info "[TECH] DNS failure - egress 53 bloque + trafic : podinfo ne resout plus son backend -> 5xx"
    # PREREQUIS : podinfo lance avec --backend-url=<hostname> (via 'setup').
    # NetworkPolicy : autorise tout l'egress SAUF le port 53 -> la resolution DNS echoue.
    Apply-NetPol @"
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: helios-chaos-dns
  namespace: $NS
spec:
  podSelector:
    matchLabels:
      app: $APP
  policyTypes: [Egress]
  egress:
    - to: []
      ports:
        - { protocol: TCP, port: 80 }
        - { protocol: TCP, port: 443 }
        - { protocol: TCP, port: 5432 }
        - { protocol: TCP, port: 8000 }
        - { protocol: TCP, port: 8080 }
        - { protocol: TCP, port: $PORT }
"@
    Schedule-SelfRestore "dns" 50
    Warn ">>> Attendu : readiness (nslookup) KO -> NotReady -> DEPLOYMENT_UNAVAILABLE -> RESOLVED (auto-restauration)."
  }

  "db" {
    if ($Restore) { Ok "Rien a restaurer (CNPG recree le pod / failover automatique)."; break }
    Warn "[TECH] Database down - suppression du pod primaire postgres (CNPG failover)"
    Warn "    PREREQUIS : verifier l operateur CNPG (kubectl get pods -n cnpg-system)"
    kubectl delete pod postgres-1 -n $NS
    Warn ">>> Attendu : failover CNPG + DB_CONNECTION_FAILURE -> RESTART_POD"
  }

# ================= UTILITAIRES =================

  "setup" {
    Info "Configuration baseline chaos : CPU 1200m + emptyDir /tmp 300Mi (disk) + readiness DNS (dns)"
    Restore-Resources
    # 1 seul patch combine -> 1 rollout :
    #  - emptyDir sizeLimit sur /tmp : depassement -> eviction kubelet (test disk)
    #  - readinessProbe exec nslookup : DNS coupe -> readiness echoue -> NotReady -> unavailable (test dns)
    $patch = '{"spec":{"template":{"spec":{"volumes":[{"name":"chaos-tmp","emptyDir":{"sizeLimit":"300Mi"}}],"containers":[{"name":"podinfo","args":[],"volumeMounts":[{"name":"chaos-tmp","mountPath":"/tmp"}],"readinessProbe":{"exec":{"command":["sh","-c","nslookup podinfo.pfe.svc.cluster.local"]},"httpGet":null,"periodSeconds":5,"failureThreshold":2,"initialDelaySeconds":5}}]}}}}'
    Patch-Deploy "strategic" $patch
    kubectl rollout status deploy/$APP -n $NS --timeout=90s
    Ok "Baseline chaos prete. Tu peux maintenant lancer n'importe quel test."
  }

  "status" {
    Info "=== podinfo ==="; kubectl get deploy $APP -n $NS
    kubectl get pods -n $NS -l "app=$APP" -o wide
    Info "=== NetworkPolicies chaos actives ==="
    $np = (kubectl get networkpolicy -n $NS -o name 2>$null | Select-String "helios-chaos")
    if ($np) { $np } else { Write-Host "  (aucune)" -ForegroundColor DarkGray }
  }

  default {
    Write-Host ""
    Write-Host "HELIOS CHAOS TOOLKIT - tests disponibles" -ForegroundColor Magenta
    Write-Host "----------------------------------------"
    Write-Host " APPLICATIFS :" -ForegroundColor Cyan
    Write-Host "   podkill     Pod kill (delete pod)             -> POD_CRASH_LOOP"
    Write-Host "   crashloop   Conteneur qui crash en boucle     -> POD_CRASH_LOOP"
    Write-Host "   eviction    Eviction via taint NoExecute       -> reschedule"
    Write-Host "   container   Kill du process principal (PID1)   -> restart"
    Write-Host "   service     Coupe les appels inter-services    -> SERVICE failure"
    Write-Host "   node        Cordon+drain d un worker (cle)     -> NODE"
    Write-Host "   hpa         Scaling sous charge (needs metrics-server)"
    Write-Host " TECHNIQUES :" -ForegroundColor Cyan
    Write-Host "   cpu         CPU stress (tous les coeurs)       -> HIGH_CPU_USAGE"
    Write-Host "   memory      OOM (limite 16Mi)                  -> POD_CRASH_LOOP"
    Write-Host "   disk        Disk pressure (/tmp 4Go)           -> eviction"
    Write-Host "   http503     Erreurs 503 en masse               -> ERROR_STORM"
    Write-Host "   latency     Latence HTTP (/delay)              -> HIGH_RESPONSE_TIME"
    Write-Host "   network     Partition reseau totale            -> SERVICE_UNAVAILABLE"
    Write-Host "   dns         Resolution DNS cassee              -> SERVICE_UNAVAILABLE"
    Write-Host "   db          Postgres primaire down (failover)  -> DB_CONNECTION_FAILURE"
    Write-Host ""
    Write-Host " USAGE : .\helios-chaos.ps1 <test> [-Restore]" -ForegroundColor Yellow
    Write-Host "         .\helios-chaos.ps1 status" -ForegroundColor Yellow
    Write-Host ""
  }
}
