#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reparation du WAL CORROMPU de Prometheus sur .160 (monitoring) — constat du 12 nov. 2026.

Symptome : toutes les 2 h pile Prometheus journalise « compaction failed … WAL truncation in Compact: create checkpoint:
read segments: corruption in segment …/wal/00001973 at 89: unexpected full record ». Le bloc de 2 h est ecrit mais le WAL
n'est JAMAIS tronque : il grossit (≈ +100 Mo/h), le disque racine de .160 monte (80 % -> 86 % en une nuit) et finira plein.

Principe : tout ce qui precede le dernier bloc ecrit est DEJA dans les blocs ; seule la tete recente vit dans le WAL. Juste
APRES une coupe de 2 h, le WAL ne contient donc presque plus rien d'irremplacable : on arrete Prometheus proprement, on met le
WAL ENTIER de cote (jamais de suppression, jamais de segments isoles : les enregistrements de series sont disperses), on
redemarre. Cout : environ 1 min d'aveuglement (le detecteur repond NO_INCIDENT, monitoringFiable=false : sans danger) et la
perte de quelques minutes d'echantillons.

    python repair_prometheus_wal_160.py              # SIMULATION (defaut) : lecture seule, dit ce qu'elle ferait et QUAND
    python repair_prometheus_wal_160.py --apply      # repare, SI on est dans les 10 min qui suivent une coupe de 2 h
    python repair_prometheus_wal_160.py --apply --now   # repare tout de suite (perd la tete recente, jusqu'a ~2 h de metriques)
    python repair_prometheus_wal_160.py --verify     # apres la coupe SUIVANTE : controle que le WAL se tronque enfin

