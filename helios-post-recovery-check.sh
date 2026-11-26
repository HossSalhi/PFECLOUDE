#!/usr/bin/env bash
# =============================================================================
# HELIOS — VALIDATION TERRAIN POST-RECUPERATION DE LA VM MONITORING (.160)
# =============================================================================
# RETRAIT DE L'IA (11 nov. 2026) : les phases 9c (journal du collecteur ML), 10 (dataset MVP) et 11 (reprise de la
# collecte) concernent le collecteur ML et le service IA, SUPPRIMÉS ; elles ne sont plus rejouables. Les phases 1-9
# (monitoring, Bridge) restent valables. Script ponctuel conservé pour l'historique (copie : helios-state\snapshots).
# =============================================================================
# STRICTEMENT LECTURE SEULE.
#   - ne supprime aucun fichier          - ne redemarre aucun service
#   - ne modifie aucun dataset           - ne repare aucun WAL
#   - ne touche aucun chunk Loki         - ne modifie aucune configuration
#   - n'execute aucun git, aucun deploiement
# Les seules operations distantes sont : lectures systemd/journalctl, GET HTTP,
# lecture de fichiers, et l'execution de detecter.py (qui ne fait que des
# requetes de monitoring et imprime un verdict JSON sur stdout).
#
# Usage :
#   export HELIOS_SSH_USER=kube
#   export HELIOS_SSH_PASS=kube
#   bash /c/Users/houss/IdeaProjects/PFE-Orange/helios-post-recovery-check.sh \
#        > /c/Users/houss/helios-post-recovery-rapport.txt 2>&1
# =============================================================================
set -uo pipefail

MON=192.168.148.160          # Prometheus / Loki / Grafana / detecter.py
AI=192.168.148.161           # bridge + collecteur + dataset MVP
U="${HELIOS_SSH_USER:-kube}"
P="${HELIOS_SSH_PASS:-kube}"
ATTENTE="${HELIOS_ATTENTE:-75}"

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o LogLevel=ERROR"

if command -v sshpass >/dev/null 2>&1; then
  MODE_SSH="sshpass"
  S() { sshpass -p "$P" ssh $SSH_OPTS "$U@$1" "$2" 2>&1; }
else
  MODE_SSH="ssh interactif (mot de passe demande a chaque appel)"
  S() { ssh $SSH_OPTS "$U@$1" "$2" 2>&1; }
fi

t() { printf '\n\n============================== %s ==============================\n' "$1"; }
kv() { printf '%-26s : %s\n' "$1" "$2"; }

R_SSH160=INCONNU; R_UPTIME=INCONNU; R_BOOT=INCONNU
R_PROM=INCONNU; R_LOKI=INCONNU; R_GRAF=INCONNU
R_PROM_READY=INCONNU; R_PROM_HEALTHY=INCONNU; R_LOKI_READY=INCONNU
R_GRAF_DB=INCONNU; R_WAL=INCONNU; R_LOKI_INTEG=INCONNU
R_TARGETS_UP=0; R_TARGETS_DOWN=0
R_FIABLE=INCONNU; R_VERDICT=INCONNU; R_TYPE=INCONNU
R_BRIDGE=INCONNU; R_SANTE=INCONNU; R_CATALOGUE=INCONNU
R_COLLECTEUR=INCONNU; R_TOKEN=INCONNU
R_T0=0; R_T75=0; DELTA=""; R_REPRISE=INCONNU

echo "HELIOS — validation post-recuperation"
echo "date locale : $(date)"
echo "mode ssh    : $MODE_SSH"
echo "cibles      : monitoring=$MON  aiCamunda=$AI"

# ============================================================ PHASE 1 — VM .160
t "PHASE 1 — VM MONITORING .160"
echo "--- ping ---"
ping -c 3 -w 6 "$MON" 2>&1 | tail -4 || echo "(ping indisponible ou bloque — non bloquant)"
echo "--- ssh ---"
OUT=$(S "$MON" 'hostname; uptime; date; who -b; echo "___OK___"')
echo "$OUT"
if echo "$OUT" | grep -q "___OK___"; then
  R_SSH160=OK
  R_UPTIME=$(echo "$OUT" | sed -n '2p' | sed 's/^ *//')
  R_BOOT=$(echo "$OUT" | grep -i "system boot" | sed 's/^ *//')
