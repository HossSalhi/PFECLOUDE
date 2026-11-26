#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HELIOS — LOT HOST-EXEC-LEAST-PRIVILEGE-REDESIGN-DEV-ONLY-01 — HELPER PRIVILÉGIÉ CANDIDAT.

Cible de conception (NON installée dans ce lot) : /usr/local/sbin/helios-host-ops, root:root, non modifiable
par `kube`. Reçoit UN SEUL identifiant d'opération d'un ENSEMBLE FERMÉ et exécute des commandes privilégiées
CODÉES EN DUR, en appels DIRECTS (`shell=False`, chemins absolus validés) — AUCUN shell, AUCUN `bash -c`,
AUCUN argument libre de l'appelant.

Usage cible : `helios-host-ops <operation_id>`  (exactement un argument).

Statut : CANDIDAT DEV-ONLY. Les chemins absolus des binaires doivent être CONFIRMÉS sur les nœuds avant tout
déploiement (voir `_resolve_abs`) ; tant qu'ils ne le sont pas, le candidat est **non prêt au déploiement**.
Testable par injection d'un `runner` simulé (aucun privilège root requis en test, aucune exécution réelle).
"""
import sys

# Ensemble FERMÉ d'identifiants autorisés (doit correspondre aux clés HOST_OPS du transport).
OP_IDS = ("restart-kubelet", "restart-containerd", "sync-time", "image-gc", "reboot")

# Chemins absolus CANDIDATS par binaire logique (emplacements FHS standard). Le chemin effectif est
# VALIDÉ au runtime (_resolve_abs) ; AUCUN n'est « inventé » comme certain -> validation + fail-closed.
BIN_CANDIDATES = {
    "systemctl": ("/usr/bin/systemctl", "/bin/systemctl"),
    "crictl": ("/usr/local/bin/crictl", "/usr/bin/crictl"),
    "chronyc": ("/usr/bin/chronyc", "/bin/chronyc"),
    # LOT 09.18 (revue) : timedatectl est requis par la POSTCONDITION de sync-time (_sync_confirmed) ;
    # il doit être résolu via _resolve_abs (chemin absolu validé), comme tout binaire exécuté.
    "timedatectl": ("/usr/bin/timedatectl", "/bin/timedatectl"),
}
SUBPROC_TIMEOUT = 120  # délai max par sous-processus (s)
SYNC_CONFIRM_ATTEMPTS = 5   # LOT 09.18 : nb max de contrôles de la postcondition de synchro
SYNC_CONFIRM_INTERVAL = 2   # LOT 09.18 : intervalle (s) entre deux contrôles de synchro


class HelperError(Exception):
    """Erreur contrôlée (jamais de détail sensible)."""


# ── Séquences d'opérations (sémantique préservée des chaînes HOST_OPS, SANS shell) ────────────────
def _op_restart_kubelet(run):
    return run("systemctl", ["restart", "kubelet"])


def _op_restart_containerd(run):
    # équivaut à `systemctl restart containerd && systemctl restart kubelet` : 2e étape SEULEMENT si 1re OK
    rc = run("systemctl", ["restart", "containerd"])
    if rc != 0:
        return rc
    return run("systemctl", ["restart", "kubelet"])


def _op_sync_time(run, bin_available=None, unit_available=None, sync_confirmed=None, sleep=None):
    """Synchronise l'heure via le premier mécanisme DISPONIBLE (chronyc -> chronyd -> systemd-timesyncd)
    PUIS EXIGE une POSTCONDITION explicite de synchronisation (LOT 09.18).

    Évolution des contrats :
      * LOT 09.16 : disponibilité vérifiée avant usage (un binaire absent ne fait plus échouer l'op).
      * LOT 09.18 : un `systemctl restart` renvoyant 0 ne suffit PLUS. Après une commande réussie
        (code 0), on CONFIRME l'état via un contrôle de synchronisation (`sync_confirmed`, attente
        BORNÉE). `sync-time` ne renvoie 0 QUE si la synchronisation est CONFIRMÉE.

    Tout le reste lève HelperError (fail-closed, AUCUNE fausse réussite) : aucun mécanisme disponible,
    commande en échec, ou commande OK mais synchro NON confirmée (contrôle indisponible / `no` / sortie
    absente ou mal formée / code non nul). On n'assimile PAS la présence de `systemctl` à celle d'un
    service de temps (unités vérifiées séparément). Dépendances INJECTABLES (disponibilité, lecture
    d'état, attente) : les tests ne touchent jamais aux services/horloge réels."""
    bin_av = bin_available if bin_available is not None else _bin_available
    unit_av = unit_available if unit_available is not None else _unit_available
    confirmed = sync_confirmed if sync_confirmed is not None else _sync_confirmed
    _sleep = sleep if sleep is not None else _default_sleep
    # (type de vérification, nom à vérifier, binaire à lancer, arguments fixes)
    mechanisms = (
        ("bin", "chronyc", "chronyc", ["makestep"]),
        ("unit", "chronyd", "systemctl", ["restart", "chronyd"]),
        ("unit", "systemd-timesyncd", "systemctl", ["restart", "systemd-timesyncd"]),
    )
    attempted = False
    for kind, name, binary, args in mechanisms:
        available = bin_av(name) if kind == "bin" else unit_av(name)
        if not available:
            continue                        # indisponible -> suivant (aucune hypothèse)
        attempted = True
        rc = run(binary, args)              # exception éventuelle = fail-closed (remonte)
        if rc != 0:
            continue                        # commande en échec -> mécanisme suivant disponible
        if _await_sync(confirmed, _sleep):  # POSTCONDITION confirmée (attente bornée) ?
            return 0                        # succès DÉMONTRÉ (pas seulement rc 0)
        # commande OK mais synchro NON confirmée -> pas un succès ; essayer le suivant disponible
    if not attempted:
        raise HelperError("sync-time: aucun mecanisme de synchro temporelle disponible")
    raise HelperError("sync-time: commande executee mais synchronisation NON confirmee (postcondition)")


def _op_image_gc(run):
    return run("crictl", ["rmi", "--prune"])


def _op_reboot(run):
    # commande de référence actuelle : `systemctl reboot`
    return run("systemctl", ["reboot"])


OPERATIONS = {
    "restart-kubelet": _op_restart_kubelet,
    "restart-containerd": _op_restart_containerd,
    "sync-time": _op_sync_time,
    "image-gc": _op_image_gc,
    "reboot": _op_reboot,
}


def _resolve_abs(binary_name):
    """Résout un binaire logique en chemin ABSOLU VALIDÉ : fichier régulier, propriétaire root (uid 0), NON
    modifiable par groupe/autres. Renvoie le 1er candidat valide, sinon lève HelperError (fail-closed).
    N'est appelé qu'à l'exécution réelle (on-node) ; jamais en test (runner injecté)."""
    import os
    import stat
    for path in BIN_CANDIDATES.get(binary_name, ()):
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if st.st_uid != 0:                      # doit appartenir à root
            continue
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):  # pas modifiable groupe/autres
            continue
        return path
    raise HelperError(f"binaire non resolu/valide: {binary_name}")


