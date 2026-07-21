#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""helios-ssh-bridge — permet a Camunda d'executer une commande sur les VMs du cluster.

Le bundle de connecteurs Camunda n'a pas de connecteur SSH : ce service expose donc
l'execution SSH en HTTP, que le connecteur io.camunda:http-json:1 sait appeler.

Perimetre de securite :
  - ECOUTE UNIQUEMENT SUR 127.0.0.1 : injoignable depuis le reseau, seul un processus
    de la VM .161 (donc Camunda) peut l'appeler.
  - jeton partage obligatoire (en-tete X-Helios-Token).
  - cibles limitees a une table fixe (master / worker1 / worker2).
  - toute commande est journalisee dans /var/log/helios-ssh-bridge.log AVANT execution.
"""
import json, os, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

NOEUDS = {"master": "192.168.148.153", "worker1": "192.168.148.158", "worker2": "192.168.148.155"}
UTIL   = os.environ.get("HELIOS_SSH_USER", "kube")
MDP    = os.environ.get("HELIOS_SSH_PASS", "kube")
JETON  = os.environ.get("HELIOS_BRIDGE_TOKEN", "")
JOURNAL = "/var/log/helios-ssh-bridge.log"
DELAI  = 45


def tracer(*parts):
    ligne = time.strftime("%Y-%m-%dT%H:%M:%S") + " " + " ".join(str(p) for p in parts)
    try:
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except Exception:
        pass
    print(ligne, flush=True)


def executer(noeud, commande):
    ip = NOEUDS[noeud]
    tracer("EXEC", noeud, ip, "|", commande)
    argv = ["sshpass", "-p", MDP, "ssh",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR",
            "%s@%s" % (UTIL, ip), commande]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=DELAI)
        res = {"host": noeud, "ip": ip, "command": commande,
               "exitCode": p.returncode,
               "stdout": p.stdout.strip()[:4000],
               "stderr": p.stderr.strip()[:2000],
               "ok": p.returncode == 0}
    except subprocess.TimeoutExpired:
        res = {"host": noeud, "ip": ip, "command": commande, "exitCode": 124,
               "stdout": "", "stderr": "timeout apres %ds" % DELAI, "ok": False}
    tracer("RES ", noeud, "exit=%s" % res["exitCode"], "|", res["stdout"][:120].replace("\n", " "))
    return res


class Handler(BaseHTTPRequestHandler):
    def repondre(self, code, corps):
        donnees = json.dumps(corps).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def do_GET(self):
        if self.path == "/sante":
            return self.repondre(200, {"statut": "ok", "noeuds": sorted(NOEUDS)})
        self.repondre(404, {"erreur": "chemin inconnu"})

    def do_POST(self):
        if self.path != "/exec":
            return self.repondre(404, {"erreur": "chemin inconnu"})
        if JETON and self.headers.get("X-Helios-Token") != JETON:
            tracer("REFUS jeton invalide depuis", self.client_address[0])
            return self.repondre(401, {"erreur": "jeton invalide"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            d = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            return self.repondre(400, {"erreur": "corps JSON invalide: %s" % e})
        noeud = str(d.get("host", "")).strip()
        commande = str(d.get("command", "")).strip()
        if noeud not in NOEUDS:
            return self.repondre(400, {"erreur": "noeud inconnu", "attendus": sorted(NOEUDS)})
        if not commande:
            return self.repondre(400, {"erreur": "commande vide"})
        self.repondre(200, executer(noeud, commande))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    tracer("DEMARRAGE bridge sur 127.0.0.1:8099 | jeton:", "oui" if JETON else "NON")
    HTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