else
  R_SSH160=FAIL
fi

# ================================================== PHASE 2 — SERVICES MONITORING
t "PHASE 2 — SERVICES MONITORING"
for s in prometheus loki grafana-server; do
  v=$(S "$MON" "systemctl is-active $s" | tr -d '\r' | head -1)
  kv "$s" "$v"
  case "$s" in
    prometheus)     R_PROM="$v" ;;
    loki)           R_LOKI="$v" ;;
    grafana-server) R_GRAF="$v" ;;
  esac
done
echo "--- statut detaille ---"
S "$MON" 'systemctl status prometheus loki grafana-server --no-pager 2>&1 | head -60'

# ============================== PHASE 3 — INTEGRITE PROMETHEUS (WAL apres crash)
t "PHASE 3 — INTEGRITE PROMETHEUS (WAL)"
echo "--- occurrences suspectes dans le journal du boot courant ---"
WALOUT=$(S "$MON" 'journalctl -u prometheus -b --no-pager 2>&1 | grep -Ei "wal|recover|repair|corrupt|truncat|error|failed" | tail -40')
echo "${WALOUT:-(aucune occurrence)}"
if echo "$WALOUT" | grep -Eiq "corrupt|repair|truncat"; then
  R_WAL="PROBLEME — corruption/reparation detectee"
elif echo "$WALOUT" | grep -Eiq "replay|recover"; then
  R_WAL="OK — rejeu du WAL sans corruption"
elif [ -z "$WALOUT" ]; then
  R_WAL="OK — aucune occurrence suspecte"
else
  R_WAL="A LIRE — occurrences presentes, voir detail"
fi
echo "--- 60 dernieres lignes ---"
S "$MON" 'journalctl -u prometheus -b --no-pager -n 60 2>&1'
echo "--- taille TSDB / WAL (lecture seule) ---"
S "$MON" 'for d in /var/lib/prometheus /var/lib/prometheus/data /var/lib/prometheus/metrics2 /opt/prometheus/data; do
            [ -d "$d" ] && { echo "== $d"; du -sh "$d" 2>/dev/null; ls -la "$d/wal" 2>/dev/null | head -8; }; done
          echo "-- chemin TSDB declare --"
          systemctl cat prometheus --no-pager 2>/dev/null | grep -E "ExecStart|storage.tsdb.path" | head -5'

# ============================================================ PHASE 4 — LOKI
t "PHASE 4 — INTEGRITE LOKI"
LOKIOUT=$(S "$MON" 'journalctl -u loki -b --no-pager 2>&1 | grep -Ei "error|corrupt|chunk|wal|recover|failed" | tail -40')
echo "${LOKIOUT:-(aucune occurrence)}"
if echo "$LOKIOUT" | grep -Eiq "corrupt"; then
  R_LOKI_INTEG="PROBLEME — corruption detectee"
elif [ -z "$LOKIOUT" ]; then
  R_LOKI_INTEG="OK — aucune occurrence suspecte"
else
  R_LOKI_INTEG="A LIRE — occurrences presentes, voir detail"
fi
echo "--- 60 dernieres lignes ---"
S "$MON" 'journalctl -u loki -b --no-pager -n 60 2>&1'

# ============================================================ PHASE 5 — GRAFANA
t "PHASE 5 — GRAFANA (dependance DURE de detecter.py:184)"
kv "grafana-server" "$R_GRAF"
echo "--- /api/health ---"
GH=$(S "$MON" 'curl -fsS --max-time 10 http://127.0.0.1:3000/api/health 2>&1')
echo "$GH"
if echo "$GH" | grep -q '"database"[[:space:]]*:[[:space:]]*"ok"'; then
  R_GRAF_DB="ok"
else
  R_GRAF_DB="NON-ok  >>> monitoringFiable sera false <<<"
fi
kv "database" "$R_GRAF_DB"
S "$MON" 'systemctl status grafana-server --no-pager 2>&1 | head -20'

