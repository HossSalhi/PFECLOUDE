#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent Windows HELIOS DISK en lecture seule.

Une seule route POST existe : /v1/preflight. Elle reutilise les collecteurs REELS deja
presents dans helios_disk_test.py et la porte helios_disk_e2e.py. Aucun endpoint de
commande, aucune injection, aucune action VM, aucune modification Kubernetes/Bridge.

Authentification HMAC-SHA256 bidirectionnelle, horodatage, nonce anti-rejeu et IP
appelante strictement limitee a 192.168.148.158. Le watchdog VM_DOWN :9099 est distinct.
"""
import argparse
import collections
import hashlib
import hmac
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import helios_disk_test as H  # noqa: E402
import helios_disk_e2e as E  # noqa: E402
import helios_diskfill_service as S  # noqa: E402

CONTRAT = 1
CIBLE = "158"
SCENARIO = "DISK_E2E_158"
IMAGE_BACKEND = "pfe-backend:0.2.165-disk-e2e"
BIND = "192.168.148.1"
PORT = 19158
APPELANT_AUTORISE = "192.168.148.158"
CHEMIN = "/v1/preflight"
CLE_PAR_DEFAUT = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "HELIOS", "disk-host-agent.key")
MAX_CORPS = 4096
MAX_SKEW_S = 30
MAX_NONCES = 256


def _canon(role, timestamp, nonce, body):
    return ("%s\n%d\n%s\n" % (role, timestamp, nonce)).encode("ascii") + body


def signature(cle, role, timestamp, nonce, body):
    return hmac.new(cle, _canon(role, timestamp, nonce, body), hashlib.sha256).hexdigest()


def lire_cle(chemin=CLE_PAR_DEFAUT):
    """Lit la cle privee locale. ACL Windows restrictives posees par l'installateur."""
    st = os.stat(chemin)
    if not os.path.isfile(chemin) or os.path.islink(chemin):
        raise ValueError("fichier de cle absent, non regulier ou lien symbolique")
    with open(chemin, "rb") as f:
        brut = f.read(257)
    texte = brut.decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", texte):
        raise ValueError("format de cle invalide")
    return bytes.fromhex(texte)


CODES_ORANGE_AUTORISES = frozenset(("GEL_HOTE_48H", E.CODE_INCIDENTS_ANCIENS))


def _preflight_autorisable(verdict, go, raisons):
    if go is not True or not isinstance(raisons, list) or not all(isinstance(x, dict) for x in raisons):
        return False
    niveaux = [x.get("level") for x in raisons]
    if any(niveau not in ("INFO", "ORANGE") for niveau in niveaux):
        return False
    oranges = [x for x in raisons if x.get("level") == "ORANGE"]
    if verdict == "VERT":
        return not oranges and all(niveau == "INFO" for niveau in niveaux)
    if verdict == "ORANGE":
        return bool(oranges) and all(x.get("code") in CODES_ORANGE_AUTORISES for x in oranges)
    return False


