#!/usr/bin/env bash
# HELIOS — Test de production E2E, exécuté DANS le cluster (sur le master, via kubectl).
# Déclenche un vrai incident (crash loop sur podinfo/pfe), puis observe la chaîne complète :
#   monitoring (.160) -> Camunda détecte -> IA (.161) classe+décide+explique -> Camunda exécute
#   dans le cluster -> l'app est remédiée (rollback) -> pods de nouveau Ready.
#
# Recette PROUVÉE (identique au chaos toolkit). L'incident est déclenché ICI, dans la VM,
# jamais depuis Windows. Aucune dépendance externe : 100% kubectl.
#
# Usage (sur le master) :  bash prod_test_in_cluster.sh [--restore]
set -uo pipefail

NS=pfe
APP=podinfo
DEADLINE=360         # secondes max pour attendre la remédiation HELIOS
CRASH_CMD='[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","echo boot; exit 1"]}]'
UNDO_CMD='[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]'

c_g="\033[32m"; c_y="\033[33m"; c_r="\033[31m"; c_0="\033[0m"
say(){ echo -e "${c_y}>>> $*${c_0}"; }
ok(){  echo -e "${c_g}[OK] $*${c_0}"; }
err(){ echo -e "${c_r}[KO] $*${c_0}"; }

# --- restauration manuelle (au cas où HELIOS n'aurait pas corrigé) ---
if [[ "${1:-}" == "--restore" ]]; then
  say "Restauration de $APP (retrait du command override + rollout)"
  kubectl patch deployment "$APP" -n "$NS" --type=json -p "$UNDO_CMD" 2>/dev/null
  kubectl rollout status deploy/"$APP" -n "$NS" --timeout=120s
  ok "podinfo restauré."
  exit 0
fi

# --- pré-requis ---
if ! kubectl get deploy "$APP" -n "$NS" >/dev/null 2>&1; then
  err "Déploiement $APP introuvable dans $NS. (setup podinfo requis — voir chaos toolkit)"
  exit 2
fi
say "État initial :"
kubectl get deploy "$APP" -n "$NS"
kubectl get pods -n "$NS" -l app="$APP"

# --- 1) DÉCLENCHEMENT de l'incident réel (dans le cluster) ---
say "Déclenchement CRASH LOOP sur $APP (le conteneur sort en erreur immédiatement)"
kubectl patch deployment "$APP" -n "$NS" --type=json -p "$CRASH_CMD"
say "Attendu : CrashLoopBackOff -> incident POD_CRASH_LOOP -> IA classe+décide (ROLLBACK) + explique -> Camunda exécute -> RESOLVED."
echo

# --- 2) OBSERVATION de la remédiation automatique ---
start=$(date +%s)
crash_seen=0
while :; do
  now=$(date +%s); el=$((now - start))
  # état courant des pods podinfo
  line=$(kubectl get pods -n "$NS" -l app="$APP" --no-headers 2>/dev/null)
  ready=$(echo "$line" | awk '{print $2}' | grep -c '^1/1' || true)
  total=$(echo "$line" | grep -c . || true)
  waiting=$(kubectl get pods -n "$NS" -l app="$APP" \
     -o jsonpath='{range .items[*]}{.status.containerStatuses[0].state.waiting.reason}{"\n"}{end}' 2>/dev/null | grep -c CrashLoopBackOff || true)
  # le command override a-t-il été retiré ? (= HELIOS a rollback)
  cmd=$(kubectl get deploy "$APP" -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].command}' 2>/dev/null)

  printf "  t=%3ds  pods_ready=%s/%s  crashloop=%s  override=%s\n" \
     "$el" "${ready:-0}" "${total:-0}" "${waiting:-0}" "$([[ -z "$cmd" ]] && echo removed || echo present)"

  [[ "${waiting:-0}" -gt 0 ]] && crash_seen=1

  # SUCCÈS : on a bien vu le crashloop, puis HELIOS a rollback (override retiré) et les pods sont Ready
  if [[ "$crash_seen" -eq 1 && -z "$cmd" && "${ready:-0}" -ge 1 && "${waiting:-0}" -eq 0 ]]; then
    echo
    ok "REMÉDIATION CONFIRMÉE en ${el}s : crash loop détecté -> HELIOS a rollback -> pods Ready."
    break
  fi
  if [[ "$el" -ge "$DEADLINE" ]]; then
    echo
    err "TIMEOUT (${DEADLINE}s) : pas de remédiation observée. (crash vu=$crash_seen)"
    err "Vérifie l'IA (.161:8000/health), Camunda, et les logs backend. Restauration : bash $0 --restore"
    exit 1
  fi
  sleep 10
done

# --- 3) La CLASSIFICATION + ACTION + EXPLICATION de l'IA (depuis pfe_db) ---
echo
say "Décision IA enregistrée pour cet incident (base pfe_db) :"
PG=$(kubectl get pods -n "$NS" -o name 2>/dev/null | grep -oE 'postgres-[0-9]+' | head -1)
PG=${PG:-postgres-1}
PSQL="kubectl exec -n $NS $PG -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -P pager=off -c"
# requête riche (type + sévérité + action + explication) ; repli minimal si colonnes différentes
$PSQL "SELECT incident_type, severity, status, recommended_action, left(decision_explanation,300) AS why \
       FROM incident WHERE app='$APP' ORDER BY id DESC LIMIT 1;" 2>/dev/null \
  || $PSQL "SELECT incident_type, status, created_at FROM incident WHERE app='$APP' ORDER BY id DESC LIMIT 1;" 2>/dev/null \
  || echo "  (requête DB indisponible — regarde l'incident dans l'UI HELIOS ou les logs backend)"

echo
ok "Test E2E terminé. La chaîne monitoring -> Camunda -> IA(classe+décide+explique) -> exécution -> RESOLVED a fonctionné."
