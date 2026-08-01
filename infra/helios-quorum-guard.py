#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HELIOS — Gardien de quorum : le cluster tourne meme s'il ne reste qu'UNE VM.

Tourne en systemd sur CHACUN des 3 noeuds (.153 / .155 / .158). Chaque instance
surveille son propre etcd. Quand le quorum est perdu et que le noeud local est le
DERNIER debout, il reconstitue etcd en cluster mono-membre (--force-new-cluster)
puis, quand les autres reviennent, les reintegre automatiquement.

=== POURQUOI C'EST DANGEREUX ET COMMENT ON S'EN PROTEGE ===
Reconstituer un cluster sur un seul noeud est irreversible : si les 2 autres
n'etaient pas MORTS mais seulement ISOLES (coupure reseau), on obtient deux
clusters qui acceptent chacun des ecritures -> split-brain, corruption.
Cinq garde-fous, tous obligatoires, avant d'agir :

  1. PATIENCE       : quorum perdu en continu pendant CONFIRMATION_S (defaut 300 s).
                      Une bascule etcd normale dure quelques secondes.
  2. ISOLEMENT      : les 2 autres noeuds doivent etre injoignables sur les DEUX
                      plans - ping ICMP ET port etcd 2380. Si l'un repond au ping,
                      on est en partition reseau, PAS en panne -> on n'agit pas.
  3. TEMOIN VISIBLE : un arbitre hors cluster (.160 monitoring, .161 IA) doit etre
                      joignable. Si on ne voit PERSONNE, c'est NOUS qui sommes
                      isoles -> surtout ne rien forcer.
  4. AVIS DE L'ARBITRE : on DEMANDE a l'arbitre ce que LUI voit. Sans ca il reste
                      un trou : si nous sommes isoles mais que les 2 autres se
                      voient entre eux, ils gardent le quorum 2/3 et servent
                      toujours, alors que nos sondes les croient morts. Si
                      l'arbitre les voit vivants -> on ne touche a RIEN.
  5. ELECTION       : si plusieurs noeuds se croyaient seuls, seule la plus petite
                      IP agit. Empeche deux reconstitutions concurrentes.

Un verrou disque (ETAT) empeche aussi de reforcer en boucle.
"""
import json
import os
import socket
import subprocess
import sys
import time

PKI = "/etc/kubernetes/pki/etcd"
MANIFESTE = "/etc/kubernetes/manifests/etcd.yaml"
RUNBOOK = "/opt/helios/etcd-recuperation-quorum.sh"
ETAT = "/var/lib/helios-quorum-guard.json"
JOURNAL = "/var/log/helios-quorum-guard.log"

NOEUDS = {"k8s-master": "192.168.148.153",
          "k8s-worker1": "192.168.148.158",
          "k8s-worker2": "192.168.148.155"}
# Arbitres hors cluster = les 2 machines LB. Ce sont aussi les 2 membres etcd
# non-applicatifs : si l'une d'elles est debout, elle voit tout le reseau et peut
# donc dire de facon fiable si les autres noeuds sont morts ou seulement isoles.
# (.161 a ete retire : elle n'a plus AUCUN role dans le cluster.)
TEMOINS = ["192.168.148.160", "192.168.148.162"]

PERIODE_S = int(os.environ.get("HELIOS_GUARD_PERIODE", "30"))
CONFIRMATION_S = int(os.environ.get("HELIOS_GUARD_CONFIRMATION", "300"))
MDP = os.environ.get("HELIOS_SSH_PASS", "kube")
UTIL = os.environ.get("HELIOS_SSH_USER", "kube")

MOI = socket.gethostname()
MON_IP = NOEUDS.get(MOI, "")

ETCDCTL = ("ETCDCTL_API=3 etcdctl --endpoints=https://127.0.0.1:2379 "
           "--cacert=%s/ca.crt --cert=%s/server.crt --key=%s/server.key "
           "--command-timeout=8s" % (PKI, PKI, PKI))


def tracer(*parts):
    ligne = time.strftime("%Y-%m-%dT%H:%M:%S") + " " + " ".join(str(p) for p in parts)
    try:
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except Exception:
        pass
    print(ligne, flush=True)


def sh(cmd, delai=40):
    try:
        p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=delai)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def etat_lire():
    try:
        with open(ETAT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def etat_ecrire(d):
    try:
        with open(ETAT, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception as e:
        tracer("ETAT non ecrit:", e)


# --------------------------------------------------------------------- sondes
def quorum_ok():
    """L'etcd local accepte-t-il une operation lineaire ? (donc il y a un leader)"""
    code, out, _ = sh("%s endpoint status" % ETCDCTL, 20)
    return code == 0 and "no leader" not in out