def calculer_preflight():
    """Meme collecte reelle que le pre-vol DISK, mais sans verifier l'etat actif de l'unite qui demarre.

    Ne PAS appeler helios_diskfill_service.preflight_complet ici : evaluer_service()
    verrait l'unite principale en etat 'activating' pendant ExecStartPre et s'auto-refuserait.
    Les controles locaux de service restent dans diskfill.py verifier sur .158.
    """
    deps = H.Dependances()
    faits, ctx = H.collecter_reel(deps, CIBLE, H.VM_BASE_DEFAUT)
    faits_e2e = E.collecter_faits_e2e(deps, ctx["exe"], ctx["primaire"])
    rapport = E.evaluer_e2e(
        faits, faits_e2e, E.parametres(), CIBLE,
        image_backend_attendue=IMAGE_BACKEND,
        acquittes=("GEL_HOTE_48H", E.CODE_INCIDENTS_ANCIENS),  # deux ORANGE historiques strictement autorises
    )
    # Controle d'integrite du programme installe, sans lire systemctl is-active : pendant ExecStartPre
    # l'unite principale est legitimement 'activating'. Les empreintes des unites sont verifiees localement
    # par diskfill.py verifier, comparees a SHA_UNITES fige dans le programme.
    integrity = []
    sommes = S.sommes_calculees()
    if S.lire_sommes_fichier() != sommes:
        integrity.append({"level": "ROUGE", "code": "SVC_SOMMES_DEPOT",
                          "message": "SHA256SUMS du depot absent ou perime", "remedy": "revoir la source de verite"})
    D = S.noeud_module()
    rsha = ctx["exe"].ssh(H.CIBLES[CIBLE][1], ["sudo", "-n", "sha256sum", "--", D.CHEMIN_SCRIPT], 30)
    lu = None
    if rsha.rc == 0:
        for ligne in (rsha.out or "").splitlines():
            m = re.fullmatch(r"([0-9a-f]{64})\s+(.+)", ligne.strip())
            if m and m.group(2) == D.CHEMIN_SCRIPT:
                lu = m.group(1)
                break
    if lu != sommes.get("diskfill.py"):
        integrity.append({"level": "ROUGE", "code": "SVC_SHA_DISKFILL",
                          "message": "SHA-256 diskfill.py installe illisible ou different de la source Windows",
                          "remedy": "reinstaller depuis la source de verite apres relecture"})
    if integrity:
        rapport["raisons"].extend((x["level"], x["message"], x["remedy"], x["code"]) for x in integrity)
        rapport["verdict"] = "ROUGE"
        rapport["go"] = False
    raisons = [
        {"level": niveau, "message": str(message), "remedy": str(remede or ""), "code": str(code or "")}
        for niveau, message, remede, code in rapport["raisons"]
    ]
    hote = (faits or {}).get("hote") or {}
    pages = (faits_e2e or {}).get("hote") or {}
    vmdk = hote.get("vmdk") if isinstance(hote, dict) else None
    resume = {
        "ram_available_mo": hote.get("ram_mo"),
        "c_free_go": hote.get("c_libre_go"),
        "vmware_gels_48h": len(hote.get("gels") or []) if isinstance(hote.get("gels"), list) else None,
        "pages_read_s": pages.get("pages_s"),
        "vmdk_type": vmdk.get("type") if isinstance(vmdk, dict) else None,
        "vmdk_snapshots": vmdk.get("snapshots") if isinstance(vmdk, dict) else None,
    }
    return {
        "verdict": rapport.get("verdict"),
        "go": rapport.get("go") is True,
        "reasons": raisons,
        "host": resume,
    }


def _payload(request_timestamp, request_nonce, generated_epoch, verdict, go, reasons, host=None):
    return {
        "contract": CONTRAT,
        "target": CIBLE,
        "scenario": SCENARIO,
        "image_backend": IMAGE_BACKEND,
        "request_timestamp": request_timestamp,
        "request_nonce": request_nonce,
        "generated_epoch": generated_epoch,
        "verdict": verdict,
        "go": bool(go),
        "reasons": reasons,
        "host": host or {},
    }


