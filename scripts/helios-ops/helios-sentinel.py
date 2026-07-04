#!/usr/bin/env python3
"""HELIOS Sentinel — capteur EXTERNE de santé cluster.

Tourne sur .161 (à côté de Camunda + MailHog), DONC survit à la chute du cluster Kubernetes.
Poll le plan de contrôle (VIP + 3 control-plane) et les nœuds DE L'EXTÉRIEUR toutes les POLL s.
Rôle : détecter ce que l'intérieur du cluster ne peut PLUS voir quand il tombe —
API_SERVER_DOWN / NODE_UNREACHABLE / PARTIAL_CLUSTER_OUTAGE / COMPLETE_CLUSTER_OUTAGE.

Quand une panne est confirmée (anti-rebond DEBOUNCE) :
  1) journalise localement (survit)          -> /var/log/helios-sentinel.log
  2) alerte HORS cluster via MailHog (.161)   -> survit même backend down
  3) si le backend est joignable : crée l'incident -> Camunda le détecte/remédie
     sinon : met en file, et le POST au RETOUR du backend (traçabilité de l'outage total).
Le rallumage des VMs reste au watchdog hôte (séparation des rôles). La Sentinelle DÉTECTE+ALERTE.
"""
import ssl, socket, http.client, urllib.request, json, time, smtplib, logging, os
from email.mime.text import MIMEText

# ─────────── configuration ───────────
VIP            = ("192.168.148.150", 6443)                     # endpoint API HA (control-plane)
CP_NODES       = {"k8s-master": "192.168.148.153",
                  "k8s-worker1": "192.168.148.158",
                  "k8s-worker2": "192.168.148.155"}            # les 3 sont control-plane
KUBELET_PORT   = 10250
BACKEND        = "http://192.168.148.158:30880"                # NodePort (frontend->backend)
LOGIN          = {"username": "superadmin", "password": "Helios2026!"}
MAILHOG        = ("127.0.0.1", 1025)                           # SMTP local .161 (survit)
MAIL_FROM      = "helios-sentinel@helios.local"
MAIL_TO        = ["houssamsalhi972@gmail.com"]
POLL_SECONDS   = 15
DEBOUNCE       = 3            # nb de cycles consécutifs avant de confirmer un état (anti-flapping)
LOG_FILE       = "/var/log/helios-sentinel.log"
QUEUE_FILE     = "/opt/helios-sentinel/pending.json"

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [SENTINEL] %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("sentinel")

# ─────────── sondes bas niveau ───────────
def tcp_ok(host, port, t=4):
    try:
        with socket.create_connection((host, port), timeout=t):
            return True
    except OSError:
        return False

def healthz_ok(host, port, t=5):
    """GET https://host:port/healthz (cert auto-signé accepté) -> True si 'ok'."""
    ctx = ssl._create_unverified_context()
    try:
        conn = http.client.HTTPSConnection(host, port, timeout=t, context=ctx)
        conn.request("GET", "/healthz")
        r = conn.getresponse(); body = r.read().decode(errors="replace"); conn.close()
        return r.status == 200 and "ok" in body.lower()
    except Exception:
        return False

def backend_reachable(t=5):
    """N'importe quelle réponse HTTP du NodePort = la couche in-cluster est vivante."""
    try:
        urllib.request.urlopen(BACKEND + "/", timeout=t)
        return True
    except urllib.error.HTTPError:
        return True                      # 403/404 = ça répond = up
    except Exception:
        return False

# ─────────── évaluation de l'état ───────────
def probe():
    api_vip = healthz_ok(*VIP)
    cp_ok   = {n: healthz_ok(ip, 6443) for n, ip in CP_NODES.items()}     # apiserver par nœud
    node_up = {n: tcp_ok(ip, KUBELET_PORT) for n, ip in CP_NODES.items()} # kubelet = VM vivante
    return {"api_vip": api_vip, "cp_ok": cp_ok, "node_up": node_up,
            "healthy_cp": sum(cp_ok.values()), "nodes_alive": sum(node_up.values()),
            "backend": backend_reachable()}