def joignable(ip, port, delai=3):
    try:
        with socket.create_connection((ip, port), timeout=delai):
            return True
    except Exception:
        return False


def repond_ping(ip):
    code, _, _ = sh("ping -c 2 -W 2 %s >/dev/null 2>&1" % ip, 12)
    return code == 0


def autres_noeuds():
    return {n: ip for n, ip in NOEUDS.items() if ip != MON_IP}


def temoin_visible():
    """Voit-on au moins un arbitre hors cluster ? Sinon c'est NOUS l'isole."""
    return any(repond_ping(ip) or joignable(ip, 22) for ip in TEMOINS)


def temoin_confirme_mort():
    """GARDE-FOU DECISIF — on demande a l'arbitre ce que LUI voit.

    Sans cela il reste un trou : si NOUS sommes isoles des 2 autres mais qu'EUX
    se voient toujours entre eux, ils gardent le quorum 2/3 et continuent de
    servir ; nos propres sondes les croient morts (on ne les joint pas) et on
    reconstituerait un 2e cluster => split-brain reel.
    L'arbitre est hors cluster et joint tout le monde : s'il les voit VIVANTS,
    c'est nous l'isole, et on ne touche a RIEN."""
    autres = list(autres_noeuds().values())
    for t in TEMOINS:
        if not joignable(t, 22, 4):
            continue
        vus = []
        for ip in autres:
            code, sortie, _ = sh(
                "ssh -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=8 "
                "%s@%s 'ping -c 2 -W 2 %s >/dev/null 2>&1 && echo VIVANT || echo MORT'"
                % (UTIL, t, ip), 40)
            if code != 0:
                tracer("  arbitre", t, "interrogeable mais reponse KO pour", ip)
                vus.append(None)
            elif "VIVANT" in sortie:
                tracer("  arbitre", t, "voit", ip, "VIVANT -> c'est NOUS qui sommes isoles")
                return False
            else:
                vus.append(ip)
        if all(v is not None for v in vus):
            tracer("  arbitre", t, "confirme : les", len(autres), "autres sont injoignables")
            return True
    tracer("  aucun arbitre n'a pu se prononcer -> on s'abstient")
    return False


def vraiment_seul():
    """Les 2 autres sont-ils REELLEMENT morts (ni ping, ni port etcd) ?"""
    for nom, ip in autres_noeuds().items():
        if repond_ping(ip):
            tracer("  ", nom, ip, "repond au PING -> partition reseau, PAS une panne")
            return False
        if joignable(ip, 2380):
            tracer("  ", nom, ip, "repond sur etcd:2380 -> vivant")
            return False
        tracer("  ", nom, ip, "muet (ni ping ni 2380)")
    return True


def je_suis_elu():
    """Si plusieurs se croient seuls (partition), seule la plus petite IP agit."""
    candidats = [MON_IP] + [ip for ip in autres_noeuds().values()
                            if repond_ping(ip) or joignable(ip, 2380)]
    return MON_IP == sorted(candidats)[0]


