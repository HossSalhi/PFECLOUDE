#!/usr/bin/env python3
"""
HELIOS — Watchdog d'auto-récupération des nœuds (sur l'HOTE).
Surveille l'état des workers K8s et RALLUME automatiquement tout worker NotReady
(Power Off / crash) via `vmrun start`. C'est le "bras" qui exécute la décision de
récupération que l'IA/Camunda ne peut pas faire (pas d'accès hyperviseur).

- Polling toutes les 15s (kubectl get nodes).
- Un worker NotReady >= GRACE secondes -> vmrun start <vmx> (power-on).
- Le MASTER est detecte par SONDE TCP DIRECTE (API 6443 / SSH 22), pas via kubectl :
  si le master tombe l'API tombe aussi -> kubectl ne peut pas servir de detecteur.
  master injoignable >= GRACE -> vmrun start (rallumage auto, comme les workers).
- Cooldown par nœud (évite de spammer).
- Ne fait QUE 'start' (jamais stop) -> sûr (ne peut que récupérer).
"""
# ==========================================================================================
# VERSION CANDIDATE — HELIOS-WATCHDOG-SECURE-RECONCILIATION-01 (LOT 08). *** NON DEPLOYEE ***
# Base = version DEPLOYEE (protections LOT 3BG/3AZ : jeton /restart, bind VMnet8, cooldown)
# + diagnostics LECTURE SEULE /host-diag (diag-cpu/diag-disk) via helios_diag_profiles +
#   helios_diag_ssh (SSH PAR CLE, known_hosts + RejectPolicy, fail-closed).
# SECRETS NON COPIES : HELIOS_PASS / SSH_PASS sources par variables d'environnement (defaut vide).
# Ne pas executer ici, ne pas demarrer, ne pas deployer sans la reconciliation du rapport 04.
# ==========================================================================================
import subprocess, json, time, logging, socket, threading
import hmac, os                      # LOT 3BG : comparaison de jeton a temps constant
import urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

# LOT 3BG - jeton partage avec le Bridge, seul appelant autorise de /restart.
# Cote Bridge : HELIOS_WATCHDOG_TOKEN dans /etc/helios/bridge.env.
# Fichier absent ou vide => l authentification est DESACTIVEE et le comportement
# d avant le 3BG est conserve : on ne veut pas qu une erreur de deploiement casse
# la seule voie de recuperation d une VM.
_JETON_FICHIER = r"C:\Users\houss\.helios-watchdog-token"
try:
    with open(_JETON_FICHIER, encoding="utf-8") as _f:
        JETON_ATTENDU = _f.read().strip()
except Exception:
    JETON_ATTENDU = ""
try:
    import paramiko  # pour /host-exec (SSH dans les VMs : systemctl restart kubelet/containerd, etc.)
except Exception:
    paramiko = None
try:
    # LOT 08 : diagnostics LECTURE SEULE reutilisant les modules TESTES (importables sans effet
    # de bord). Absents -> /host-diag fail-closed (503).
    import helios_diag_profiles as diagprof
except Exception:
    diagprof = None
try:
    import helios_diag_ssh as diagssh
except Exception:
    diagssh = None
try:
    # LOT 09.2 : fournisseur de secrets (Credential Manager Windows via ctypes, sans dependance).
    # Absent -> les secrets retombent sur l'env (compat) puis "" (fail-closed), jamais de valeur en dur.
    import helios_credstore as credstore
except Exception:
    credstore = None
try:
    # LOT 09.9 : transport host-exec REDESIGN (helper privilégié + op-id fermé ; PLUS de bash -c ni de
    # commande shell libre). Remplace l'ancien helios_host_exec (laissé en legacy). Importable/testable.
    import helios_host_exec_candidate as hostexec
except Exception:
    hostexec = None

LOG_FILE = r"C:\Users\houss\helios-autoheal.log"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [AUTOHEAL] %(message)s", datefmt="%H:%M:%S",
                    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger(__name__)

# Garde mono-instance : empêche 2 watchdogs en parallèle (Startup + tâche planifiée
# peuvent se lancer tous les deux). Le 2e n'arrive pas à réserver le port et sort.
_singleton = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _singleton.bind(("127.0.0.1", 49231))
    _singleton.listen(1)
except OSError:
    log.info("Une autre instance du watchdog tourne déjà — sortie.")
    raise SystemExit(0)

