# -*- coding: utf-8 -*-
"""HELIOS — LOT WATCHDOG-READONLY-DIAGNOSTICS-01.

Logique PURE et TESTABLE des profils de diagnostic en LECTURE SEULE (CPU/DISK) du watchdog hôte.
Aucun effet de bord à l'import (ni socket, ni log, ni réseau, ni VM) : ce module est importé par le
watchdog et par les tests unitaires. Il ne fournit QUE des profils fermés — jamais de commande libre.
"""
import hmac
import os

# Taille maximale d'une sortie conservée (octets) — borne anti-sortie volumineuse.
MAX_OUTPUT_BYTES = 4096

# Profils FERMÉS, en lecture seule, à durée bornée. Identifiants fixes ; AUCUN champ de commande libre.
# (pas de sudo requis : toutes ces commandes sont lisibles par un utilisateur normal.)
DIAG_PROFILES = {
    "diag-cpu": [
        "date -Is",
        "uptime",
        "cat /proc/stat",            # echantillon 1 (avant)
        "cat /proc/stat",            # echantillon 2 (apres un court intervalle) -> util = f(delta)
        "systemctl is-active helios-cpuload.service",
        "systemctl show helios-cpuload.service -p CPUQuotaPerSecUSec",
    ],
    "diag-disk": [
        "date -Is",
        "df -hT /",
        "df -ih /",
        "lsblk",
    ],
}


def profile_commands(profile):
    """Liste figée des commandes d'un profil, ou None si le profil est inconnu (jamais une commande libre)."""
    return DIAG_PROFILES.get(profile)


def is_allowed(node, profile, allowed_nodes):
    """La cible doit appartenir à l'allowlist ET le profil doit être connu."""
    return bool(node) and node in (allowed_nodes or set()) and profile in DIAG_PROFILES


def auth_ok(received, expected):
    """Comparaison de jeton à temps constant. FAIL-CLOSED : refus si aucun jeton attendu n'est configuré."""
    if not expected:
        return False
    if not received:
        return False
    return hmac.compare_digest(str(received), str(expected))


def ssh_identity_source(known_hosts_path):
    """Détermine la source de confiance pour vérifier l'identité SSH de l'hôte distant.
    Retourne (ok, detail). FAIL-CLOSED : sans fichier known_hosts contrôlé et lisible, l'identité
    n'est PAS vérifiable -> (False, raison) -> le diagnostic doit être refusé (jamais d'AutoAddPolicy).
    N'invente aucune clé : se contente de constater la présence d'une ancre de confiance."""
    if not known_hosts_path:
        return (False, "known_hosts non defini: identite SSH non verifiable (fail-closed)")
    try:
        if os.path.isfile(known_hosts_path) and os.path.getsize(known_hosts_path) > 0:
            return (True, known_hosts_path)
    except Exception as e:
        return (False, f"known_hosts illisible: {e} (fail-closed)")
    return (False, f"known_hosts absent/vide: {known_hosts_path} -> identite SSH non verifiable (fail-closed)")


def authorize_diag(path, header_token, expected_token, allowed_nodes):
    """Décide l'autorisation d'une requête /host-diag SANS rien exécuter (ni SSH, ni réseau).
    Retourne (code, node, profile) : 400 usage, 401 auth, 404 cible/profil, 200 ok.
    L'ordre GARANTIT que l'authentification est vérifiée AVANT la cible, et qu'aucun appel
    distant (diag_exec) n'est atteignable tant que le code n'est pas 200."""
    parts = (path or "").strip("/").split("/")
    if len(parts) != 3 or parts[0] != "host-diag":
        return (400, None, None)
    _, node, profile = parts
    if not auth_ok(header_token, expected_token):
        return (401, node, profile)
    if not is_allowed(node, profile, allowed_nodes):
        return (404, node, profile)
    return (200, node, profile)


def handle_host_diag(path, header_token, expected_token, allowed_nodes, executor, diag_available=True):
    """Décision COMPLÈTE de la route /host-diag, INJECTABLE et testable SANS serveur HTTP ni SSH.
    `executor(node, profile)` (ex. diag_exec) n'est appelé QUE si l'autorisation vaut 200 ;
    toute requête refusée (400/401/404/503) retourne AVANT et ne peut PAS l'atteindre.
    Retourne (http_code, body_dict). `diag_available=False` => 503 (module diag absent)."""
    if not diag_available:
        return (503, {"error": "module diag indisponible"})
    code, node, profile = authorize_diag(path, header_token, expected_token, allowed_nodes)
    if code == 400:
        return (400, {"error": "usage: POST /host-diag/<node>/<diag-cpu|diag-disk>"})
    if code == 401:
        return (401, {"error": "authentification requise"})
    if code == 404:
        return (404, {"error": f"noeud/profil non autorise: {node}/{profile}"})
    ok, res = executor(node, profile)      # ATTEINT UNIQUEMENT SUR 200
    return (200 if ok else 500, {"node": node, "profile": profile, "ok": ok,
                                 "evidence": res if ok else None, "error": None if ok else res})


def truncate(text, limit=MAX_OUTPUT_BYTES):
    """Borne la taille d'une sortie (sans jamais inventer de contenu)."""
    if text is None:
        return None
    b = text.encode("utf-8", "replace")
    if len(b) <= limit:
        return text
    return b[:limit].decode("utf-8", "replace") + "...[tronque]"


def build_evidence(node, profile, phase, raw_outputs):
    """Normalise des sorties FOURNIES par l'exécuteur en preuve structurée.
    Une commande sans sortie => available=False (NON COLLECTÉ) ; aucune valeur inventée."""
    cmds = profile_commands(profile) or []
    by_cmd = {}
    for r in (raw_outputs or []):
        if isinstance(r, dict):
            by_cmd[r.get("command")] = r
    outputs = []
    for c in cmds:
        r = by_cmd.get(c)
        if r is None:
            outputs.append({"command": c, "available": False,
                            "exit_code": None, "stdout": None, "stderr": None})
        else:
            available = (r.get("stdout") is not None or r.get("exit_code") is not None)
            outputs.append({"command": c, "available": bool(available),
                            "exit_code": r.get("exit_code"),
                            "stdout": truncate(r.get("stdout")), "stderr": truncate(r.get("stderr"))})
    return {"node": node, "profile": profile, "phase": phase, "outputs": outputs}


def compute_cpu_util(stat_before, stat_after):
    """% d'utilisation CPU à partir de DEUX échantillons /proc/stat (ligne 'cpu'). None si incalculable.
    Méthode : util = (dTotal - dIdle) / dTotal, idle = idle + iowait. Un seul échantillon NE suffit PAS."""
    def parse(s):
        for line in (s or "").splitlines():
            p = line.split()
            if p and p[0] == "cpu":
                return [int(x) for x in p[1:] if x.lstrip("-").isdigit()]
        return None
    a, b = parse(stat_before), parse(stat_after)
    if not a or not b or len(a) < 5 or len(b) < 5:
        return None
    idle_a, idle_b = a[3] + a[4], b[3] + b[4]
    tot_a, tot_b = sum(a), sum(b)
    d_tot, d_idle = tot_b - tot_a, idle_b - idle_a
    if d_tot <= 0:
        return None
    return round(100.0 * (d_tot - d_idle) / d_tot, 1)
