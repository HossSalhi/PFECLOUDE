#!/usr/bin/env bash
# DETECTOR-PRIORITY-03 (12 nov. 2026, DEPLOYE a 09:43) - deploiement du detecteur corrige sur .160 (monitoring) : /opt/helios/detecter.py
#
#   bash scripts/helios-ops/deploy-detecteur-160.sh              # SIMULATION (defaut) : lecture seule, ne modifie RIEN
#   bash scripts/helios-ops/deploy-detecteur-160.sh --apply      # DEPLOIE      (autorisation explicite de l'utilisateur)
#   bash scripts/helios-ops/deploy-detecteur-160.sh --rollback   # RESTAURE la sauvegarde (autorisation explicite)
#
# Garanties de --apply : tests unitaires verts AVANT ; empreinte de l'original verifiee (abandon s'il a change) ;
# sauvegarde verifiee et JAMAIS ecrasee ; essai en lecture seule AVANT bascule ; bascule ATOMIQUE (rename) ; retour arriere
# AUTOMATIQUE si la sortie du detecteur n'est plus un JSON valide. AUCUN redemarrage : le detecteur est un script relance a
# chaque appel (passerelle /detecter du Bridge et alarme_exporter.py). Aucun secret manipule.
#
# Fichier deploye = scripts/helios-ops/detecter.candidate.py (octet pour octet). Une fois deploye et verifie, il devient la
# nouvelle baseline : infra/helios-sot/baseline/detecteur/detecter.py (qui corrige aussi l'ecart NODE_TIME_DRIFT constate).
set -euo pipefail

MODE="${1:---simulation}"
case "$MODE" in
  --simulation|--apply|--rollback) ;;
  *) echo "usage : $0 [--simulation|--apply|--rollback]"; exit 2 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANDIDAT="$HERE/detecter.candidate.py"
TESTS=("$HERE/test_detecter_priorite.py" "$HERE/test_detecter_persistance.py")
HOTE="kube@192.168.148.160"
OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no)
ORIG_SHA="2d742d8028e93e4a89cf126d349381d0c1c07fe8584079ac7569c2e85eda6e22"   # detecteur deploye le 11 nov. 2026 a 22:21 (PRIORITY-02, 31 896 o)
F=/opt/helios/detecter.py
BK_DIR=/opt/helios-backups/detecteur-avant-PRIORITY-03-20261112
PY="$(command -v python || command -v python3)"
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

NEW_SHA="$(sha256sum "$CANDIDAT" | cut -d' ' -f1)"
echo "mode     : $MODE"
echo "candidat : $CANDIDAT"
echo "           sha256=${NEW_SHA:0:12}  $(wc -c < "$CANDIDAT") octets"

# ------------------------------------------------------------------ retour arriere
if [ "$MODE" = "--rollback" ]; then
  ssh "${OPTS[@]}" "$HOTE" bash -s -- "$ORIG_SHA" "$F" "$BK_DIR" <<'REMOTE'
set -eu
ORIG_SHA="$1"; F="$2"; BK_DIR="$3"; BK="$BK_DIR/detecter.py"; STAGE="$F.retour-arriere"
[ -e "$BK" ] || { echo "ABANDON : pas de sauvegarde $BK"; exit 30; }
[ "$(sha256sum "$BK" | cut -d' ' -f1)" = "$ORIG_SHA" ] || { echo "ABANDON : sauvegarde alteree"; exit 31; }
sudo -n cp -p "$BK" "$STAGE"
python3 "$STAGE" | python3 -c "import json,sys; json.load(sys.stdin)" || { echo "ABANDON : la sauvegarde ne produit pas de JSON valide"; sudo -n rm -f "$STAGE"; exit 32; }
sudo -n mv -f "$STAGE" "$F"
echo "retour arriere OK : sha256=$(sha256sum "$F" | cut -c1-12)"
REMOTE
  exit 0
fi

# ------------------------------------------------------------------ tests unitaires (simulation et apply)
echo "== tests unitaires sur le candidat (aucun reseau, aucune VM)"
for TEST in "${TESTS[@]}"; do
  echo "-- $(basename "$TEST")"
  HELIOS_DETECTER_PATH="$(winpath "$CANDIDAT")" "$PY" -I "$TEST" 2>&1 | tail -3
done