VMRUN = r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
NODES = {
    "k8s-worker1": r"C:\Users\houss\Documents\Virtual Machines\k8s-worker1\k8s-worker1.vmx",
    "k8s-worker2": r"C:\Users\houss\Documents\Virtual Machines\k8s-worker2\k8s-worker2.vmx",
}
# Le master : detecte par sonde DIRECTE (pas kubectl, car l'API EST sur le master).
MASTER = "k8s-master"
MASTER_IP = "192.168.148.153"
MASTER_API_PORT = 6443
MASTER_VMX = r"C:\Users\houss\Documents\Virtual Machines\k8s-master\k8s-master.vmx"
# Map vmx complet (workers + master) pour power_on.
ALL_VMX = {**NODES, MASTER: MASTER_VMX}
POLL = 15          # intervalle de polling (s)
GRACE = 120        # (conserve pour compat) — l'auto-rallumage autonome est desactive
COOLDOWN = 240     # ne pas re-rallumer le même nœud avant COOLDOWN s
HTTP_PORT = 9099   # API : POST /restart/<node> -> declenche vmrun (appele par Camunda)
# LOT 3BG : interface VMware VMnet8, seule utile. Voir le commentaire de start_http().
BIND_ADDR = os.environ.get("HELIOS_WATCHDOG_BIND", "192.168.148.1")

not_ready_since = {}   # node -> timestamp du 1er NotReady
last_action = {}       # node -> timestamp du dernier power-on

# --- Enregistrement de l'incident MASTER dans HELIOS ---------------------------
# Quand le master tombe, l'API K8s tombe -> le backend ne PEUT PAS detecter/enregistrer
# la panne master (il detecte via l'API). C'est donc le watchdog (qui detecte par sonde
# directe) qui POSTe l'incident a HELIOS, via un NodePort WORKER (backend+DB survivent
# sur les workers). NB: ceci enregistre "master DOWN -> rallumage auto"; AUCUN worker ne
# devient master (pas de HA control-plane sur ce materiel).
HELIOS_APIS = ["http://192.168.148.158:30880", "http://192.168.148.155:30880"]  # frontend proxy -> /api
HELIOS_USER = os.environ.get("HELIOS_SUPERADMIN_USER", "superadmin")
HELIOS_PASS = os.environ.get("HELIOS_SUPERADMIN_PASS", "")  # LOT 08 : secret NON copie -> env
master_incident_id = None  # id de l'incident master ouvert (pour le resoudre au retour)


def _superadmin_pass():
    """LOT 09.2 : secret superadmin = env (compat) PUIS Credential Manager. '' si indisponible
    (FAIL-CLOSED ; aucun substitut en dur). Jamais journalise."""
    if HELIOS_PASS:
        return HELIOS_PASS
    if credstore is not None:
        return credstore.get_secret("superadmin_pass", logger=log) or ""
    return ""


def _helios_login():
    """Retourne (base_url, jwt) ou (None, None). Essaie chaque worker.
    LOT 09.2 : le mot de passe vient du fournisseur securise (_superadmin_pass). Indisponible -> ECHEC
    EXPLICITE sans tenter de login ; n'impacte PAS /restart ni /host-diag (fonctions independantes)."""
    pw = _superadmin_pass()
    if not pw:
        log.warning("Helios: secret superadmin indisponible (env/credential) -> login non tente, "
                    "incident master non enregistre")
        return None, None
    for base in HELIOS_APIS:
        try:
            data = json.dumps({"username": HELIOS_USER, "password": pw}).encode()
            req = urllib.request.Request(base + "/api/auth/login", data=data,
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=8) as r:
                tok = json.loads(r.read().decode()).get("accessToken")
                if tok:
                    return base, tok
        except Exception:
            continue
    return None, None


def helios_report_master_down():
    """POST un incident 'Master DOWN' a HELIOS (best-effort). Ne bloque jamais l'auto-heal."""
    global master_incident_id
    if master_incident_id:
        return
    base, tok = _helios_login()
    if not tok:
        log.warning("Helios: login KO -> incident master non enregistre (sera reessaye)")
        return
    try:
        body = json.dumps({
            "title": "Infrastructure DOWN: k8s-nodes/k8s-master",
            "description": ("Master Kubernetes injoignable (API + etcd down). Recuperation "
                            "automatique: power-on de la VM par le watchdog hote (vmrun start "
                            "k8s-master). HELIOS reste accessible via les workers pendant la panne. "
                            "NB: aucun worker ne devient master (pas de HA control-plane)."),
            "severity": "CRITICAL", "source": "AI",
            "namespace": "k8s-nodes", "app": "k8s-master",
        }).encode()
        req = urllib.request.Request(base + "/api/incidents", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + tok}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            master_incident_id = json.loads(r.read().decode()).get("id")
            log.info("Helios: incident master enregistre (id=%s)", master_incident_id)
    except Exception as e:
        log.warning("Helios: POST incident master KO: %s", e)


