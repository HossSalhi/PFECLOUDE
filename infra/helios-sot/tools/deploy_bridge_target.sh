#!/usr/bin/env bash
# =============================================================================
# HELIOS — Bridge CIBLE (sans aucune capacité de power-on) sur .161. DRY-RUN PAR DÉFAUT.
#
#   bash deploy_bridge_target.sh                     # contrôles en LECTURE SEULE + plan
#   bash deploy_bridge_target.sh --apply             # déploie (sauvegardes, installation, jeton retiré, redémarrage, preuves)
#   bash deploy_bridge_target.sh --rollback <UTC>    # restaure les fichiers `*.avant-VMDOWN-4AUTO-<UTC>` et bridge.env
#
# Accès : alias SSH `helios161` (clé ~/.ssh/id_ed25519 ; .161 refuse les mots de passe), sudo sans mot de passe.
# À exécuter APRÈS le déploiement du backend (c'est lui qui porte alors les notifications des VM).
#
# Ce que fait --apply (sur .161, en root) :
#   1. contrôle que les fichiers DÉPLOYÉS sont identiques à la baseline du dépôt (sinon STOP : dérive) ;
#   2. dépose les fichiers cibles dans /tmp, vérifie leur syntaxe ;
#   3. sauvegardes horodatées dans le même dossier (bridge.py, telemetrie.py, /etc/helios/bridge.env) ;
#   4. installe bridge.py + telemetrie.py (644 root:root), RETIRE HELIOS_WATCHDOG_TOKEN de bridge.env (600 conservé) ;
#   5. redémarre helios-ssh-bridge ; sans /sante 200 sous 25 s => RETOUR ARRIÈRE AUTOMATIQUE ;
#   6. preuves : jeton absent de l'environnement du processus ; POST /action RESTART_NODE_VM => POWER_ON_INTERDIT ;
#      POST /notifier d'un SAFE_STOP de VM => SUPPRIMEE_DETECTION_SEULE (un e-mail réellement reçu = filtre KO).
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
SRC="$REPO/infra/helios-sot/bridge"
MAN="$REPO/infra/helios-sot/baseline/MANIFEST.sha256"
HOST="${HELIOS_BRIDGE_HOST:-helios161}"
DIR=/opt/helios-ssh-bridge
ENVF=/etc/helios/bridge.env
SVC=helios-ssh-bridge
MODE=dry
TS=""
case "${1:-}" in
  --apply) MODE=apply ;;
  --rollback) MODE=rollback; TS="${2:?horodatage UTC requis (ex. 20261106T010000Z)}" ;;
  ""|--dry-run) ;;
  *) echo "usage: $0 [--dry-run|--apply|--rollback <UTC>]" >&2; exit 2 ;;
esac
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
rssh() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "$@"; }
want() { awk -v f="*./bridge/$1" '$2==f {print $1}' "$MAN"; }

rollback_remote() { # $1 = horodatage
  rssh "sudo -n bash -s" <<EOF
set -e
cp -p $DIR/bridge.py.avant-VMDOWN-4AUTO-$1 $DIR/bridge.py
cp -p $DIR/telemetrie.py.avant-VMDOWN-4AUTO-$1 $DIR/telemetrie.py
cp -p $ENVF.bak-VMDOWN-4AUTO-$1 $ENVF
systemctl restart $SVC
sleep 3
systemctl is-active $SVC
EOF
}

if [ "$MODE" = "rollback" ]; then
  log "RETOUR ARRIÈRE vers les sauvegardes $TS"
  rollback_remote "$TS"
  log "restauré : Bridge et bridge.env d'avant (jeton du watchdog compris)"
  exit 0
fi

log "1) fichiers cibles (locaux)"
for f in bridge.py telemetrie.py; do [ -s "$SRC/$f" ] || { echo "absent : $SRC/$f" >&2; exit 1; }; done
python -I - "$SRC/bridge.py" "$SRC/telemetrie.py" <<'PY'
import ast, sys
for p in sys.argv[1:]:
    ast.parse(open(p, encoding="utf-8").read(), p)
print("   syntaxe Python OK")
PY
grep -q "__poweron_interdit__" "$SRC/bridge.py" || { echo "le Bridge cible ne contient pas le refus de power-on" >&2; exit 1; }
! grep -qE "def rallumer|JETON_WATCHDOG" "$SRC/bridge.py" || { echo "le Bridge cible contient encore une capacité de power-on" >&2; exit 1; }
echo "   bridge.py cible : $(sha256sum "$SRC/bridge.py" | cut -c1-16)…   telemetrie.py cible : $(sha256sum "$SRC/telemetrie.py" | cut -c1-16)…"