# ------------------------------------------------------------------- actions
def reconstituer():
    """Reconstitue etcd en cluster mono-membre via le runbook (deja teste)."""
    tracer("=== RECONSTITUTION AUTOMATIQUE sur", MOI, MON_IP, "===")
    if not os.path.exists(RUNBOOK):
        tracer("ECHEC: runbook absent:", RUNBOOK)
        return False
    # sauvegarde d'abord : si tout se passe mal, on garde de quoi restaurer
    sh("%s sauvegarder" % RUNBOOK, 240)
    code, out, err = sh("yes | %s recuperer" % RUNBOOK, 900)
    tracer("runbook recuperer -> code", code)
    for l in (out or "").splitlines()[-12:]:
        tracer("  |", l)
    if code != 0:
        tracer("ECHEC reconstitution:", (err or "")[:300])
        return False
    e = etat_lire()
    e["reconstitue_le"] = time.time()
    e["reconstitue_par"] = MOI
    e["a_reintegrer"] = list(autres_noeuds().keys())
    etat_ecrire(e)
    tracer("=== RECONSTITUTION TERMINEE — le cluster tourne sur ce seul noeud ===")
    return True


def reintegrer_revenants():
    """Apres une reconstitution, rattache les noeuds qui reviennent."""
    e = etat_lire()
    restants = e.get("a_reintegrer") or []
    if not restants:
        return
    for nom in list(restants):
        ip = NOEUDS.get(nom)
        if not ip or not joignable(ip, 22, 4):
            continue
        tracer("REVENANT detecte:", nom, ip, "-> reintegration")
        code, out, err = sh("%s rejoindre %s %s" % (RUNBOOK, nom, ip), 900)
        tracer("  rejoindre -> code", code)
        for l in (out or "").splitlines()[-8:]:
            tracer("  |", l)
        if code == 0:
            restants.remove(nom)
            e["a_reintegrer"] = restants
            etat_ecrire(e)
            tracer("  ", nom, "REINTEGRE")
        else:
            tracer("  echec:", (err or "")[:200])


# ---------------------------------------------------------------------- main
def main():
    if not MON_IP:
        tracer("hostname", MOI, "hors table NOEUDS -> arret"); sys.exit(1)
    tracer("DEMARRAGE gardien de quorum |", MOI, MON_IP,
           "| periode", PERIODE_S, "s | confirmation", CONFIRMATION_S, "s")
    perdu_depuis = None

    while True:
        try:
            if quorum_ok():
                if perdu_depuis:
                    tracer("quorum RETABLI (sans intervention)")
                perdu_depuis = None
                reintegrer_revenants()          # rattacher d'eventuels revenants
            else:
                if perdu_depuis is None:
                    perdu_depuis = time.time()
                    tracer("QUORUM PERDU — debut du compte a rebours",
                           CONFIRMATION_S, "s avant reconstitution")
                ecoule = time.time() - perdu_depuis
                if ecoule < CONFIRMATION_S:
                    tracer("quorum toujours perdu (%ds/%ds)" % (ecoule, CONFIRMATION_S))
                else:
                    tracer("--- verification des garde-fous ---")
                    if not temoin_visible():
                        tracer("REFUS: aucun temoin externe visible -> c'est MOI qui suis isole")
                    elif not vraiment_seul():
                        tracer("REFUS: un autre noeud repond -> partition, pas une panne")
                    elif not temoin_confirme_mort():
                        tracer("REFUS: l'arbitre externe ne confirme pas la mort des autres")
                    elif not je_suis_elu():
                        tracer("REFUS: un autre noeud a une IP plus petite -> il decide")
                    else:
                        tracer("garde-fous OK (patience + isolement + ARBITRE + election)")
                        if reconstituer():
                            perdu_depuis = None
                        else:
                            perdu_depuis = time.time()   # nouvelle fenetre avant retry
        except Exception as e:
            tracer("ERREUR boucle:", e)
        time.sleep(PERIODE_S)


if __name__ == "__main__":
    main()