def helios_report_master_up():
    """Resout l'incident master ouvert au retour du master (best-effort)."""
    global master_incident_id
    if not master_incident_id:
        return
    base, tok = _helios_login()
    if not tok:
        return
    try:
        req = urllib.request.Request(base + "/api/incidents/" + str(master_incident_id) + "/status/RESOLVED",
                                     data=b"", headers={"Authorization": "Bearer " + tok}, method="PUT")
        with urllib.request.urlopen(req, timeout=8) as r:
            log.info("Helios: incident master %s -> RESOLVED", master_incident_id)
        master_incident_id = None
    except Exception as e:
        log.warning("Helios: resolution incident master KO: %s", e)


def node_ready_map():
    """Retourne {node: True/False} via kubectl. Robuste aux erreurs transitoires."""
    try:
        out = subprocess.run(["kubectl", "get", "nodes", "-o", "json"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        data = json.loads(out.stdout)
        res = {}
        for item in data.get("items", []):
            name = item["metadata"]["name"]
            ready = False
            for c in item.get("status", {}).get("conditions", []):
                if c.get("type") == "Ready":
                    ready = (c.get("status") == "True")
            res[name] = ready
        return res
    except Exception as e:
        log.warning(f"kubectl get nodes échec (transitoire): {e}")
        return None


def is_reachable(ip, port, timeout=4):
    """Sonde TCP directe — NE depend PAS de l'API K8s (sert a detecter le master lui-meme)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def master_alive():
    """Le master est vivant si son API (6443) OU son SSH (22) repond a une sonde directe.
    Independant de kubectl : quand le master tombe, l'API tombe -> kubectl est muet."""
    return is_reachable(MASTER_IP, MASTER_API_PORT) or is_reachable(MASTER_IP, 22)


def power_on(node):
    vmx = ALL_VMX[node]
    try:
        r = subprocess.run([VMRUN, "start", vmx, "nogui"], capture_output=True, text=True, timeout=60,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        out = (r.stdout + r.stderr).strip()
        log.info(f"AUTO-HEAL: vmrun start {node} -> rc={r.returncode} {out or 'started'}")
        return True
    except Exception as e:
        log.error(f"AUTO-HEAL: échec vmrun start {node}: {e}")
        return False


# ── HOST-EXEC : commandes HÔTE dans les VMs via SSH (le "bras" host de Camunda) ──
# Camunda appelle POST /host-exec/<node>/<op> quand la résolution est une commande HÔTE
# (redémarrer kubelet/containerd, sync NTP, image GC…) — impossible via kubectl. L'agent SSH
# dans la VM (user kube) et exécute la commande en sudo. Whitelist stricte (jamais arbitraire).
NODE_IPS = {"k8s-worker1": "192.168.148.158", "k8s-worker2": "192.168.148.155", "k8s-master": MASTER_IP}
SSH_USER = os.environ.get("HELIOS_SSH_USER", "kube")
SSH_PASS = os.environ.get("HELIOS_SSH_PASS", "")  # LOT 09.6 : PLUS utilise par host_exec (cle seule) ; conserve compat/doc
SSH_KEY = r"C:\Users\houss\.ssh\id_ed25519"  # auth par CLÉ (éprouvée) ; fallback mot de passe
HOST_OPS = {
    "restart-kubelet": "systemctl restart kubelet",
    "restart-containerd": "systemctl restart containerd && systemctl restart kubelet",
    "sync-time": "chronyc makestep || systemctl restart chronyd || systemctl restart systemd-timesyncd",
    "image-gc": "crictl rmi --prune",
    "reboot": "systemctl reboot",
}
# LOT 09.9 : les CLÉS de HOST_OPS sont les identifiants d'opération (ensemble fermé) envoyés au helper ;
# les valeurs (chaînes de commande) deviennent de la DOCUMENTATION — l'exécution appartient désormais au
# helper privilégié (helios_host_ops_helper), PLUS au transport. Chemin CIBLE du helper (non installé ici) :
HELIOS_HOST_OPS_HELPER = "/usr/local/sbin/helios-host-ops"


def host_exec(node, op):
    """Délègue au transport REDESIGN (LOT 09.9) : envoie un IDENTIFIANT d'opération (clé HOST_OPS, ensemble
    fermé) au helper privilégié via `sudo -n -p '' <helper> <op>` — PLUS de `bash -c`, PLUS de commande shell,
    PLUS de mot de passe. SSH par clé + known_hosts/RejectPolicy préservés. Retourne (ok, texte)."""
    if paramiko is None:
        return False, "paramiko indisponible sur l'hôte"
    if hostexec is None:
        return False, "module host-exec indisponible"
    return hostexec.run_host_op(
        node, op, ip=NODE_IPS.get(node), ssh_user=SSH_USER, ssh_key_path=SSH_KEY,
        known_hosts_path=DIAG_KNOWN_HOSTS, allowed_ops=set(HOST_OPS), helper_path=HELIOS_HOST_OPS_HELPER,
        paramiko_mod=paramiko, logger=log)


# ── HOST-DIAG : diagnostic LECTURE SEULE (reconciliation LOT 08) ──────────────────
# Canal NOUVEAU et SEPARE de /host-exec (destructif), ajoute a la version deployee. Reutilise les
# modules testes. Profils FERMES ; jeton OBLIGATOIRE (meme fichier que /restart) ; allowlist ;
# SSH PAR CLE + known_hosts (RejectPolicy, fail-closed) ; timeouts/sorties bornes ; aucune commande libre.
DIAG_NODES = set(NODE_IPS)
DIAG_CMD_TIMEOUT = 15
DIAG_KNOWN_HOSTS = os.environ.get("HELIOS_KNOWN_HOSTS", r"C:\Users\houss\.ssh\known_hosts")


def diag_exec(node, profile):
    """Executeur injecte dans handle_host_diag. Diagnostic SSH LECTURE SEULE, PAR CLE UNIQUEMENT :
    delegue a helios_diag_ssh.run_diagnostic (aucun repli mot de passe ; known_hosts + RejectPolicy
    fail-closed ; erreurs sans secret). Retourne (ok, evidence|erreur)."""
    if diagprof is None:
        return False, "module diag indisponible"
    if diagssh is None:
        return False, "module diag ssh indisponible"
    if paramiko is None:
        return False, "paramiko indisponible sur l'hote"
    return diagssh.run_diagnostic(
        node, profile, ip=NODE_IPS.get(node), ssh_user=SSH_USER, ssh_key_path=SSH_KEY,
        known_hosts_path=DIAG_KNOWN_HOSTS, profile_commands=diagprof.profile_commands,
        build_evidence=diagprof.build_evidence, ssh_identity_source=diagprof.ssh_identity_source,
        cmd_timeout=DIAG_CMD_TIMEOUT, paramiko_mod=paramiko, logger=log)


def force_leader_reelection():
    """Quand un nœud tombe, le pod backend LEADER peut être dessus → la détection
    nœud (réservée au leader) est en pause jusqu'au TTL du lease (300s) → l'incident
    n'est pas créé. On vide `leader_lease` pour forcer une ré-élection IMMÉDIATE par
    un pod vivant → la détection reprend en ~30s et l'incident est créé. Best-effort."""
    try:
        prim = subprocess.run(
            ["kubectl", "get", "cluster", "postgres", "-n", "pfe",
             "-o", "jsonpath={.status.currentPrimary}"],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        if not prim:
            return
        subprocess.run(
            ["kubectl", "exec", "-n", "pfe", prim, "-c", "postgres", "--",
             "psql", "-U", "postgres", "-d", "pfe_db", "-c", "delete from leader_lease;"],
            capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW)
        log.info("Lease leader vidé -> ré-élection forcée (la détection nœud reprend sur un pod vivant)")
    except Exception as e:
        log.warning(f"force_leader_reelection échec (non bloquant): {e}")


# ── API HTTP : le watchdog devient le "bras" commande par Camunda ─────────────
# Le rallumage d'un worker n'est PLUS autonome : il ne se produit QUE si Camunda
# appelle POST /restart/<node>. Donc si Camunda est down, aucun rallumage -> la
# resolution des incidents ne se fait pas (choix operateur assume).
class WatchdogHTTP(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # pas de log HTTP par defaut

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/restart/"):
            # LOT 3BG - AUTHENTIFICATION. Jusqu ici l adaptateur acceptait un
            # /restart de n importe quel client. Le LOT 3BE avait etabli que le
            # Bridge en etait le seul appelant DE FAIT ; il en devient ici le seul
            # appelant AUTORISE. Le jeton est partage via /etc/helios/bridge.env
            # cote Bridge et ce fichier cote hote. GET /health reste ouvert : c est
            # une sonde de vivacite qui ne declenche aucune action.
            # LOT 08.1 - FAIL-CLOSED. Le deploye etait fail-OPEN si JETON_ATTENDU vide (pour ne pas casser
            # la recuperation). Le candidat REFUSE toute operation protegee des que l'authentification est
            # impossible : jeton non configure, absent ou invalide => 401, AUCUN power-on. Le jeton DOIT
            # etre provisionne au deploiement (prerequis documente). `not JETON_ATTENDU` d'abord : evite
            # le piege compare_digest("","")==True.
            recu = self.headers.get("X-Helios-Token", "")
            if not JETON_ATTENDU or not hmac.compare_digest(recu, JETON_ATTENDU):
                log.warning(f"POST /restart REFUSE : authentification impossible ou invalide "
                            f"(client {self.client_address[0]})")
                self._json(401, {"error": "jeton absent ou invalide"})
                return
            node = self.path.split("/restart/", 1)[1].strip("/")
            if node in ALL_VMX:
                # LOT 3AZ - IDEMPOTENCE. COOLDOWN et last_action existaient deja
                # (lignes 53 et 57) mais n etaient JAMAIS relus : last_action etait
                # ecrit puis ignore. On applique enfin la regle deja prevue.
                # POURQUOI : mesure du LOT 3AY. Un seul arret de .158 a produit TROIS
                # power-on, de deux origines independantes :
                #   - helios-camunda-full-cycle, dont le RetryGate revenait sur
                #     Executer (2 appels) ;
                #   - incident-remediation, workflow backend qui a traite le meme
                #     incident en parallele (1 appel).
                # Le watchdog est le SEUL point de convergence des deux chemins :
                # c est donc ici que "1 incident -> 1 power-on" peut etre garanti.
                # Un second appel devient un NO-OP explicite, pas une erreur : la VM
                # demarre bien, simplement elle ne redemarre pas deux fois.
                # LIMITE ASSUMEE : last_action vit en memoire. Un redemarrage du
                # watchdog reinitialise la fenetre. La protection couvre la rafale
                # de quelques dizaines de secondes qui suit un incident, ce qui est
                # exactement le cas observe au 3AY.
                depuis = time.time() - last_action.get(node, 0.0)
                if depuis < COOLDOWN:
                    log.info(f"HTTP /restart/{node} IGNORE (NO-OP) : power-on deja "
                             f"effectue il y a {int(depuis)}s, cooldown {COOLDOWN}s")
                    self._json(200, {"node": node, "started": False, "noop": True,
                                     "reason": "ALREADY_EXECUTED",
                                     "seconds_since_last": int(depuis),
                                     "cooldown": COOLDOWN})
                    return
                log.info(f"HTTP /restart/{node} recu (declenche par Camunda) -> power-on")
                ok = power_on(node)
                last_action[node] = time.time()
                self._json(200 if ok else 500, {"node": node, "started": ok})
            else:
                self._json(404, {"error": f"noeud inconnu: {node}"})
        elif self.path.startswith("/host-exec/"):
            parts = self.path.strip("/").split("/")  # ['host-exec', <node>, <op>]
            if len(parts) == 3:
                _, node, op = parts
                log.info(f"HTTP /host-exec/{node}/{op} recu (declenche par Camunda) -> SSH sudo systemctl")
                ok, res = host_exec(node, op)
                self._json(200 if ok else 500, {"node": node, "op": op, "ok": ok, "result": res[:400]})
            else:
                self._json(400, {"error": "usage: POST /host-exec/<node>/<op>"})
        elif self.path.startswith("/host-diag/"):
            # LECTURE SEULE. Jeton OBLIGATOIRE (meme fichier que /restart ; fail-closed pour le diag),
            # cible+profil verifies, SSH par cle, identite known_hosts. Decision deleguee UNE SEULE fois
            # a handle_host_diag ; diag_exec (SSH) atteint UNIQUEMENT sur autorisation 200.
            if diagprof is None:
                self._json(503, {"error": "module diag indisponible"})
                return
            code, body = diagprof.handle_host_diag(
                self.path, self.headers.get("X-Helios-Token", ""), JETON_ATTENDU,
                DIAG_NODES, diag_exec)
            if code == 401:
                log.warning("HTTP /host-diag refuse: authentification invalide")
            elif code in (200, 500):
                log.info(f"HTTP /host-diag -> {body.get('node')}/{body.get('profile')} ok={body.get('ok')}")
            self._json(code, body)
        else:
            self._json(404, {"error": "not found"})


def start_http():
    # LOT 3BG - EXPOSITION RESEAU. L ecoute etait sur 0.0.0.0, or l hote porte huit
    # interfaces IPv4 dont le Wi-Fi 192.168.1.13 : mesure du 2026-09-04, GET
    # http://192.168.1.13:9099/health rendait 200. L API de power-on etait donc
    # joignable depuis le reseau local. On se lie desormais a la seule interface
    # utile, VMware VMnet8, qui est EXACTEMENT l adresse que le Bridge utilise
    # (WATCHDOG = http://192.168.148.1:9099 dans bridge.py).
    srv = HTTPServer((BIND_ADDR, HTTP_PORT), WatchdogHTTP)
    log.info(f"API watchdog a l'ecoute sur {BIND_ADDR}:{HTTP_PORT} "
             f"(POST /restart/<node> AUTHENTIFIE, GET /health)")
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def main():
    log.info(f"Watchdog démarré — workers={list(NODES)} + master={MASTER} (sonde directe). "
             f"Rallumage workers = UNIQUEMENT via Camunda (POST /restart). Auto-rallumage autonome DESACTIVE.")
    start_http()  # expose POST /restart/<node> -> Camunda pilote le rallumage
    while True:
        now = time.time()

        # --- MASTER : detection DIRECTE + enregistrement de l'incident, MAIS PAS de rallumage auto ---
        # Choix operateur (desactive volontairement) : si le master tombe, il RESTE down jusqu'a un
        # rallumage MANUEL. On detecte et on trace l'incident, mais on ne fait JAMAIS vmrun start master.
        if master_alive():
            if MASTER in not_ready_since:
                log.info(f"{MASTER} de retour Ready (rallumage manuel)")
            not_ready_since.pop(MASTER, None)
        else:
            if MASTER not in not_ready_since:
                not_ready_since[MASTER] = now
                # ZÉRO EXCEPTION (choix opérateur) : le watchdog ne crée PLUS l'incident master en direct.
                # La détection du master passe DÉSORMAIS UNIQUEMENT par le backend
                # (InfrastructureHealthScheduler, via l'API qui survit sur la VIP HA .150) -> gate Camunda.
                # -> Camunda down = master NON détecté (comme tous les autres incidents). TOUT via Camunda.
                log.warning(f"{MASTER} injoignable — détection déléguée au backend/Camunda (API via VIP HA). "
                            f"AUCUN enregistrement direct par le watchdog, AUCUN rallumage auto (manuel).")
            # Volontairement : NI power_on(MASTER), NI POST direct. Tout passe par Camunda (0 exception).

        # --- WORKERS : detection seulement (PAS d'auto-rallumage). Le rallumage est declenche par
        #     Camunda via POST /restart/<node>. On debloque juste la detection backend ( re-election
        #     leader) pour que Camunda puisse creer l'incident + lancer le workflow rapidement. ---
        ready = node_ready_map()
        if ready is not None:
            for node in NODES:
                is_ready = ready.get(node, False)  # absent => considéré indisponible
                if is_ready:
                    not_ready_since.pop(node, None)
                    continue
                # NotReady (ou absent) — NE PAS rallumer ici (Camunda s'en charge via /restart)
                if node not in not_ready_since:
                    not_ready_since[node] = now
                    log.warning(f"{node} NotReady — PAS d'auto-rallumage (desactive). "
                                f"Camunda declenchera le restart via POST /restart/{node}.")
                    # débloque la détection backend si le leader était sur ce nœud -> Camunda peut agir
                    force_leader_reelection()
        time.sleep(POLL)


if __name__ == "__main__":
    main()
