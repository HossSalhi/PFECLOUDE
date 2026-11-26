# -*- coding: utf-8 -*-
"""HELIOS — LOT WATCHDOG-DIAG-KEYONLY-ROUTE-AUDIT-01.

Transport SSH du diagnostic LECTURE SEULE. Propriétés garanties :
  * PAR CLÉ UNIQUEMENT — AUCUN repli par mot de passe sur le chemin de diagnostic ;
  * identité d'hôte vérifiée : known_hosts CONTRÔLÉ chargé AVANT connexion + RejectPolicy (fail-closed) ;
  * UNE SEULE tentative de connexion ; erreurs explicites SANS secret (ni jeton, ni mot de passe, ni clé) ;
  * sortie/temps bornés (délégués à l'appelant via build_evidence / timeout).

Importable SANS effet de bord (ni socket, ni logging.basicConfig, ni réseau, ni VM). `paramiko` est
INJECTABLE (`paramiko_mod`) pour des tests avec doubles, sans aucun appel SSH réel.
"""


def _safe_close(client):
    try:
        client.close()
    except Exception:
        pass


def run_diagnostic(node, profile, *, ip, ssh_user, ssh_key_path, known_hosts_path,
                   profile_commands, build_evidence, ssh_identity_source,
                   cmd_timeout=15, paramiko_mod=None, logger=None):
    """Exécute un profil de diagnostic LECTURE SEULE par SSH PAR CLÉ. Retourne (ok, evidence|erreur).
    Aucune commande libre : `profile_commands(profile)` fournit la liste fermée. Aucun mot de passe.
    L'ordre est strict : (1) ancre known_hosts obligatoire, (2) clé, (3) RejectPolicy + known_hosts,
    (4) UNE connexion par clé, (5) exécution. Tout échec en 1-4 => refus fermé, sans secret."""
    def _warn(msg):
        if logger is not None:
            logger.warning(msg)

    if paramiko_mod is None:
        return False, "paramiko indisponible sur l'hôte"
    cmds = profile_commands(profile)
    if not ip or cmds is None:
        return False, f"noeud/profil inconnu: {node}/{profile}"

    # (1) Ancre de confiance OBLIGATOIRE, AVANT toute connexion (fail-closed).
    ok_id, id_detail = ssh_identity_source(known_hosts_path)
    if not ok_id:
        _warn(f"HOST-DIAG {node}/{profile} refuse: {id_detail}")   # id_detail = raison, sans secret
        return False, id_detail

    # (2) Clé SSH UNIQUEMENT (aucun mot de passe). Clé absente/illisible => refus fermé.
    try:
        key = paramiko_mod.Ed25519Key.from_private_key_file(ssh_key_path)
    except Exception:
        _warn(f"HOST-DIAG {node}/{profile} refuse: cle SSH introuvable/illisible")
        return False, "cle SSH introuvable ou illisible"

    client = paramiko_mod.SSHClient()
    # (3) known_hosts chargé AVANT connect ; RejectPolicy => hôte inconnu / clé changée rejeté.
    client.load_system_host_keys(known_hosts_path)
    client.set_missing_host_key_policy(paramiko_mod.RejectPolicy())

    # (4) UNE SEULE tentative de connexion, PAR CLÉ. Pas de repli mot de passe. Erreurs sans secret.
    try:
        client.connect(ip, username=ssh_user, pkey=key, timeout=8,
                       allow_agent=False, look_for_keys=False)
    except paramiko_mod.BadHostKeyException:
        _safe_close(client)
        _warn(f"HOST-DIAG {node}/{profile} refuse: identite d'hote invalide")
        return False, "identite d'hote SSH invalide (cle known_hosts differente)"
    except paramiko_mod.AuthenticationException:
        _safe_close(client)
        _warn(f"HOST-DIAG {node}/{profile} refuse: authentification par cle refusee")
        return False, "authentification SSH par cle refusee"
    except paramiko_mod.SSHException:
        _safe_close(client)
        _warn(f"HOST-DIAG {node}/{profile} refuse: hote SSH inconnu ou connexion refusee")
        return False, "hote SSH inconnu ou connexion refusee"
    except Exception:
        _safe_close(client)
        _warn(f"HOST-DIAG {node}/{profile} refuse: echec de connexion SSH")
        return False, "echec de connexion SSH"

    # (5) Exécution du profil fermé (lecture seule, sans sudo). Bornes de temps par commande.
    try:
        raw = []
        for cmd in cmds:
            try:
                _, out, err = client.exec_command(cmd, timeout=cmd_timeout)
                rc = out.channel.recv_exit_status()
                raw.append({"command": cmd, "exit_code": rc,
                            "stdout": out.read().decode(errors="replace"),
                            "stderr": err.read().decode(errors="replace")})
            except Exception:
                raw.append({"command": cmd, "exit_code": None, "stdout": None,
                            "stderr": "echec d'execution de la commande"})
        _safe_close(client)
        if logger is not None:
            logger.info(f"HOST-DIAG {node}/{profile} -> {len(raw)} commandes (lecture seule)")
        return True, build_evidence(node, profile, "SNAPSHOT", raw)
    except Exception:
        _safe_close(client)
        return False, "echec d'execution du profil de diagnostic"
