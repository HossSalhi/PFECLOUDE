#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""helios-alarme — l'alarme du monitoring = EXACTEMENT le verdict du detecteur.

Principe (demande explicite) : PAS de regle PromQL parallele qui re-derive la detection.
On lance le MEME `detecter.py` que Camunda appelle via la passerelle, et on publie
son resultat brut comme metrique Prometheus. Le monitoring (Prometheus + Grafana)
affiche donc le meme incident que Camunda detecte — une seule source de verite.

Expose sur :9110/metrics :
  helios_incident_present            0/1  (1 = le detecteur voit un incident)
  helios_incident{type,ns,cible} 1        (present uniquement quand incident actif)
  helios_detecteur_fiable            0/1
  helios_detecteur_derniere_sonde_timestamp
"""
import json, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

DETECTEUR = ["python3", "/opt/helios/detecter.py"]
INTERVALLE = 30           # cadence de sonde (le meme detecteur que Camunda)
PORT = 9110

_etat = {"present": 0, "type": "NO_INCIDENT", "ns": "aucun", "cible": "aucun",
         "nb": 0, "fiable": 1, "ts": 0, "preuve": ""}
_lock = threading.Lock()


def sonder():
    try:
        p = subprocess.run(DETECTEUR, capture_output=True, text=True, timeout=60)
        inc = json.loads(p.stdout)
        typ = inc.get("type", "NO_INCIDENT")
        with _lock:
            _etat.update(present=0 if typ == "NO_INCIDENT" else 1,
                         type=typ, ns=inc.get("ns", "aucun"), cible=inc.get("cible", "aucun"),
                         nb=inc.get("nbAnomalies", 0),
                         fiable=1 if inc.get("monitoringFiable", True) else 0,
                         ts=time.time(), preuve=str(inc.get("preuve", ""))[:120])
    except Exception as e:
        with _lock:
            _etat.update(fiable=0, ts=time.time(), preuve="detecteur illisible: %s" % e)


def boucle():
    while True:
        sonder()
        time.sleep(INTERVALLE)


def esc(s):
    return str(s).replace("\\", "").replace('"', "").replace("\n", " ")


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/metrics", "/"):
            self.send_response(404); self.end_headers(); return
        with _lock:
            e = dict(_etat)
        L = ["# HELP helios_incident_present 1 si le detecteur voit un incident, 0 sinon",
             "# TYPE helios_incident_present gauge",
             "helios_incident_present %d" % e["present"],
             "# HELP helios_detecteur_fiable 1 si le monitoring est fiable",
             "# TYPE helios_detecteur_fiable gauge",
             "helios_detecteur_fiable %d" % e["fiable"],
             "# TYPE helios_detecteur_derniere_sonde_timestamp gauge",
             "helios_detecteur_derniere_sonde_timestamp %d" % int(e["ts"]),
             "# HELP helios_incident_anomalies nombre d'anomalies vues par le detecteur",
             "# TYPE helios_incident_anomalies gauge",
             "helios_incident_anomalies %d" % int(e["nb"])]
        if e["present"]:
            L += ['# HELP helios_incident incident courant (= ce que Camunda va traiter)',
                  '# TYPE helios_incident gauge',
                  'helios_incident{type="%s",ns="%s",cible="%s"} 1'
                  % (esc(e["type"]), esc(e["ns"]), esc(e["cible"]))]
        b = ("\n".join(L) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    sonder()
    threading.Thread(target=boucle, daemon=True).start()
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