def creer_handler(cle_path=CLE_PAR_DEFAUT, collecteur=calculer_preflight, horloge=time.time):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HELIOS-DISK-HostAgent/1"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            # Un client incomplet ne doit pas bloquer le serveur mono-thread.
            self.connection.settimeout(8)

        def log_message(self, _fmt, *_args):
            # Pas de donnees de diagnostic ou de secret dans stdout/stderr de la tache Windows.
            return

        def _reponse(self, code, objet, cle=None, ts=None, nonce=None):
            brut = json.dumps(objet, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=us-ascii")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("Content-Length", str(len(brut)))
            if cle is not None and ts is not None and nonce is not None:
                self.send_header("X-Helios-Signature", signature(cle, "RESP", ts, nonce, brut))
            self.end_headers()
            self.wfile.write(brut)
            self.close_connection = True

        def do_GET(self):  # noqa: N802
            self._reponse(405, {"status": "METHOD_NOT_ALLOWED"})

        def do_POST(self):  # noqa: N802
            cle = None
            authentifie = False
            ts = None
            nonce = None
            if self.path.split("?", 1)[0] != CHEMIN:
                self._reponse(404, {"status": "NOT_FOUND"})
                return
            if self.client_address[0] != APPELANT_AUTORISE:
                self._reponse(403, {"status": "CALLER_NOT_ALLOWED"})
                return
            try:
                longueur = int(self.headers.get("Content-Length", "-1"))
                if longueur < 1 or longueur > MAX_CORPS:
                    raise ValueError("taille de requete invalide")
                brut = self.rfile.read(longueur)
                if len(brut) != longueur:
                    raise ValueError("corps incomplet")
                ts = int(self.headers.get("X-Helios-Time", ""))
                nonce = self.headers.get("X-Helios-Nonce", "")
                sig = self.headers.get("X-Helios-Signature", "")
                if not re.fullmatch(r"[0-9a-f]{32}", nonce):
                    raise ValueError("nonce invalide")
                if not re.fullmatch(r"[0-9a-f]{64}", sig):
                    raise ValueError("signature invalide")
                maintenant = int(horloge())
                if abs(maintenant - ts) > MAX_SKEW_S:
                    raise ValueError("horodatage hors fenetre")
                req = json.loads(brut.decode("ascii"))
                if (not isinstance(req, dict) or req.get("contract") != CONTRAT or req.get("target") != CIBLE or
                        req.get("scenario") != SCENARIO or req.get("image_backend") != IMAGE_BACKEND or
                        req.get("timestamp") != ts or req.get("nonce") != nonce):
                    raise ValueError("requete hors contrat")
                cle = lire_cle(cle_path)
                attendu = signature(cle, "REQ", ts, nonce, brut)
                if not hmac.compare_digest(sig, attendu):
                    self._reponse(401, {"status": "AUTH_FAILED"})
                    return
                authentifie = True
                vu = self.server.nonces
                if nonce in vu:
                    self._reponse(409, {"status": "REPLAY_REJECTED"})
                    return
                vu.append(nonce)
                resultat = collecteur()
                if not isinstance(resultat, dict):
                    raise ValueError("collecteur sans resultat structure")
                # Seuls deux ORANGE connus peuvent etre autorises localement ; aucun acquittement HTTP distant.
                raisons = resultat.get("reasons")
                if not isinstance(raisons, list) or not all(isinstance(x, dict) for x in raisons):
                    raise ValueError("raisons de pre-vol illisibles")
                verdict = resultat.get("verdict")
                go = _preflight_autorisable(verdict, resultat.get("go"), raisons)
                if any(x.get("level") == "ROUGE" for x in raisons):
                    verdict, go = "ROUGE", False
                elif verdict == "VERT" and not go:
                    verdict = "ROUGE"
                elif verdict == "ORANGE" and resultat.get("go") is True and not go:
                    verdict = "ROUGE"
                elif verdict not in ("VERT", "ORANGE", "ROUGE"):
                    verdict, go = "ROUGE", False
                objet = _payload(ts, nonce, horloge(), verdict, go, raisons, resultat.get("host"))
                self._reponse(200, objet, cle, ts, nonce)
            except Exception as exc:  # echec sur : attestation ROUGE signee seulement apres authentification
                if authentifie and cle is not None and ts is not None and nonce is not None:
                    objet = _payload(ts, nonce, horloge(), "ROUGE", False,
                                     [{"level": "ROUGE", "code": "AGENT_PREFLIGHT_ERROR",
                                       "message": "collecte Windows impossible (%s)" % exc.__class__.__name__, "remedy": "corriger l'agent et refaire le pre-vol"}])
                    self._reponse(200, objet, cle, ts, nonce)
                    return
                self._reponse(503, {"status": "PREFLIGHT_UNAVAILABLE"})

    return Handler


def servir(cle_path=CLE_PAR_DEFAUT):
    # Verifie la cle avant d'ouvrir le port : absence/integrite invalide = aucun ecouteur.
    lire_cle(cle_path)
    serveur = HTTPServer((BIND, PORT), creer_handler(cle_path))
    serveur.nonces = collections.deque(maxlen=MAX_NONCES)
    serveur.timeout = 1
    try:
        print("HELIOS DISK Host Agent en ecoute sur %s:%d ; appelant autorise : %s" % (BIND, PORT, APPELANT_AUTORISE), flush=True)
        serveur.serve_forever(poll_interval=0.5)
    finally:
        serveur.server_close()


def principal(argv=None):
    ap = argparse.ArgumentParser(description="Agent Windows HELIOS DISK en lecture seule ; aucune commande VM ou d'injection.", allow_abbrev=False)
    ap.add_argument("--serve", action="store_true", required=True, help="demarre uniquement le serveur preflight en lecture seule")
    ap.add_argument("--key-file", default=CLE_PAR_DEFAUT, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    servir(a.key_file)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(principal())