# ==================================================== PHASE 6 — API PROMETHEUS
t "PHASE 6 — API PROMETHEUS"
R_PROM_READY=$(S "$MON" 'curl -fsS --max-time 10 http://127.0.0.1:9090/-/ready 2>&1' | tr -d '\r' | head -1)
R_PROM_HEALTHY=$(S "$MON" 'curl -fsS --max-time 10 http://127.0.0.1:9090/-/healthy 2>&1' | tr -d '\r' | head -1)
kv "/-/ready"   "$R_PROM_READY"
kv "/-/healthy" "$R_PROM_HEALTHY"
echo "--- buildinfo ---"
S "$MON" 'curl -fsS --max-time 10 http://127.0.0.1:9090/api/v1/status/buildinfo 2>&1 | head -c 400'
echo
echo "--- query up (extrait) ---"
S "$MON" 'curl -fsS --max-time 10 "http://127.0.0.1:9090/api/v1/query?query=up" 2>&1 | head -c 600'
echo
echo "--- targets actives : repartition ---"
TG=$(S "$MON" 'curl -fsS --max-time 15 "http://127.0.0.1:9090/api/v1/targets?state=active" 2>/dev/null | grep -o "\"health\":\"[a-z]*\"" | sort | uniq -c')
echo "${TG:-(aucune target ou API indisponible)}"
R_TARGETS_UP=$(echo "$TG"   | grep '"up"'   | awk '{print $1}' | head -1); R_TARGETS_UP=${R_TARGETS_UP:-0}
R_TARGETS_DOWN=$(echo "$TG" | grep '"down"' | awk '{print $1}' | head -1); R_TARGETS_DOWN=${R_TARGETS_DOWN:-0}
echo "--- targets DOWN : lesquelles ? ---"
S "$MON" 'curl -fsS --max-time 15 "http://127.0.0.1:9090/api/v1/targets?state=active" 2>/dev/null \
          | tr "," "\n" | grep -B2 "\"health\":\"down\"" | head -30'
echo "--- continuite des donnees sur 36 h (trou pendant l arret ?) ---"
S "$MON" 'curl -fsS --max-time 15 "http://127.0.0.1:9090/api/v1/query?query=count_over_time(up[36h])" 2>&1 | head -c 400'

# ========================================================= PHASE 7 — API LOKI
t "PHASE 7 — API LOKI"
R_LOKI_READY=$(S "$MON" 'curl -fsS --max-time 10 http://127.0.0.1:3100/ready 2>&1' | tr -d '\r' | head -1)
kv "/ready" "$R_LOKI_READY"
echo "--- labels ---"
S "$MON" 'curl -fsS --max-time 10 http://127.0.0.1:3100/loki/api/v1/labels 2>&1 | head -c 600'
echo
echo "--- valeurs du label job (le selecteur k8s-containers existe-t-il ?) ---"
S "$MON" 'curl -fsS --max-time 10 http://127.0.0.1:3100/loki/api/v1/label/job/values 2>&1 | head -c 600'
echo
echo "--- requete reelle utilisee par detecter.py ---"
S "$MON" 'curl -fsS --max-time 15 --get http://127.0.0.1:3100/loki/api/v1/query \
            --data-urlencode "query=sum(count_over_time({job=\"k8s-containers\"}[5m]))" 2>&1 | head -c 500'

# ======================================================= PHASE 8 — DETECTER.PY
t "PHASE 8 — DETECTER.PY (execution reelle, lecture seule)"
S "$MON" 'ls -lh /opt/helios/detecter.py 2>&1; md5sum /opt/helios/detecter.py 2>/dev/null'
echo "--- execution ---"
DET=$(S "$MON" 'cd /opt/helios && timeout 60 python3 detecter.py 2>&1')
echo "$DET"
echo
if echo "$DET" | grep -q '"monitoringFiable"[[:space:]]*:[[:space:]]*true'; then
  R_FIABLE=true
elif echo "$DET" | grep -q '"monitoringFiable"[[:space:]]*:[[:space:]]*false'; then
  R_FIABLE=false