# ------------------------------------------------------------------ etat de .160 (lecture seule)
echo "== etat de .160 (lecture seule)"
ssh "${OPTS[@]}" "$HOTE" bash -s -- "$F" "$BK_DIR" <<'REMOTE'
F="$1"; BK_DIR="$2"
echo "sha256 en place     : $(sha256sum "$F" | cut -d' ' -f1)"
echo "mode / proprietaire : $(stat -c '%a %U:%G' "$F")"
echo "sauvegarde presente : $([ -e "$BK_DIR/detecter.py" ] && echo OUI || echo non)"
echo -n "detecteur en place  : "
python3 "$F" | python3 -c "import json,sys; d=json.load(sys.stdin); print('type=%s cible=%s matches=%s' % (d.get('type'), d.get('cible'), d.get('matches')))"
REMOTE
LIVE_SHA="$(ssh "${OPTS[@]}" "$HOTE" "sha256sum $F | cut -d' ' -f1")"
if [ "$LIVE_SHA" = "$NEW_SHA" ]; then
  echo "empreinte en place  : IDENTIQUE au candidat -> DEJA DEPLOYE, rien a faire"
  exit 0
fi
if [ "$LIVE_SHA" = "$ORIG_SHA" ]; then
  echo "empreinte en place  : CONFORME a l'original attendu"
else
  echo "empreinte en place  : DIFFERENTE de l'original attendu (${LIVE_SHA:0:12}) - --apply s'arreterait"
fi

if [ "$MODE" = "--simulation" ]; then
  echo "SIMULATION TERMINEE : rien n'a ete modifie. Pour deployer (autorisation explicite requise) :"
  echo "  bash scripts/helios-ops/deploy-detecteur-160.sh --apply"
  exit 0
fi

# ------------------------------------------------------------------ deploiement
[ "$LIVE_SHA" = "$ORIG_SHA" ] || { echo "ABANDON : le detecteur en place a change - rien n'est modifie"; exit 10; }
scp "${OPTS[@]}" "$CANDIDAT" "$HOTE:/tmp/detecter_nouveau.py"
ssh "${OPTS[@]}" "$HOTE" bash -s -- "$ORIG_SHA" "$NEW_SHA" "$F" "$BK_DIR" <<'REMOTE'
set -eu
ORIG_SHA="$1"; NEW_SHA="$2"; F="$3"; BK_DIR="$4"
SRC=/tmp/detecter_nouveau.py; STAGE="$F.nouveau-PRIORITY03"; BK="$BK_DIR/detecter.py"
sha() { sha256sum "$1" | cut -d' ' -f1; }
resume() { python3 -c "import json,sys; d=json.load(sys.stdin); print('type=%s cible=%s nbAnomalies=%s matches=%s' % (d.get('type'), d.get('cible'), d.get('nbAnomalies'), d.get('matches')))"; }

echo "=== 0. controles avant toute modification"
[ "$(sha "$F")" = "$ORIG_SHA" ] || { echo "ABANDON : le detecteur en place a change"; exit 10; }
[ "$(sha "$SRC")" = "$NEW_SHA" ] || { echo "ABANDON : le fichier recu n'a pas l'empreinte attendue"; exit 11; }
MODE=$(stat -c '%a' "$F"); OWNER=$(stat -c '%U' "$F"); GROUP=$(stat -c '%G' "$F")
echo "=== 1. sauvegarde de l'original (jamais ecrasee)"
[ ! -e "$BK" ] || { echo "ABANDON : une sauvegarde existe deja : $BK"; exit 12; }
sudo -n mkdir -p "$BK_DIR"; sudo -n cp -p "$F" "$BK"
[ "$(sha "$BK")" = "$ORIG_SHA" ] || { echo "ABANDON : sauvegarde invalide"; exit 13; }
echo "sauvegarde OK : $BK"
echo "=== 2. mise en place a cote (meme dossier => rename atomique)"
sudo -n install -m "$MODE" -o "$OWNER" -g "$GROUP" "$SRC" "$STAGE"
[ "$(sha "$STAGE")" = "$NEW_SHA" ] || { echo "ABANDON : copie a cote alteree"; sudo -n rm -f "$STAGE"; exit 14; }
echo "=== 3. controles AVANT bascule (lecture seule, donnees en direct)"
python3 -c "import ast,sys; ast.parse(open(sys.argv[1], encoding='utf-8').read())" "$STAGE" && echo "syntaxe : OK"
echo -n "ANCIEN (sauvegarde) : "; python3 "$BK" | resume
echo -n "NOUVEAU (a cote)    : "; python3 "$STAGE" | resume || { echo "ABANDON : JSON invalide"; sudo -n rm -f "$STAGE"; exit 15; }
echo "=== 4. bascule atomique"
sudo -n mv -f "$STAGE" "$F"
echo "=== 5. controle APRES bascule"
if python3 "$F" | resume; then
  [ "$(sha "$F")" = "$NEW_SHA" ] && echo "DEPLOIEMENT OK (sha256=${NEW_SHA:0:12})" || echo "ALERTE : empreinte differente de celle attendue"
else
  echo "ECHEC apres bascule : retour arriere automatique"
  sudo -n cp -p "$BK" "$STAGE" && sudo -n mv -f "$STAGE" "$F"
  echo "retour arriere : sha256=$(sha "$F" | cut -c1-12)"; exit 20
fi
rm -f "$SRC"
REMOTE
