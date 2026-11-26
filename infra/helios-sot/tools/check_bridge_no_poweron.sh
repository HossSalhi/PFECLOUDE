#!/usr/bin/env bash
# Contrôle du Bridge .161 : il ne doit avoir AUCUNE capacité de mise sous tension d'une VM.
#
#   bash infra/helios-sot/tools/check_bridge_no_poweron.sh --static-only    A seule : LECTURE SEULE stricte (aucune requête au Bridge)
#   bash infra/helios-sot/tools/check_bridge_no_poweron.sh                  A + B : B envoie 7 requêtes de REFUS (voir « Effets »)
#
# A. STATIQUE (lecture seule, rien n'est écrit) :
#    - SHA-256 complets de bridge.py et telemetrie.py DÉPLOYÉS = ceux du dépôt (infra/helios-sot/bridge, mesurés à chaque lancement) ;
#    - NOMS des variables de /etc/helios/bridge.env : aucune ne doit commencer par HELIOS_WATCHDOG (les valeurs ne sont jamais lues) ;
#    - environnement du processus du Bridge : aucune variable HELIOS_WATCHDOG* (comptage seulement) ;
#    - unités systemd : aucune ne cite HELIOS_WATCHDOG ; service actif ; le Bridge n'écoute que sur 127.0.0.1:8099 ;
#    - TCP depuis .161 vers le watchdog hôte (192.168.148.1:9099 et :9999) : doit être INACCESSIBLE (connexion seule, aucune requête HTTP).
# B. FONCTIONNEL (preuve par l'usage) : 7 POST /action RESTART_NODE_VM (workers, master, lb2, monitoring et alias k8s-*) ;
#    chacun doit répondre execute=False et refuse=POWER_ON_INTERDIT.
#    EFFETS EXACTS de B (le code du Bridge les impose AVANT le refus, voir bridge.py : TELEMETRY-01) : 7 lignes ajoutées à
#    /var/lib/helios/telemetry/decisions-AAAA-MM-JJ.jsonl (étiquetées injection_id=CHECK_BRIDGE_NO_POWERON) + quelques lignes du
#    journal du Bridge. Aucun effet sur une VM, le watchdog, la base, Camunda ou le cluster.
#
# Secrets : le jeton du Bridge est lu avec sed (le fichier n'est PAS « sourcé ») et transmis à curl par son entrée standard
# (-K -) : il n'apparaît ni dans la sortie, ni dans la liste des processus. Rien de sensible n'est affiché.
# Historique : la version précédente sourçait tout bridge.env et passait le jeton sur la ligne de commande de curl (visible dans `ps`) ;
# elle n'ajoutait aucune étiquette aux lignes de télémétrie. Original conservé : helios-state\snapshots\prechange-20261106.
#
# Code de sortie : 0 = tout PASS ; 1 = au moins un FAIL.
set -euo pipefail

MODE=1
case "${1:-}" in
  --static-only) MODE=0 ;;
  "") ;;
  *) echo "usage : $0 [--static-only]" >&2; exit 2 ;;
esac

HERE="$(cd "$(dirname "$0")" && pwd)"
SOT="$HERE/../bridge"
EXP_B="$(sha256sum "$SOT/bridge.py" | cut -d' ' -f1)"
EXP_T="$(sha256sum "$SOT/telemetrie.py" | cut -d' ' -f1)"
echo "empreintes attendues (dépôt) : bridge.py=${EXP_B:0:12} telemetrie.py=${EXP_T:0:12} | mode : $([ "$MODE" = 1 ] && echo 'statique + fonctionnel' || echo 'statique seul (lecture seule)')"

ssh -o BatchMode=yes -o ConnectTimeout=8 helios161 "sudo -n bash -s -- $EXP_B $EXP_T $MODE" <<'REMOTE'
set -u
EXP_B="$1"; EXP_T="$2"; FUNC="$3"
ok=0; bad=0
chk() {   # nom, 1 (ok) | 0 (échec), détail
  if [ "$2" = "1" ]; then ok=$((ok + 1)); printf 'PASS  %-62s %s\n' "$1" "$3"
  else bad=$((bad + 1)); printf 'FAIL  %-62s %s\n' "$1" "$3"; fi
}
yes1() { [ "$1" = "$2" ] && echo 1 || echo 0; }

