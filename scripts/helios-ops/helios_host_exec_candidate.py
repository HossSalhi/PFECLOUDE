# -*- coding: utf-8 -*-
"""HELIOS — LOT HOST-EXEC-LEAST-PRIVILEGE-REDESIGN-DEV-ONLY-01 — TRANSPORT CANDIDAT (redesign).

Remplace l'ancien transport `bash -c` par l'appel d'un HELPER privilégié dédié avec un IDENTIFIANT d'opération
d'un ensemble FERMÉ :

    sudo -n -p '' <helper_path> <operation_id>

Propriétés :
  * AUCUN `bash -c`, AUCUN `sudo -S`, AUCUNE commande shell arbitraire, AUCUN argument libre ;
  * `operation_id` validé contre un ensemble FERMÉ AVANT toute connexion (refus → aucune connexion) ;
  * `helper_path` est une constante fournie par l'appelant (chemin fixe), échappé par `shlex` ;
  * SSH PAR CLÉ UNIQUEMENT (aucun mot de passe, aucun `ssh_pass`), known_hosts + RejectPolicy (fail-closed),
    `get_pty=False`, timeouts, code retour, erreurs sans secret, fermeture du client.

NB sécurité : `exec_command` transmet une CHAÎNE au shell distant ; la sûreté repose donc sur le fait que la
chaîne est **uniquement** `sudo -n -p '' <helper> <op_id>` avec `op_id` d'un ensemble fermé et `helper` fixe —
PAS sur une quelconque élimination du shell par le protocole SSH. Importable sans effet de bord ; `paramiko`
INJECTABLE pour les tests (aucune connexion réelle en test)."""
import shlex


def build_helper_invocation(helper_path, op_id, allowed_ops):
    """Construit `sudo -n -p '' <helper_path> <op_id>` SANS shell libre. Lève ValueError si `op_id` n'est pas
    dans l'ensemble FERMÉ `allowed_ops` (jamais enrichi depuis l'entrée). helper_path + op_id quotés (shlex)."""
    if op_id not in allowed_ops:
        raise ValueError("operation_id non autorise")
    return "sudo -n -p '' " + shlex.quote(helper_path) + " " + shlex.quote(op_id)


def _safe_close(c):
    try:
        c.close()
    except Exception:
        pass


def run_host_op(node, op, *, ip, ssh_user, ssh_key_path, known_hosts_path, allowed_ops, helper_path,
                paramiko_mod=None, logger=None, cmd_timeout=40):
    """Exécute une opération HÔTE via le helper privilégié, PAR CLÉ. Retourne (ok, texte).
    Ordre : (1) op ∈ ensemble fermé (sinon refus AVANT connexion), (2) known_hosts obligatoire (fail-closed),
    (3) clé chargée, (4) connexion PAR CLÉ (échec -> refus explicite, aucun repli), (5) `sudo -n <helper> <op>`
    (aucun bash -c, aucun mot de passe), (6) code retour + fermeture. Jamais de secret en log/exception/retour."""
    def _warn(m):
        if logger is not None:
            logger.warning(m)

    if paramiko_mod is None:
        return False, "paramiko indisponible sur l'hote"
    if op not in allowed_ops:
        _warn(f"HOST-EXEC {node}/{op}: operation non autorisee -> refus (aucune connexion)")
        return False, "operation non autorisee"
    if not ip:
        return False, f"noeud inconnu: {node}"
    import os
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
        c.load_system_host_keys(known_hosts_path)
        c.set_missing_host_key_policy(paramiko_mod.RejectPolicy())
        c.connect(ip, username=ssh_user, pkey=key, timeout=8, allow_agent=False, look_for_keys=False)
        remote_cmd = build_helper_invocation(helper_path, op, allowed_ops)   # sudo -n <helper> <op> ; pas de bash -c
        _, out, err = c.exec_command(remote_cmd, timeout=cmd_timeout, get_pty=False)
        rc = out.channel.recv_exit_status()
        res = (out.read().decode(errors="replace") + err.read().decode(errors="replace")).strip()
        _safe_close(c)
        if logger is not None:
            logger.info(f"HOST-EXEC {node}/{op} -> rc={rc} ({res[:150] or 'done'})")  # jamais de secret
        return (rc == 0), (res or "done")
    except paramiko_mod.BadHostKeyException:
        _safe_close(c); _warn(f"HOST-EXEC {node}/{op}: identite d'hote SSH invalide")
        return False, "identite d'hote SSH invalide (known_hosts)"
    except paramiko_mod.AuthenticationException:
        _safe_close(c); _warn(f"HOST-EXEC {node}/{op}: authentification par cle refusee")
        return False, "authentification SSH par cle refusee"
    except paramiko_mod.SSHException:
        _safe_close(c); _warn(f"HOST-EXEC {node}/{op}: hote SSH inconnu ou connexion refusee")
        return False, "hote SSH inconnu ou connexion refusee"
    except Exception as e:
        _safe_close(c); _warn(f"HOST-EXEC {node}/{op}: echec ({type(e).__name__})")
        return False, "echec host-exec"
