# -*- coding: utf-8 -*-
"""HELIOS — LOT HOST-EXEC-KEY-ONLY-NOPASSWD-ALIGNMENT-01 (base : SUDO-SECRET-CHANNEL-FIX-01).

Transport SSH de l'opération HÔTE whitelistée. Alignement sur les accès réels (LOT 09.5 : clé OK + sudo
NOPASSWD sur les 3 nœuds). Propriétés :
  * authentification SSH **PAR CLÉ UNIQUEMENT** — AUCUN repli par mot de passe ; échec clé -> refus explicite ;
  * AUCUN usage du credential `HELIOS/watchdog/ssh` dans ce chemin (plus de `ssh_pass`) ;
  * `sudo` **NON INTERACTIF SANS mot de passe** (`sudo -n -p ''`) : aucun mot de passe dans la commande, aucun
    mot de passe sur stdin ; si l'op n'est pas NOPASSWD, `sudo -n` échoue proprement (fail-closed) ;
  * identité d'hôte vérifiée : known_hosts contrôlé + RejectPolicy (fail-closed) ;
  * commande = valeur whitelistée `host_ops[op]`, quotée (`shlex`) -> pas d'injection ;
  * pas de pseudo-terminal (`get_pty=False`) ; timeouts, code de retour, erreurs sans secret, fermeture client.

Importable SANS effet de bord ; `paramiko` INJECTABLE pour les tests (aucune connexion SSH réelle en test).
"""
import os
import shlex


def build_sudo_invocation(cmd):
    """Commande distante SANS secret, sudo NON INTERACTIF : `sudo -n -p '' bash -c <cmd quoté>`.
    `-n` = jamais d'invite ni de lecture stdin (échoue si un mot de passe serait requis) ; `-p ''` neutralise
    l'invite. `cmd` (valeur whitelistée HOST_OPS) est quoté par `shlex` (aucune injection ; commandes composées
    `&&`/`||` via `bash -c`). AUCUN mot de passe n'est intégré."""
    return "sudo -n -p '' bash -c " + shlex.quote(cmd)


def _safe_close(c):
    try:
        c.close()
    except Exception:
        pass


def run_host_op(node, op, *, ip, ssh_user, ssh_key_path, known_hosts_path, host_ops,
                paramiko_mod=None, logger=None, cmd_timeout=40):
    """Exécute l'op HÔTE whitelistée PAR CLÉ. Retourne (ok, texte). AUCUN mot de passe (ni AUTH SSH, ni sudo).
    Ordre : (1) op whitelistée, (2) ancre known_hosts obligatoire (fail-closed), (3) RejectPolicy + clé chargée,
    (4) connexion PAR CLÉ (échec -> refus explicite, aucun repli mot de passe), (5) `sudo -n` sans secret,
    (6) code de retour + fermeture. Jamais de secret en log/exception/retour."""
    def _warn(m):
        if logger is not None:
            logger.warning(m)

    if paramiko_mod is None:
        return False, "paramiko indisponible sur l'hote"
    cmd = host_ops.get(op)
    if not ip or not cmd:
        return False, f"noeud/op inconnu: {node}/{op}"
    if not (known_hosts_path and os.path.isfile(known_hosts_path) and os.path.getsize(known_hosts_path) > 0):
        _warn(f"HOST-EXEC {node}/{op} refuse: known_hosts absent (identite SSH non verifiable)")
        return False, "known_hosts absent: identite SSH non verifiable"
    try:
        key = paramiko_mod.Ed25519Key.from_private_key_file(ssh_key_path)
    except Exception:
        _warn(f"HOST-EXEC {node}/{op} refuse: cle SSH introuvable/illisible")
        return False, "cle SSH introuvable ou illisible"
    c = None
    try:
        c = paramiko_mod.SSHClient()
        c.load_system_host_keys(known_hosts_path)                   # ancre de confiance (lecture seule)
        c.set_missing_host_key_policy(paramiko_mod.RejectPolicy())  # hote inconnu / cle changee -> REJET
        # AUTH SSH PAR CLÉ UNIQUEMENT — aucun repli mot de passe. Échec -> except ci-dessous (refus explicite).
        c.connect(ip, username=ssh_user, pkey=key, timeout=8, allow_agent=False, look_for_keys=False)
        # commande SANS secret ; sudo -n (aucun mot de passe, aucune écriture stdin).
        remote_cmd = build_sudo_invocation(cmd)
        _, out, err = c.exec_command(remote_cmd, timeout=cmd_timeout, get_pty=False)
        rc = out.channel.recv_exit_status()
        res = (out.read().decode(errors="replace") + err.read().decode(errors="replace")).strip()
        _safe_close(c)
        if logger is not None:
            logger.info(f"HOST-EXEC {node}/{op} -> rc={rc} ({res[:150] or 'done'})")  # jamais le secret
        return (rc == 0), (res or "done")
    except paramiko_mod.BadHostKeyException:
        _safe_close(c)
        _warn(f"HOST-EXEC {node}/{op}: identite d'hote SSH invalide")
        return False, "identite d'hote SSH invalide (known_hosts)"
    except paramiko_mod.AuthenticationException:
        _safe_close(c)
        _warn(f"HOST-EXEC {node}/{op}: authentification par cle refusee")
        return False, "authentification SSH par cle refusee"
    except paramiko_mod.SSHException:
        _safe_close(c)
        _warn(f"HOST-EXEC {node}/{op}: hote SSH inconnu ou connexion refusee")
        return False, "hote SSH inconnu ou connexion refusee"
    except Exception as e:
        _safe_close(c)
        _warn(f"HOST-EXEC {node}/{op}: echec ({type(e).__name__})")   # type seul, jamais le secret/detail
        return False, "echec host-exec"