def _real_run(binary_name, args, capture=False):
    """Exécuteur RÉEL (on-node) : appel DIRECT (liste d'arguments, SANS interpréteur), chemin absolu validé,
    env minimal, timeout. Renvoie le code retour ; avec `capture=True`, renvoie le couple (code, stdout
    décodé) pour les contrôles de lecture (ex. postcondition de synchro). UNIQUE chemin d'exécution
    (aucune duplication). NON exécuté dans ce lot (tests = dépendances injectées)."""
    import subprocess
    path = _resolve_abs(binary_name)
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
    r = subprocess.run([path] + list(args), shell=False, check=False, timeout=SUBPROC_TIMEOUT,
                       capture_output=True, env=env)
    if capture:
        return r.returncode, r.stdout.decode(errors="replace")
    return r.returncode


def _bin_available(binary_name):
    """True si `binary_name` se résout en chemin absolu VALIDÉ (sinon False). Lecture seule, fail-safe.
    Sert à sélectionner un mécanisme de `sync-time` sans supposer sa présence (LOT 09.16)."""
    try:
        _resolve_abs(binary_name)
        return True
    except HelperError:
        return False


def _unit_available(unit_name):
    """Lecture seule : l'unité systemd `<unit_name>.service` existe-t-elle ? Réutilise l'UNIQUE chemin
    d'exécution validé `_real_run` (`systemctl cat <unit>.service`, code retour 0 = présente). N'exécute
    AUCUN redémarrage ; renvoie False si `systemctl` est introuvable. NE PAS assimiler la présence de
    `systemctl` à celle du service. Injecté en test (aucune commande réelle)."""
    try:
        return _real_run("systemctl", ["cat", unit_name + ".service"]) == 0
    except HelperError:
        return False