# ---------- A. statique ----------
D_B="$(sha256sum /opt/helios-ssh-bridge/bridge.py | cut -d' ' -f1)"
D_T="$(sha256sum /opt/helios-ssh-bridge/telemetrie.py | cut -d' ' -f1)"
chk "bridge.py déployé = dépôt (SHA-256 complet)" "$(yes1 "$D_B" "$EXP_B")" "${D_B:0:12}"
chk "telemetrie.py déployé = dépôt (SHA-256 complet)" "$(yes1 "$D_T" "$EXP_T")" "${D_T:0:12}"
NAMES="$(sed -E 's/=.*//' /etc/helios/bridge.env | tr '\n' ' ')"
echo "$NAMES" | grep -q 'HELIOS_WATCHDOG' && R=0 || R=1
chk "bridge.env : aucun nom HELIOS_WATCHDOG* (valeurs jamais lues)" "$R" "$NAMES"
chk "service helios-ssh-bridge actif" "$(yes1 "$(systemctl is-active helios-ssh-bridge)" active)" "$(systemctl is-active helios-ssh-bridge)"
PID="$(systemctl show -p MainPID --value helios-ssh-bridge)"
N_ENV="$(cat "/proc/$PID/environ" | tr '\0' '\n' | grep -c '^HELIOS_WATCHDOG' || true)"
chk "processus du Bridge : aucune variable HELIOS_WATCHDOG*" "$(yes1 "$N_ENV" 0)" "pid $PID, $N_ENV variable(s)"
N_UNIT="$(grep -rIl 'HELIOS_WATCHDOG' /etc/systemd/system 2>/dev/null | wc -l)"
chk "unités systemd : aucune ne cite HELIOS_WATCHDOG" "$(yes1 "$N_UNIT" 0)" "$N_UNIT fichier(s)"
LISTEN="$(ss -ltn 2>/dev/null | awk '$4 ~ /:8099$/ {print $4}' | sort -u | tr '\n' ' ')"
chk "le Bridge n'écoute que sur 127.0.0.1:8099" "$(yes1 "$LISTEN" "127.0.0.1:8099 ")" "$LISTEN"
for P in 9099 9999; do
  if timeout 3 bash -c "</dev/tcp/192.168.148.1/$P" 2>/dev/null; then R=0; T="JOIGNABLE"; else R=1; T="inaccessible"; fi
  chk "TCP .161 -> hôte:$P (watchdog / ancien agent) inaccessible" "$R" "$T"
done

# ---------- B. fonctionnel ----------
if [ "$FUNC" = "1" ]; then
  TOKEN="$(sed -n 's/^HELIOS_BRIDGE_TOKEN=//p' /etc/helios/bridge.env | head -n1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//")"
  if [ -z "$TOKEN" ]; then
    chk "jeton du Bridge lisible côté .161" 0 "introuvable"
  else
    for N in worker1 worker2 master lb2 monitoring k8s-lb2 k8s-monitoring; do
      R="$(printf 'header = "X-Helios-Token: %s"\n' "$TOKEN" | curl -s -m 10 -K - -X POST http://127.0.0.1:8099/action \
            -H 'Content-Type: application/json' \
            -d "{\"action\":\"RESTART_NODE_VM\",\"noeud\":\"$N\",\"injection_id\":\"CHECK_BRIDGE_NO_POWERON\"}" || true)"
      V="$(printf '%s' "$R" | python3 -c 'import sys,json
d=json.load(sys.stdin)
print(d.get("execute"), d.get("refuse"))' 2>/dev/null || echo "reponse-illisible")"
      chk "POST /action RESTART_NODE_VM $N -> refus attendu" "$(yes1 "$V" "False POWER_ON_INTERDIT")" "$V"
    done
  fi
  unset TOKEN
else
  echo "(partie B fonctionnelle non exécutée : --static-only)"
fi

echo "----"
if [ "$bad" -eq 0 ]; then echo "RESULTAT: PASS — $ok contrôle(s) satisfait(s), 0 écart"; else echo "RESULTAT: FAIL — $bad écart(s) sur $((ok + bad)) contrôle(s)"; exit 1; fi
REMOTE