log "2) état déployé sur $HOST (lecture seule)"
dep="$(rssh "sha256sum $DIR/bridge.py $DIR/telemetrie.py")"
for f in bridge.py telemetrie.py; do
  got="$(printf '%s\n' "$dep" | awk -v p="$DIR/$f" '$2==p {print $1}')"
  [ -n "$got" ] && [ "$got" = "$(want "$f")" ] || { echo "   DÉRIVE : $f déployé != baseline (MANIFEST). STOP : ne rien écraser sans analyse." >&2; exit 1; }
done
echo "   bridge.py / telemetrie.py déployés = baseline (MANIFEST)"
rssh "systemctl is-active $SVC"
echo "   variables de $ENVF : $(rssh "sudo -n sed -E 's/=.*//' $ENVF" | tr '\n' ' ')"

if [ "$MODE" = "dry" ]; then
  log "DRY-RUN : rien n'a été écrit. Relancer avec --apply pour déployer (retour arrière : --rollback <UTC>)."
  exit 0
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
log "3) dépôt des fichiers cibles dans /tmp/helios-bridge-target-$TS"
rssh "mkdir -p /tmp/helios-bridge-target-$TS"
scp -q -o BatchMode=yes "$SRC/bridge.py" "$SRC/telemetrie.py" "$HOST:/tmp/helios-bridge-target-$TS/"

log "4) sauvegardes + installation + retrait du jeton + redémarrage ($TS)"
rssh "sudo -n bash -s" <<EOF
set -euo pipefail
T=/tmp/helios-bridge-target-$TS
python3 -c "import ast,sys; [ast.parse(open(p).read(), p) for p in sys.argv[1:]]" \$T/bridge.py \$T/telemetrie.py
cp -p $DIR/bridge.py $DIR/bridge.py.avant-VMDOWN-4AUTO-$TS
cp -p $DIR/telemetrie.py $DIR/telemetrie.py.avant-VMDOWN-4AUTO-$TS
cp -p $ENVF $ENVF.bak-VMDOWN-4AUTO-$TS
install -m 644 -o root -g root \$T/bridge.py $DIR/bridge.py
install -m 644 -o root -g root \$T/telemetrie.py $DIR/telemetrie.py
sed -i '/^HELIOS_WATCHDOG_TOKEN=/d' $ENVF
echo "   bridge.env : \$(stat -c '%a %U:%G' $ENVF) ; jeton du watchdog présent : \$(grep -c '^HELIOS_WATCHDOG_TOKEN=' $ENVF || true)"
systemctl restart $SVC
EOF

ok=0
for i in $(seq 1 25); do
  code="$(rssh "curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8099/sante" 2>/dev/null || true)"
  [ "$code" = "200" ] && { ok=1; break; }
  sleep 1
done
if [ "$ok" != "1" ]; then
  log "❌ /sante sans réponse 200 : RETOUR ARRIÈRE AUTOMATIQUE"
  rollback_remote "$TS"
  exit 1
fi
log "   service actif, /sante = 200"

log "5) preuves (le jeton du Bridge est lu côté .161 et jamais affiché)"
rssh "sudo -n bash -s" <<EOF
set -euo pipefail
PID=\$(systemctl show -p MainPID --value $SVC)
echo "   jeton du watchdog dans l'environnement du processus : \$(tr '\0' '\n' < /proc/\$PID/environ | grep -c '^HELIOS_WATCHDOG_TOKEN=' || true) (attendu : 0)"
set -a; . $ENVF; set +a
R=\$(curl -s -m 10 -X POST http://127.0.0.1:8099/action -H 'Content-Type: application/json' -H "X-Helios-Token: \$HELIOS_BRIDGE_TOKEN" -d '{"action":"RESTART_NODE_VM","noeud":"worker1"}')
echo "   POST /action RESTART_NODE_VM : \$(printf '%s' "\$R" | python3 -c 'import sys,json; d=json.load(sys.stdin); print({k:d.get(k) for k in ("execute","refuse")})')"
N=\$(curl -s -m 10 -X POST http://127.0.0.1:8099/notifier -H 'Content-Type: application/json' -H "X-Helios-Token: \$HELIOS_BRIDGE_TOKEN" -d '{"sujet":"[HELIOS] SAFE_STOP VM_DOWN sur k8s-lb2","corps":"controle du filtre (deploiement du Bridge cible)"}')
echo "   POST /notifier SAFE_STOP VM : \$(printf '%s' "\$N" | python3 -c 'import sys,json; d=json.load(sys.stdin); print({k:d.get(k) for k in ("voie","supprimee")})')"
EOF
log "Bridge cible déployé. Retour arrière : bash $0 --rollback $TS"