else
  R_FIABLE="INDETERMINE (sortie non JSON ou execution en echec)"
fi
R_TYPE=$(echo "$DET" | grep -o '"type"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
R_VERDICT=$(echo "$DET" | grep -o '"preuve"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -c1-160)
kv "monitoringFiable" "$R_FIABLE"
kv "type"             "${R_TYPE:-INDETERMINE}"
kv "preuve"           "${R_VERDICT:-(vide)}"
if [ "$R_FIABLE" = "false" ]; then
  echo
  echo ">>> DETECTION NEUTRALISEE. detecter.py:184 exige :"
  echo "      Grafana /api/health database == ok   ET   Prometheus query up non vide"
  echo "    Grafana database = $R_GRAF_DB"
  echo "    Prometheus ready = $R_PROM_READY"
  echo "    Toute absence de ligne dans le dataset s explique ICI, pas par le collecteur."
fi

# ==================================================== PHASE 9 — BRIDGE .161
t "PHASE 9 — BRIDGE ET COLLECTEUR (.161)"
S "$AI" 'hostname; date; uptime'
R_BRIDGE=$(S "$AI" 'systemctl is-active helios-ssh-bridge' | tr -d '\r' | head -1)
R_COLLECTEUR=$(S "$AI" 'systemctl is-active helios-collecteur-ml' | tr -d '\r' | head -1)
kv "helios-ssh-bridge"    "$R_BRIDGE"
kv "helios-collecteur-ml" "$R_COLLECTEUR"
echo "--- /sante ---"
R_SANTE=$(S "$AI" 'curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://127.0.0.1:8099/sante' | tr -d '\r' | head -1)
kv "GET /sante" "HTTP $R_SANTE"
S "$AI" 'curl -fsS --max-time 10 http://127.0.0.1:8099/sante 2>&1 | head -c 400'
echo
echo "--- catalogue canonique deploye a cote du bridge ? ---"
CAT=$(S "$AI" 'ls -l /opt/helios-ssh-bridge/ 2>&1')
echo "$CAT"
if echo "$CAT" | grep -q "helios_action_catalog.py"; then
  R_CATALOGUE="PRESENT"
else
  R_CATALOGUE="ABSENT — le bridge de l etape C ne peut pas demarrer sans lui"
fi
kv "helios_action_catalog.py" "$R_CATALOGUE"
echo "--- journal du bridge ---"
S "$AI" 'journalctl -u helios-ssh-bridge -n 40 --no-pager 2>&1'

# ========================================= PHASE 9b — TOKEN (jamais en clair)
t "PHASE 9b — HELIOS_BRIDGE_TOKEN DU COLLECTEUR"
ENVC=$(S "$AI" 'systemctl show helios-collecteur-ml -p Environment 2>/dev/null')
echo "$ENVC" | sed -E 's/(HELIOS_BRIDGE_TOKEN=)[^ ]*/\1<<REDACTED>>/g'
if echo "$ENVC" | grep -q "HELIOS_BRIDGE_TOKEN=[^ ]"; then
  R_TOKEN="TOKEN PRESENT"
else
  R_TOKEN="TOKEN ABSENT — les appels /detecter renverront 401, rien ne sera collecte"
fi
kv "resultat" "$R_TOKEN"

# ====================================== PHASE 9c — JOURNAL DU COLLECTEUR
t "PHASE 9c — JOURNAL DU COLLECTEUR"
S "$AI" 'journalctl -u helios-collecteur-ml -n 60 --no-pager 2>&1'
echo "--- motifs recherches ---"
S "$AI" 'journalctl -u helios-collecteur-ml -b --no-pager 2>&1 \
         | grep -Ei "401|/detecter|error|exception|traceback|dataset|collect|monitoringFiable|refus|jeton" | tail -30'

# ============================================================ PHASE 10 — DATASET
t "PHASE 10 — DATASET MVP (les DEUX chemins)"
S "$AI" 'for f in /opt/pfe-ai-service/datasets/host_incidents_reels.csv \
                  /opt/pfe-ai-service/datasets/host_incidents_reels_v2.csv; do
           echo "=================== $f"
           if [ -f "$f" ]; then
             ls -lh "$f"
             n=$(wc -l < "$f"); echo "  lignes (entete comprise) : $n"
             echo "  colonnes                 : $(head -1 "$f" | tr "," "\n" | wc -l)"
             echo "  --- header ---"; head -1 "$f" | tr "," "\n" | nl | head -60
             echo "  --- premiere ligne de donnees ---"; sed -n "2p" "$f" | head -c 500
             echo; echo "  --- derniere ligne ---"; tail -1 "$f" | head -c 500; echo
           else
             echo "  ABSENT"
           fi; echo
         done'
echo "--- quel fichier le service ecrit-il REELLEMENT ? ---"
S "$AI" 'systemctl show helios-collecteur-ml -p Environment 2>/dev/null | tr " " "\n" | grep HELIOS_ML_DATASET \
         || echo "(HELIOS_ML_DATASET non defini -> le code utilise son defaut : host_incidents_reels_v2.csv)"'
echo "--- analyse statistique (python3, lecture seule) ---"
S "$AI" 'python3 - <<"PY" 2>&1
import csv, os, collections
for f in ("/opt/pfe-ai-service/datasets/host_incidents_reels.csv",
          "/opt/pfe-ai-service/datasets/host_incidents_reels_v2.csv"):
    print("\n=== " + f)
    if not os.path.exists(f):
        print("  ABSENT"); continue
    with open(f, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    print("  lignes de donnees : %d" % len(rows))
    if not rows:
        print("  (fichier vide ou entete seule)"); continue
    cols = list(rows[0])
    print("  colonnes          : %d" % len(cols))
    inj = {r.get("injection_id","") for r in rows}
    print("  injection_id      : %d distinctes" % len(inj))
    for champ in ("label","label_severity","label_action_id","phase","provenance","source_corpus"):
        if champ in cols:
            print("  %-17s : %s" % (champ, dict(collections.Counter(r.get(champ,"") for r in rows))))
    ts = [r.get("timestamp","") for r in rows if r.get("timestamp")]
    if ts:
        print("  premiere donnee   : %s" % min(ts))
        print("  derniere donnee   : %s" % max(ts))
    vides = {c: sum(1 for r in rows if r.get(c,"") in ("","nan","NaN","None")) for c in cols}
    top = sorted(((v,k) for k,v in vides.items() if v), reverse=True)[:15]
    print("  vides/NaN (top 15): %s" % [(k,v) for v,k in top])
    clefs = [tuple(r.get(c,"") for c in cols) for r in rows]
    print("  doublons exacts   : %d" % (len(clefs)-len(set(clefs))))
PY'

# ================================================= PHASE 11 — PREUVE DE REPRISE
t "PHASE 11 — PREUVE DE REPRISE DE LA COLLECTE (T0 -> T+${ATTENTE}s)"
echo "Le collecteur ecrit toutes les HELIOS_COLLECTEUR_PERIODE secondes (defaut 30)."
echo "Aucune ecriture n est faite par ce script : on compte, on attend, on recompte."
REP=$(S "$AI" "F=/opt/pfe-ai-service/datasets/host_incidents_reels.csv
               G=/opt/pfe-ai-service/datasets/host_incidents_reels_v2.csv
               a=\$( [ -f \"\$F\" ] && wc -l < \"\$F\" || echo 0 )
               b=\$( [ -f \"\$G\" ] && wc -l < \"\$G\" || echo 0 )
               echo \"T0 reels=\$a v2=\$b\"
               sleep $ATTENTE
               c=\$( [ -f \"\$F\" ] && wc -l < \"\$F\" || echo 0 )
               d=\$( [ -f \"\$G\" ] && wc -l < \"\$G\" || echo 0 )
               echo \"T1 reels=\$c v2=\$d\"
               echo \"DELTA reels=\$((c-a)) v2=\$((d-b))\"")
echo "$REP"
R_T0=$(echo "$REP"  | grep '^T0 '    | sed 's/T0 //')
R_T75=$(echo "$REP" | grep '^T1 '    | sed 's/T1 //')
DELTA=$(echo "$REP" | grep '^DELTA ' | sed 's/DELTA //')
if echo "$DELTA" | grep -Eq 'reels=[1-9]|v2=[1-9]'; then
  R_REPRISE="COLLECTE ACTIVE ($DELTA)"
else
  R_REPRISE="AUCUNE NOUVELLE LIGNE ($DELTA)"
fi

# ================================================================ RAPPORT FINAL
t "RAPPORT FINAL"
cat <<EOF
HELIOS — POST-RECOVERY VALIDATION

1. VM MONITORING .160
STATUS  : $R_SSH160
uptime  : $R_UPTIME
boot    : $R_BOOT

2. SERVICES
Prometheus : $R_PROM
Loki       : $R_LOKI
Grafana    : $R_GRAF

3. PROMETHEUS
STATUS     : ready=$R_PROM_READY healthy=$R_PROM_HEALTHY
WAL        : $R_WAL
TARGETS    : up=$R_TARGETS_UP down=$R_TARGETS_DOWN

4. LOKI
STATUS     : ready=$R_LOKI_READY
CORRUPTION : $R_LOKI_INTEG

5. GRAFANA
STATUS   : $R_GRAF
database : $R_GRAF_DB

6. DETECTION
monitoringFiable : $R_FIABLE
incident_type    : ${R_TYPE:-INDETERMINE}
preuve           : ${R_VERDICT:-(vide)}

7. BRIDGE
STATUS    : $R_BRIDGE
/sante    : HTTP $R_SANTE
catalogue : $R_CATALOGUE

8. COLLECTEUR
STATUS : $R_COLLECTEUR
TOKEN  : $R_TOKEN

9. DATASET
voir PHASE 10 pour le detail des deux chemins

10. PREUVE DE REPRISE
T0       : $R_T0
T+$ATTENTE     : $R_T75
delta    : $DELTA
RESULTAT : $R_REPRISE

11. CHAINE GLOBALE
Monitoring -> Detection -> Bridge -> Collector -> Dataset
EOF

if [ "$R_SSH160" != "OK" ]; then
  echo "STATUS : BLOQUEE  (VM .160 injoignable)"
elif [ "$R_PROM" != "active" ] || [ "$R_LOKI" != "active" ] || [ "$R_GRAF" != "active" ]; then
  echo "STATUS : BLOQUEE  (un service de monitoring n est pas actif)"
elif [ "$R_FIABLE" = "false" ]; then
  echo "STATUS : BLOQUEE  (monitoringFiable=false : detection neutralisee en amont)"
elif [ "$R_BRIDGE" != "active" ] || [ "$R_COLLECTEUR" != "active" ]; then
  echo "STATUS : BLOQUEE  (bridge ou collecteur inactif sur .161)"
elif echo "$R_REPRISE" | grep -q "COLLECTE ACTIVE"; then
  echo "STATUS : OPERATIONNELLE"
else
  echo "STATUS : PARTIELLE  (chaine saine mais aucune ligne produite —"
  echo "                     verifier si aucun incident n est simplement en cours)"
fi

cat <<EOF

12. PROBLEMES DETECTES
  - Grafana database        -> $R_GRAF_DB
  - Token du collecteur     -> $R_TOKEN
  - Catalogue deploye       -> $R_CATALOGUE
  - Integrite WAL Prometheus-> $R_WAL
  - Integrite Loki          -> $R_LOKI_INTEG

13. RECOMMANDATION
  Si monitoringFiable=false : traiter Grafana/Prometheus AVANT toute analyse du
  dataset. L absence de lignes serait alors une consequence, pas une cause.

  Si la chaine est saine mais delta=0 : distinguer "aucun incident en cours" de
  "collecte cassee" en relisant le journal du collecteur (PHASE 9c) et le champ
  type de detecter.py (PHASE 8). NO_INCIDENT avec monitoringFiable=true signifie
  que la chaine fonctionne et que le cluster est simplement sain.

AUCUNE MODIFICATION N A ETE FAITE PAR CE SCRIPT.
EOF

t "FIN — colle l integralite de cette sortie dans la conversation"