def _parse_ntp_synchronized(rc, out):
    """Postcondition de synchro (pur, testable) : True UNIQUEMENT si rc==0 ET une ligne EXACTE
    `NTPSynchronized=yes`. Code non nul, sortie absente/vide/mal formée, ou valeur != yes -> False.
    Ne lève jamais ; ne convertit JAMAIS l'indisponibilité en succès."""
    if rc != 0 or not out:
        return False
    for line in out.splitlines():
        if line.strip() == "NTPSynchronized=yes":
            return True
    return False


def _sync_confirmed():
    """Lecture seule : l'horloge est-elle SYNCHRONISÉE ? (`timedatectl show -p NTPSynchronized`).
    Réutilise l'UNIQUE chemin d'exécution validé `_real_run` (capture). False si `timedatectl` est
    introuvable/en erreur. AUCUNE modification. Injecté en test (jamais de timedatectl réel)."""
    try:
        rc, out = _real_run("timedatectl", ["show", "-p", "NTPSynchronized"], capture=True)
    except HelperError:
        return False
    return _parse_ntp_synchronized(rc, out)


def _await_sync(confirmed, sleep):
    """Attente BORNÉE de la postcondition : jusqu'à SYNC_CONFIRM_ATTEMPTS contrôles espacés de
    SYNC_CONFIRM_INTERVAL s. True dès qu'un contrôle est positif ; False après épuisement. Un contrôle
    qui lève n'est JAMAIS un succès."""
    for i in range(SYNC_CONFIRM_ATTEMPTS):
        try:
            if confirmed():
                return True
        except Exception:
            return False
        if i < SYNC_CONFIRM_ATTEMPTS - 1:
            sleep(SYNC_CONFIRM_INTERVAL)
    return False


def _default_sleep(seconds):
    """Attente réelle (on-node). Injectée en test par une fonction sans effet (déterminisme)."""
    import time
    time.sleep(seconds)


def run_op(op_id, runner):
    """Valide `op_id` (ensemble FERMÉ) et exécute la séquence via `runner(binary_name, args)->rc`.
    `runner` injecté en test. Renvoie le code retour (non nul = refus/échec)."""
    if op_id not in OPERATIONS:
        raise HelperError("operation inconnue")
    return OPERATIONS[op_id](runner)


def main(argv=None, runner=None):
    """Point d'entrée CLI. Exige EXACTEMENT un argument = un `op_id` autorisé. Rejette id inconnu, arguments
    supplémentaires, chaîne vide, fragments shell. Fail-closed. Renvoie un code retour (jamais d'exception
    propagée au shell appelant)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("usage: helios-host-ops <operation_id> (exactement un argument)\n")
        return 2
    op_id = args[0]
    if op_id not in OPERATIONS:          # ensemble fermé ; rejette aussi tout fragment shell (ne matche aucune clé)
        sys.stderr.write("operation refusee\n")
        return 2
    run = runner if runner is not None else _real_run
    try:
        rc = run_op(op_id, run)
    except HelperError:
        sys.stderr.write("operation refusee ou dependance indisponible\n")   # jamais de detail sensible
        return 3
    except Exception:
        sys.stderr.write("echec interne\n")
        return 4
    return int(rc) if isinstance(rc, int) else 5


if __name__ == "__main__":
    raise SystemExit(main())