--apply est une operation de PRODUCTION (arret/redemarrage de Prometheus) : autorisation explicite de l'exploitant requise.
Le WAL mis de cote (≈ 1,5 Go) n'est PAS supprime : sa suppression est une autorisation distincte. Aucun secret manipule.
"""
import argparse
import posixpath
import re
import shlex
import subprocess
import sys
import time

HOTE = "kube@192.168.148.160"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no", HOTE]
DONNEES = "/var/lib/prometheus/metrics2"
FENETRE_APRES_COUPE_S = 600        # on n'agit que dans les 10 min qui suivent un « write block »
WAL_MIN_MO = 200                   # en dessous, rien a reparer
LIBRE_MIN_MO = 300

RELEVE = f"""
echo "ACTIVE=$(systemctl is-active prometheus)"
echo "NOW=$(date -u +%s)"
echo "WALMB=$(sudo -n du -sm {DONNEES}/wal 2>/dev/null | cut -f1)"
echo "SEGS=$(sudo -n ls -1 {DONNEES}/wal 2>/dev/null | grep -cE '^[0-9]{{8}}$')"
echo "FREEMB=$(df -m --output=avail / | tail -1 | tr -d ' ')"
echo "LASTBLOCK=$(sudo -n journalctl -u prometheus --since '-5 hours' --no-pager -o short-unix 2>/dev/null | grep 'write block' | tail -1 | cut -d' ' -f1 | cut -d. -f1)"
echo "CORRUPT=$(sudo -n journalctl -u prometheus --since '-5 hours' --no-pager 2>/dev/null | grep -c 'compaction failed.*corruption in segment')"
echo "FAILED=$(curl -s 'http://localhost:9090/api/v1/query?query=prometheus_tsdb_compactions_failed_total' | python3 -c "import sys,json; r=json.load(sys.stdin)['data']['result']; print(int(float(r[0]['value'][1])) if r else '')" 2>/dev/null)"
echo "DIRDATA=$([ -d {DONNEES} ] && echo oui || echo non)"
echo "START=$(date -d "$(systemctl show prometheus -p ActiveEnterTimestamp --value)" +%s 2>/dev/null)"
echo "FAILSINCE=$(sudo -n journalctl -u prometheus --since "$(date -d "$(systemctl show prometheus -p ActiveEnterTimestamp --value)" '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" --no-pager 2>/dev/null | grep -c 'compaction failed')"
echo "CKPTSINCE=$(sudo -n journalctl -u prometheus --since "$(date -d "$(systemctl show prometheus -p ActiveEnterTimestamp --value)" '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" --no-pager 2>/dev/null | grep -c 'WAL checkpoint complete')"
true
"""

REPARATION = f"""
set -u
D=/var/lib/prometheus
A=$D/wal-corrompu-$(date -u +%Y%m%d-%H%M%S)
attendre_pret() {{ for i in $(seq 1 90); do curl -fsS http://localhost:9090/-/ready >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }}
echo "[1/5] arret propre de Prometheus"
sudo -n systemctl stop prometheus
for i in $(seq 1 60); do [ "$(systemctl is-active prometheus)" = "inactive" ] && break; sleep 1; done
if [ "$(systemctl is-active prometheus)" != "inactive" ]; then echo "ABANDON : Prometheus ne s'arrete pas ; redemarrage"; sudo -n systemctl start prometheus; exit 5; fi
echo "[2/5] WAL entier mis de cote (aucune suppression) : $A"
if ! sudo -n mv {DONNEES}/wal "$A"; then echo "ECHEC du deplacement ; redemarrage sans changement"; sudo -n systemctl start prometheus; exit 6; fi
echo "[3/5] redemarrage"
sudo -n systemctl start prometheus
if attendre_pret; then
  echo "[4/5] Prometheus PRET avec un WAL neuf"
else
  echo "ECHEC : Prometheus pas pret ; RETOUR ARRIERE (WAL d'origine remis en place)"
  sudo -n systemctl stop prometheus
  for i in $(seq 1 60); do [ "$(systemctl is-active prometheus)" = "inactive" ] && break; sleep 1; done
  [ -d {DONNEES}/wal ] && sudo -n mv {DONNEES}/wal "$D/wal-nouveau-echec-$(date -u +%H%M%S)"
  sudo -n mv "$A" {DONNEES}/wal
  sudo -n systemctl start prometheus
  attendre_pret && echo "retour arriere termine : Prometheus de nouveau pret" || echo "ALERTE : Prometheus toujours pas pret apres retour arriere"
  exit 7
fi
echo "[5/5] etat : WAL neuf $(sudo -n du -sm {DONNEES}/wal 2>/dev/null | cut -f1) Mo ; WAL mis de cote $(sudo -n du -sm "$A" 2>/dev/null | cut -f1) Mo (non supprime)"
echo "WALDEPLACE=$A"
"""


DOSSIER_PROMETHEUS = "/var/lib/prometheus"
WAL_DEPLACE_DEFAUT = DOSSIER_PROMETHEUS + "/wal-corrompu-20261112-090152"   # mis de cote le 12 nov. 2026 a 10:01:52
MOTIF_WAL_DEPLACE = re.compile(r"^wal-corrompu-\d{8}-\d{6}$")


def chemin_supprimable(chemin):
    """Garde-fou PUR : seul un dossier `wal-corrompu-AAAAMMJJ-HHMMSS` DIRECTEMENT sous /var/lib/prometheus peut etre
    supprime (jamais le WAL actif, jamais un autre dossier). Retourne (ok, raison)."""
    if not isinstance(chemin, str) or not chemin.startswith("/") or "\x00" in chemin:
        return False, "chemin invalide"
    if chemin != posixpath.normpath(chemin) or ".." in chemin.split("/"):
        return False, "chemin non normalise"
    if posixpath.dirname(chemin) != DOSSIER_PROMETHEUS:
        return False, "pas directement sous %s" % DOSSIER_PROMETHEUS
    if not MOTIF_WAL_DEPLACE.match(posixpath.basename(chemin)):
        return False, "nom hors du motif wal-corrompu-AAAAMMJJ-HHMMSS"
    return True, ""


# Suppression du WAL mis de cote (arguments : $1 = chemin, $2 = --apply ou vide = SIMULATION). Revalide TOUT cote distant.
SUPPRESSION = r"""
set -u
P="$1"; MODE="${2:-}"
D=/var/lib/prometheus
LIVE=/var/lib/prometheus/metrics2/wal
case "$P" in
  "$D"/wal-corrompu-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
  *) echo "REFUS : chemin hors du motif autorise"; exit 10 ;;
esac
if [ ! -d "$P" ] || [ -L "$P" ]; then echo "REFUS : ce n'est pas un dossier ordinaire"; exit 11; fi
if [ "$(readlink -f "$P")" != "$P" ]; then echo "REFUS : chemin non canonique"; exit 12; fi
if [ ! -d "$LIVE" ] || [ "$(readlink -f "$LIVE")" = "$P" ]; then echo "REFUS : WAL actif introuvable ou confondu avec la cible"; exit 13; fi
case "$(readlink -f "$LIVE")" in "$P"/*) echo "REFUS : le WAL actif est DANS la cible"; exit 14 ;; esac
if [ "$(systemctl is-active prometheus)" != "active" ]; then echo "REFUS : Prometheus n'est pas actif : ne rien supprimer dans cet etat"; exit 15; fi
OUVERTS=$(sudo -n lsof -nP +D "$P" 2>/dev/null | tail -n +2 | wc -l)
if [ "$OUVERTS" != "0" ]; then echo "REFUS : $OUVERTS fichier(s) ouvert(s) dans la cible"; exit 16; fi
echo "--- contenu (nom, taille, date)"
sudo -n find "$P" -mindepth 1 -printf '%s %TY-%Tm-%Td_%TH:%TM  %P\n' | sort -k3 | awk '{printf "%7.1f Mo  %s  %s\n", $1/1048576, $2, $3}'
echo "total : $(sudo -n du -sm "$P" | cut -f1) Mo ; libre avant : $(df -m --output=avail / | tail -1 | tr -d ' ') Mo"
if [ "$MODE" != "--apply" ]; then echo "SIMULATION : rien n'a ete supprime"; exit 0; fi
sudo -n rm -rf --one-file-system -- "$P"
sync
if [ -e "$P" ]; then echo "ECHEC : la cible existe encore"; exit 17; fi
echo "SUPPRIME : $P"
echo "libre apres : $(df -m --output=avail / | tail -1 | tr -d ' ') Mo"
echo "prometheus : $(systemctl is-active prometheus) ; WAL actif intact : $(sudo -n du -sm "$LIVE" | cut -f1) Mo"
"""


def parse_releve(sortie):
    d = {}
    for ligne in (sortie or "").splitlines():
        if "=" in ligne:
            k, v = ligne.split("=", 1)
            d[k.strip()] = v.strip()

    def entier(cle):
        v = d.get(cle, "")
        return int(v) if v.lstrip("-").isdigit() else None
    return {"actif": d.get("ACTIVE") == "active", "maintenant": entier("NOW"), "wal_mo": entier("WALMB"),
            "segments": entier("SEGS"), "libre_mo": entier("FREEMB"), "derniere_coupe": entier("LASTBLOCK"),
            "corruptions": entier("CORRUPT"), "echecs": entier("FAILED"), "dossier": d.get("DIRDATA") == "oui",
            "demarrage": entier("START"), "echecs_depuis": entier("FAILSINCE"),
            "checkpoints_depuis": entier("CKPTSINCE")}


def decider(f, now_force=False):
    """Faits -> (autorise, raisons de refus, avertissements, minutes avant la prochaine fenetre ou None). Pure."""
    refus, avert = [], []
    if not f.get("actif"):
        refus.append("Prometheus n'est pas actif : rien a reparer par ce chemin")
    if not f.get("dossier"):
        refus.append("dossier de donnees %s introuvable" % DONNEES)
    if (f.get("corruptions") or 0) < 1:
        refus.append("aucune erreur « compaction failed … corruption in segment » dans les 5 dernieres heures : pas de preuve de corruption")
    if (f.get("wal_mo") or 0) < WAL_MIN_MO:
        refus.append("WAL de %s Mo seulement : rien a reparer" % f.get("wal_mo"))
    if f.get("libre_mo") is not None and f["libre_mo"] < LIBRE_MIN_MO:
        refus.append("espace libre insuffisant (%s Mo) pour un redemarrage serein" % f["libre_mo"])
    attente = None
    now, coupe = f.get("maintenant"), f.get("derniere_coupe")
    if coupe is None or now is None:
        if not now_force:
            refus.append("derniere coupe de 2 h inconnue : utiliser --now pour accepter la perte de la tete recente")
    else:
        depuis = now - coupe
        if depuis > FENETRE_APRES_COUPE_S:
            attente = max(0, (coupe + 7200 - now)) // 60
            if now_force:
                avert.append("hors fenetre : la tete recente (%d min de metriques) sera perdue" % (depuis // 60))
            else:
                refus.append("pas dans la fenetre : derniere coupe il y a %d min ; la prochaine est attendue dans ~%d min ; relancer --apply "
                             "dans les 10 min qui la suivent (ou --now)" % (depuis // 60, attente))
    return (not refus), refus, avert, attente


def verifier(f):
    """Apres la coupe de 2 h SUIVANT la reparation : le WAL se tronque-t-il enfin ? Retourne (ok, constats). Pure.

    On ne compte que les echecs survenus DEPUIS le redemarrage de Prometheus (les anciens ne disent rien de la reparation)."""
    coupe, dem = f.get("derniere_coupe"), f.get("demarrage")
    if dem is None or coupe is None or coupe <= dem:
        return False, ["aucune coupe de 2 h depuis le redemarrage de Prometheus : attendre la prochaine (heures UTC impaires)"]
    ok, constats = True, []
    if not f.get("checkpoints_depuis"):
        # Prometheus ne fait un checkpoint qu'avec au moins 3 segments de WAL : une coupe sans checkpoint (« write block » et
        # « Head GC » seulement) n'a RIEN prouve. Constate le 12 nov. 2026 : les coupes de 11:00Z n'en ont pas fait.
        return False, ["aucun « WAL checkpoint complete » depuis le redemarrage : la troncature n'a pas encore ete exercee "
                       "(il faut au moins 3 segments de WAL) ; attendre la coupe suivante"]
    if (f.get("echecs_depuis") or 0) > 0:
        ok = False
        constats.append("%d compaction(s) echouee(s) depuis le redemarrage" % f["echecs_depuis"])
    if f.get("wal_mo") is None or f["wal_mo"] >= 400:
        ok = False
        constats.append("WAL de %s Mo (attendu < 400 apres une troncature reussie)" % f.get("wal_mo"))
    if ok:
        constats.append("%d checkpoint(s) du WAL reussi(s) depuis le redemarrage, aucune erreur de compactage, WAL de %s Mo : le WAL se tronque"
                        % (f["checkpoints_depuis"], f["wal_mo"]))
    return ok, constats


def ssh(commande, entree=None, delai=240):
    """Execute `commande` sur .160. L'entree standard (script bash) part en OCTETS avec des fins de ligne LF : en mode texte,
    Windows la convertirait en CRLF et bash la rejetterait (`$'\\r': command not found`, erreur de syntaxe) — constate le
    12 nov. 2026 a la premiere tentative de reparation, qui a echoue AVANT toute action."""
    try:
        donnees = entree.replace("\r\n", "\n").encode("utf-8") if entree is not None else None
        p = subprocess.run(SSH + [commande], input=donnees, capture_output=True, timeout=delai)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except Exception as e:
        return 255, "", str(e)


def releve():
    rc, out, _ = ssh("bash -s", entree=RELEVE, delai=90)
    return parse_releve(out)


def afficher(f, autorise, refus, avert, attente):
    print("ETAT DE PROMETHEUS SUR .160 (%s)" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("  service actif            : %s" % ("oui" if f["actif"] else "NON"))
    print("  WAL                      : %s Mo en %s segments" % (f["wal_mo"], f["segments"]))
    print("  erreurs de corruption    : %s (5 dernieres heures) ; compactions echouees (total) : %s" % (f["corruptions"], f["echecs"]))
    if f.get("derniere_coupe") and f.get("maintenant"):
        print("  derniere coupe de 2 h    : il y a %d min" % ((f["maintenant"] - f["derniere_coupe"]) // 60))
    print("  espace libre             : %s Mo" % f["libre_mo"])
    for a in avert:
        print("  [AVERTISSEMENT] " + a)
    for r in refus:
        print("  [REFUS] " + r)
    if autorise:
        print("=> Reparation possible maintenant.")
    elif attente is not None:
        print("=> Pas maintenant : prochaine fenetre dans ~%d min." % attente)


def principal(argv=None):
    ap = argparse.ArgumentParser(description="Reparation du WAL corrompu de Prometheus (.160)")
    ap.add_argument("--apply", action="store_true", help="REPARE (production : autorisation requise)")
    ap.add_argument("--now", action="store_true", help="repare meme hors fenetre (perd la tete recente)")
    ap.add_argument("--verify", action="store_true", help="controle apres la coupe suivante")
    ap.add_argument("--supprimer-wal-deplace", action="store_true",
                    help="SUPPRIME le WAL mis de cote (SIMULATION sans --apply ; autorisation distincte requise)")
    ap.add_argument("--chemin", default=WAL_DEPLACE_DEFAUT, help="dossier mis de cote a supprimer")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if a.supprimer_wal_deplace:
        ok, raison = chemin_supprimable(a.chemin)
        if not ok:
            print("REFUS : %s (%s)" % (a.chemin, raison))
            return 4
        rc, out, err = ssh("bash -s -- %s%s" % (shlex.quote(a.chemin), " --apply" if a.apply else ""), entree=SUPPRESSION, delai=300)
        print(out)
        if err.strip():
            print(err.strip()[:300])
        return rc
    f = releve()
    if f["maintenant"] is None:
        print("ABANDON : .160 injoignable ou releve illisible")
        return 2
    if a.verify:
        ok, constats = verifier(f)
        print("VERIFICATION (%s) : WAL %s Mo ; echecs de compactage depuis le redemarrage : %s ; derniere coupe : %s" % (
            time.strftime("%H:%M:%S"), f["wal_mo"], f["echecs_depuis"],
            time.strftime("%H:%M:%S", time.localtime(f["derniere_coupe"])) if f.get("derniere_coupe") else "inconnue"))
        for c in constats:
            print("  - " + c)
        print("=> %s" % ("OK : le WAL se tronque de nouveau" if ok else "PAS ENCORE OK : attendre la coupe suivante ou investiguer"))
        return 0 if ok else 1
    autorise, refus, avert, attente = decider(f, a.now)
    afficher(f, autorise, refus, avert, attente)
    if not a.apply:
        print("\nSIMULATION : rien n'a ete modifie. Pour reparer (autorisation explicite requise) : "
              "python scripts/helios-ops/repair_prometheus_wal_160.py --apply")
        return 0
    if not autorise:
        return 3
    print("\nREPARATION EN COURS (Prometheus sera arrete environ 30 s)...")
    rc, out, err = ssh("bash -s", entree=REPARATION, delai=420)
    print(out)
    if err.strip():
        print(err.strip()[:300])
    if rc != 0:
        print("ECHEC (code %d) : voir ci-dessus ; l'etat d'origine est restaure si Prometheus ne redemarrait pas." % rc)
        return rc or 1
    print("TERMINE. A faire apres la coupe de 2 h SUIVANTE : python scripts/helios-ops/repair_prometheus_wal_160.py --verify")
    print("Le WAL mis de cote n'est PAS supprime : sa suppression demande une autorisation distincte.")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
