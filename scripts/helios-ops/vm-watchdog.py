#!/usr/bin/env python3
"""
HELIOS — Watchdog central de redémarrage de nœud (L11 / RESTART_VM).

ARTEFACT — fourni mais NON déployé en prod (un endpoint HTTP->reboot est sensible,
et le valider = rebooter un nœud, donc disruptif). À déployer en connaissance de cause.

Le backend appelle  POST ${remediation.watchdog-base-url}/restart/<node>  quand
l'IA décide RESTART_VM. Ce service central (à lancer sur le master .153, qui a une
clé SSH vers les workers + sudo NOPASSWD sur les nœuds) reboote le nœud ciblé.

Sécurité : token obligatoire (header X-Watchdog-Token), liste blanche de nœuds,
binding sur l'IP interne uniquement.

Lancement (systemd recommandé) :
    WATCHDOG_TOKEN=$(openssl rand -hex 16) python3 vm-watchdog.py
Backend (configmap) :
    REMEDIATION_WATCHDOG_BASE_URL=http://192.168.148.153:9999
    + ouvrir le port 9999 dans backend-egress (NetworkPolicy) vers .153
Réponse attendue par le backend : HTTP 202 (cf. KubernetesRemediationExecutor.restartVm).
"""
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = (os.environ.get("WATCHDOG_BIND", "0.0.0.0"), int(os.environ.get("WATCHDOG_PORT", "9999")))
TOKEN = os.environ.get("WATCHDOG_TOKEN", "")
# Mapping nœud -> (hôte SSH, commande reboot). NOPASSWD sudo requis sur les workers.
NODES = {
    "k8s-worker1": "192.168.148.158",
    "k8s-worker2": "192.168.148.155",
    # master volontairement EXCLU : rebooter le control-plane est trop risqué.
}
_NODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def reboot_node(node: str) -> bool:
    host = NODES.get(node)
    if not host:
        return False
    # SSH (clé kube partagée) + sudo reboot NOPASSWD sur le worker.
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
           f"kube@{host}", "sudo systemctl reboot"]
    try:
        subprocess.Popen(cmd)  # fire-and-forget : le nœud part en reboot
        return True
    except Exception:
        return False


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, msg):
        self.send_response(code); self.end_headers(); self.wfile.write(msg.encode())

    def do_POST(self):
        if TOKEN and self.headers.get("X-Watchdog-Token") != TOKEN:
            return self._send(401, "unauthorized\n")
        m = re.match(r"^/restart/([^/]+)$", self.path)
        if not m:
            return self._send(404, "usage: POST /restart/<node>\n")
        node = m.group(1)
        if not _NODE_RE.match(node) or node not in NODES:
            return self._send(400, f"node inconnu ou non autorisé: {node}\n")
        if reboot_node(node):
            return self._send(202, f"reboot initié pour {node}\n")  # 202 attendu par le backend
        return self._send(500, f"échec reboot {node}\n")

    def log_message(self, *a):  # silence
        pass


if __name__ == "__main__":
    if not TOKEN:
        print("ATTENTION: WATCHDOG_TOKEN vide -> endpoint NON protégé. Définir un token.")
    print(f"[watchdog] écoute sur {LISTEN[0]}:{LISTEN[1]} — nœuds: {list(NODES)}")
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()