def classify(p):
    """-> (state, severity, detail). HEALTHY quand tout est vert."""
    n = len(CP_NODES); alive = p["nodes_alive"]; hcp = p["healthy_cp"]
    dead_nodes = [x for x, up in p["node_up"].items() if not up]
    if p["api_vip"] and hcp == n and alive == n:
        return ("HEALTHY", "LOW", "plan de contrôle + nœuds OK")
    if alive == 0 and not p["api_vip"]:
        return ("COMPLETE_CLUSTER_OUTAGE", "CRITICAL", "aucun nœud joignable, API HS")
    # quorum etcd/API : la VIP tient tant qu'une majorité de control-plane répond
    if not p["api_vip"] or hcp < (n // 2 + 1):
        return ("API_SERVER_DOWN", "CRITICAL",
                f"VIP={'ok' if p['api_vip'] else 'KO'}, apiservers sains={hcp}/{n}")
    if dead_nodes:
        return ("NODE_UNREACHABLE", "HIGH", f"nœud(s) injoignable(s): {', '.join(dead_nodes)}")
    return ("PARTIAL_CLUSTER_OUTAGE", "HIGH", f"dégradation partielle (cp={hcp}/{n}, nœuds={alive}/{n})")

# ─────────── actions ───────────
def send_mail(subject, body):
    try:
        msg = MIMEText(body, _charset="utf-8")
        msg["Subject"] = subject; msg["From"] = MAIL_FROM; msg["To"] = ", ".join(MAIL_TO)
        with smtplib.SMTP(*MAILHOG, timeout=8) as s:
            s.sendmail(MAIL_FROM, MAIL_TO, msg.as_string())
        log.info("email envoyé (MailHog) : %s", subject)
    except Exception as e:
        log.warning("email KO: %s", e)

def _login():
    data = json.dumps(LOGIN).encode()
    req = urllib.request.Request(BACKEND + "/api/auth/login", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode()).get("accessToken")

def post_incident(itype, severity, detail):
    """Crée l'incident dans le backend -> Camunda le prend. Renvoie True si posté."""
    try:
        tok = _login()
        if not tok:
            return False
        body = {
            "title": f"{itype} : cluster (Sentinelle externe)",
            "description": f"Détection EXTERNE (Sentinelle .161) — {detail} | Type: {itype} "
                           f"| action=RESTART_NODE_VM | source=sentinel-externe",
            "severity": severity, "source": "OTHER",
            "namespace": "k8s-nodes", "app": "cluster",
        }
        req = urllib.request.Request(BACKEND + "/api/incidents", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + tok}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            iid = json.loads(r.read().decode()).get("id")
        log.info("incident #%s créé dans le backend -> Camunda (type=%s)", iid, itype)
        return True
    except Exception as e:
        log.warning("POST incident KO (backend injoignable ?): %s", e)
        return False

def queue_add(entry):
    q = queue_load(); q.append(entry)
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(q, f)

def queue_load():
    try:
        with open(QUEUE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def queue_flush():
    """Au retour du backend : poste les incidents mis en file pendant l'outage (traçabilité)."""
    q = queue_load()
    if not q or not backend_reachable():
        return
    rest = []
    for e in q:
        ok = post_incident(e["type"], e["severity"], "[différé] " + e["detail"])
        if not ok:
            rest.append(e)
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(rest, f)
    if q and not rest:
        log.info("file d'incidents différés vidée (%d) au retour du backend", len(q))

# ─────────── boucle principale ───────────
def main():
    log.info("HELIOS Sentinel démarré (poll=%ss, debounce=%s) — surveille le cluster depuis .161",
             POLL_SECONDS, DEBOUNCE)
    last_state = "HEALTHY"; pending_state = None; streak = 0
    while True:
        try:
            p = probe(); state, sev, detail = classify(p)
            if state == pending_state:
                streak += 1
            else:
                pending_state = state; streak = 1

            # confirmation par anti-rebond (HEALTHY confirmé aussi = récupération)
            if streak >= DEBOUNCE and state != last_state:
                if state == "HEALTHY":
                    log.info("RÉCUPÉRATION : cluster de nouveau sain (%s)", detail)
                    send_mail("[HELIOS] Cluster RÉTABLI", f"Le cluster est de nouveau sain.\n{detail}")
                    queue_flush()
                else:
                    log.warning("DÉTECTION %s (%s) — %s", state, sev, detail)
                    send_mail(f"[HELIOS] {sev} — {state}",
                              f"La Sentinelle externe (.161) a détecté :\n\n{state} ({sev})\n{detail}\n\n"
                              f"VIP={p['api_vip']} apiservers={p['healthy_cp']}/{len(CP_NODES)} "
                              f"nœuds={p['nodes_alive']}/{len(CP_NODES)} backend={p['backend']}")
                    if p["backend"] and post_incident(state, sev, detail):
                        pass                                   # Camunda a été nourri
                    else:
                        queue_add({"type": state, "severity": sev, "detail": detail})
                        log.info("backend injoignable -> incident %s mis en file (POST au retour)", state)
                last_state = state
            elif state != "HEALTHY":
                queue_flush()                                  # tente de vider la file dès que possible
        except Exception as e:
            log.error("cycle en erreur (non bloquant): %s", e)
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
