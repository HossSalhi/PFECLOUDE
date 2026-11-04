#!/usr/bin/env python3
"""
HA-01 — boîte à outils commune des scripts d'INJECTION de panne (Phase 2b) et d'ARRÊT RÉEL de VM (Phase 4).
AUCUN effet à l'import. p2a_rehearsal.py reste autonome (il a déjà ses propres helpers) et n'importe pas ce module.

Contenu : SSH/kubectl/etcd/mémoire/canaris, minuterie de sécurité côté machine (systemd), détection DNS et état des
contrôleurs, extraction des fenêtres de sondes (Windows, .161 sans cache, .161 avec cache DNS), analyse « brut » ET « hors
gel d'E/S hôte » (écart E6), archivage du journal du Bridge HELIOS (D7), contexte d'exécution (dossier de résultats + journal).
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.path.normpath(os.path.join(HERE, "..", "probes"))
RES = os.path.normpath(os.path.join(HERE, "..", "results", "2026-11-03"))
if PROBES not in sys.path:
    sys.path.insert(0, PROBES)
import ha01_canary_report  # noqa: E402
import ha01_hoststall  # noqa: E402
import ha01_probe  # noqa: E402
import ha01_report  # noqa: E402

for _flux in (sys.stdout, sys.stderr):      # défaut Windows = cp1252 : les symboles du rapport feraient planter print()
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

NET = "192.168.148."
NODE_NAME = {"153": "k8s-master", "155": "k8s-worker2", "158": "k8s-worker1"}
WORKER_SUFFIX = {"k8s-worker1": "158", "k8s-worker2": "155"}
ETCD_ALL = ("153", "155", "158", "160", "162")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR"]
PROBE_WIN = os.path.join(RES, "probe-baseline-p0.jsonl")
SAMPLES = os.path.join(RES, "samples-baseline-p0.jsonl")
P161_FILE = "/opt/helios-backups/ha01/probe161-p0.jsonl"
TOOLS_161 = "/opt/helios-backups/ha01/tools"
BY_NAME = ("web", "api_app", "kube_api")          # sondes « par nom » (vues d'un vrai client)
# LB « actif » que le DNS à santé contrôlée publie seul tant qu'il est sain (HELIOS_DNS_ORDER). .162 depuis le 4 nov. (écart E12, ops/p2b_primary.py) ;
# les scripts ne codent plus cette valeur en dur : ils la lisent ici.
DNS_PRIMARY = "162"


# ----------------------------------------------------------------------------------------------- temps
def now():
    return datetime.now(timezone.utc)


def iso(t=None):
    return (t or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_ms(epoch_s):
    """Epoch -> « 2026-11-04T05:00:17.685Z » (même format que les sondes, comparable lexicographiquement)."""
    return datetime.fromtimestamp(epoch_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def epoch_of(ts):
    return datetime.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z" if "." in ts else "%Y-%m-%dT%H:%M:%S%z").timestamp()


# ----------------------------------------------------------------------------------------------- SSH / kubectl
def ssh(host, cmd, timeout=45, stdin=None):
    """host = « 160 » ou « 192.168.148.160 ». Retourne (code, sortie stdout). 124 = délai dépassé, 255 = SSH en échec."""
    ip = host if "." in host else NET + host
    try:
        # MODE BINAIRE obligatoire : avec text=True, Python/Windows convertit « \n » en « \r\n » dans ce qu'on envoie sur l'entrée de SSH, ce qui
        # corrompait les scripts shell (« #!/bin/sh\r » -> introuvable), les unités systemd et les configs poussés par `stdin` (constaté le 4 nov.).
        r = subprocess.run(SSH + ["kube@" + ip, cmd], capture_output=True, timeout=timeout,
                           input=None if stdin is None else stdin.replace("\r\n", "\n").encode("utf-8"))
        return r.returncode, r.stdout.decode("utf-8", "replace").strip()
    except subprocess.TimeoutExpired:
        return 124, ""


def kubectl(args, via=("155", "158", "153"), timeout=50):
    for h in via:
        rc, out = ssh(h, "kubectl --request-timeout=30s " + args, timeout)
        if rc not in (255, 124):
            return rc, out
    return 255, ""


def etcdctl(sub, eps=ETCD_ALL):
    e = ",".join("https://%s%s:2379" % (NET, s) for s in eps)
    return ("sudo -n env ETCDCTL_API=3 etcdctl --endpoints=%s --cacert=/etc/kubernetes/pki/etcd/ca.crt "
            "--cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key --command-timeout=6s %s" % (e, sub))


def etcd_healthy(via=("155", "158", "153"), eps=ETCD_ALL):
    """Nombre de membres sains parmi `eps` (par défaut les 5). Pendant l'isolation d'un membre, l'exclure évite 6 s d'attente par cycle."""
    for h in via:
        rc, out = ssh(h, etcdctl("endpoint health -w json", eps) + " 2>/dev/null", timeout=25)
        try:
            return sum(1 for e in json.loads(out) if e.get("health"))
        except Exception:  # noqa: BLE001
            continue
    return 0


def etcd_health_detail(via=("155", "158", "153"), eps=ETCD_ALL):
    """(nombre de membres sains, [suffixes des membres NON sains]) : pour savoir QUEL membre a échoué à un contrôle (écart E17)."""
    for h in via:
        rc, out = ssh(h, etcdctl("endpoint health -w json", eps) + " 2>/dev/null", timeout=25)
        try:
            arr = json.loads(out)
            bad = sorted(e["endpoint"].split("//")[1].split(":")[0].split(".")[-1] for e in arr if not e.get("health"))
            return sum(1 for e in arr if e.get("health")), bad
        except Exception:  # noqa: BLE001
            continue
    return 0, ["?"]


def etcd_term_leader(via=("155", "158", "153")):
    """(raftTerm max, id du leader) — le terme augmente à chaque élection : mesure les changements de leader."""
    for h in via:
        rc, out = ssh(h, etcdctl("endpoint status -w json") + " 2>/dev/null", timeout=25)
        try:
            st = json.loads(out)
            term = max(int(e["Status"]["raftTerm"]) for e in st)
            leader = [e["Status"]["leader"] for e in st if e["Status"]["leader"]][0]
            return term, leader
        except Exception:  # noqa: BLE001
            continue
    return None, None


def nodes_state():
    rc, out = kubectl("get nodes --no-headers")
    st = {}
    for l in out.splitlines():
        p = l.split()
        if len(p) >= 2:
            st[p[0]] = p[1]
    return st


def unhealthy_pods():
    rc, out = kubectl("get pods -A --no-headers")
    return [l for l in out.splitlines() if len(l.split()) > 3 and l.split()[3] not in ("Running", "Completed")]


def cnpg_state():
    rc, out = kubectl("-n pfe get cluster.postgresql.cnpg.io postgres -o jsonpath='{.status.phase}|{.status.currentPrimary}|{.status.readyInstances}'")
    return out.replace("'", "")


def mem_avail(host):
    rc, out = ssh(host, "awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo", timeout=15)
    return int(out) if rc == 0 and out.isdigit() else None


def host_avail_mb():
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                    ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return int(m.ullAvailPhys / 1048576)


def canary_ack_age(dep):
    """Âge (s) de la dernière écriture acquittée d'un canari (None si illisible)."""
    rc, out = kubectl("-n pfe logs deploy/%s --tail=3" % dep, timeout=40)
    last = None
    for l in out.splitlines():
        p = l.split(" ", 1)
        if len(p) == 2 and p[1].strip().isdigit():
            try:
                last = float(p[0])
            except ValueError:
                pass
    return None if last is None else time.time() - last


def file_age(path):
    return time.time() - os.path.getmtime(path) if os.path.exists(path) else 9e9


def tail_fail_run(path, name, tail_bytes=60000):
    """Durée (s) de l'échec ininterrompu EN COURS d'une sonde dans un JSONL local (0 si le dernier échantillon est OK)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return 0
    recs = []
    for l in lines:
        try:
            r = json.loads(l)
        except ValueError:
            continue
        if r.get("probe") == name:
            recs.append(r)
    run = 0
    for r in reversed(recs):
        if r["ok"]:
            break
        run += 1
    return run


# ----------------------------------------------------------------------------------------------- répartiteurs de charge / DNS
def lb_direct_ok(lb):
    """Le LB sert-il en direct (IP) le web et l'API Kubernetes ? Retourne (ok, détail)."""
    ok1, st1, _ip, err1 = ha01_probe.one_request(ha01_probe.TARGETS["lb%s_web" % lb])
    ok2, st2, _ip, err2 = ha01_probe.one_request(ha01_probe.TARGETS["lb%s_api" % lb])
    return ok1 and ok2, "web=%s api=%s %s" % (st1, st2, (err1 or err2 or ""))


def haproxy_all_up(lb):
    """(tous les serveurs UP ?, détail) d'après la page de stats du LB (CSV)."""
    import csv
    import io
    import urllib.request
    try:
        txt = urllib.request.urlopen("http://%s%s:8404/;csv" % (NET, lb), timeout=6).read().decode()
        rd = csv.reader(io.StringIO(txt))
        hdr = next(rd)
        hdr[0] = hdr[0].lstrip("# ")
        ix = {h: i for i, h in enumerate(hdr)}
        bad = [r[ix["pxname"]] + "/" + r[ix["svname"]] + "=" + r[ix["status"]] for r in rd
               if len(r) >= len(hdr) and r[ix["svname"]] not in ("FRONTEND", "BACKEND") and not r[ix["status"]].startswith("UP")
               and not (r[ix["pxname"]] == "stats")]
        return not bad, bad[:6]
    except Exception as e:  # noqa: BLE001
        return False, ["stats injoignables: %s" % type(e).__name__]


def dns_state(lb):
    """État du contrôleur DNS d'un LB (state.json) : dict ou {}."""
    rc, out = ssh(lb, "sudo -n cat /var/lib/helios-dns-health/state.json 2>/dev/null", timeout=20)
    try:
        return json.loads(out)
    except ValueError:
        return {}


def dns_answers(server_suffix, names=("helios.orange.tn", "api.helios.orange.tn")):
    """Réponses A (liste triée d'IP, TTL) de dnsmasq d'un LB, interrogé en direct depuis le poste."""
    out = {}
    for n in names:
        try:
            ips, ttl = ha01_probe._dns_query(n, NET + server_suffix, timeout=2.0)
            out[n] = (sorted(ips), ttl)
        except Exception as e:  # noqa: BLE001
            out[n] = (None, str(type(e).__name__))
    return out


class DnsPoller(threading.Thread):
    """Interroge dnsmasq des deux LB toutes les `period` s : horodate ce que publie chaque serveur DNS (bascule gating)."""

    def __init__(self, servers=("160", "162"), names=("helios.orange.tn", "api.helios.orange.tn"), period=1.0):
        super().__init__(daemon=True)
        self.servers, self.names, self.period = servers, names, period
        self.records = []
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            t0 = time.time()
            for s in self.servers:
                for n in self.names:
                    t1 = time.time()
                    try:
                        ips, ttl = ha01_probe._dns_query(n, NET + s, timeout=1.5)
                        rec = {"t": t1, "server": s, "name": n, "ips": sorted(i.split(".")[-1] for i in ips), "ttl": ttl}
                    except Exception as e:  # noqa: BLE001
                        rec = {"t": t1, "server": s, "name": n, "ips": None, "err": type(e).__name__}
                    rec["ms"] = int((time.time() - t1) * 1000)
                    self.records.append(rec)
            self._halt.wait(max(0.0, self.period - (time.time() - t0)))

    def stop(self):
        self._halt.set()

    def first_without(self, server, lb_suffix, after, name="helios.orange.tn"):
        """Premier instant (>= after) où `server` répond SANS l'IP du LB `lb_suffix` ; None si jamais."""
        for r in self.records:
            if r["server"] == server and r["name"] == name and r["t"] >= after and r["ips"] is not None and lb_suffix not in r["ips"]:
                return r["t"]
        return None

    def first_with(self, server, lb_suffix, after, name="helios.orange.tn"):
        for r in self.records:
            if r["server"] == server and r["name"] == name and r["t"] >= after and r["ips"] is not None and lb_suffix in r["ips"]:
                return r["t"]
        return None


# ----------------------------------------------------------------------------------------------- minuterie de sécurité (systemd, côté machine)
def deadman_arm(host, unit, seconds, command):
    """Arme une minuterie systemd (root, SUR la machine `host`, indépendante de ce script et de la session) qui exécutera
    `command` dans `seconds` s. `command` = un programme et ses arguments, SANS apostrophe. True si la minuterie est « active »."""
    cmd = ("sudo -n systemctl stop %(u)s.timer %(u)s.service 2>/dev/null; sudo -n systemctl reset-failed %(u)s.service 2>/dev/null; "
           "sudo -n systemd-run --quiet --unit=%(u)s --on-active=%(s)ds --property=Type=oneshot "
           "--description='HA-01 filet de securite' %(c)s >/dev/null 2>&1; sudo -n systemctl is-active %(u)s.timer"
           % {"u": unit, "s": int(seconds), "c": command})
    rc, out = ssh(host, cmd, timeout=30)
    return bool(out) and out.strip().splitlines()[-1] == "active"


def deadman_disarm(host, unit):
    u = unit
    return ssh(host, "sudo -n systemctl stop %s.timer %s.service 2>/dev/null; sudo -n systemctl reset-failed %s.service 2>/dev/null; echo ok" % (u, u, u), timeout=30)


UNISOLATE_SH = """#!/bin/sh
# HA-01 — retire les regles iptables d'isolation de TEST (commentaire ha01-isolate). Idempotent, sans effet s'il n'y en a pas.
n=0
while iptables -S INPUT 2>/dev/null | grep -q 'ha01-isolate' && [ "$n" -lt 10 ]; do
  iptables -S INPUT | grep 'ha01-isolate' | sed 's/^-A/-D/' | while read -r r; do iptables $r; done
  n=$((n+1))
done
exit 0
"""
UNISOLATE_PATH = "/usr/local/sbin/ha01-unisolate.sh"


def install_unisolate(host):
    rc, out = ssh(host, "sudo -n tee %s >/dev/null && sudo -n chmod 755 %s && echo ok" % (UNISOLATE_PATH, UNISOLATE_PATH), timeout=30, stdin=UNISOLATE_SH)
    return rc == 0 and out.endswith("ok")


# ----------------------------------------------------------------------------------------------- Bridge HELIOS (D7) : archivage de ses réactions
def bridge_journal(t0, t1):
    """Lignes du journal du Bridge HELIOS (.161) entre t0 et t1 (epoch) : ce que HELIOS a détecté/exécuté pendant le test."""
    a = datetime.fromtimestamp(t0, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    b = datetime.fromtimestamp(t1, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rc, out = ssh("161", "sudo -n journalctl -u helios-ssh-bridge --since '%s' --until '%s' --utc --no-pager -o short-iso 2>&1 | tail -n 400" % (a, b), timeout=60)
    return out


def bridge_seconds_to_next_cycle():
    """Secondes avant le prochain cycle de détection du Bridge (période 300 s, calé sur la dernière ligne EXEC), None si inconnu."""
    rc, out = ssh("161", "sudo -n journalctl -u helios-ssh-bridge -n 60 --utc --no-pager -o short-iso 2>&1 | grep ' EXEC monitoring' | tail -1 | cut -c1-25", timeout=40)
    try:
        last = datetime.strptime(out.strip()[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None
    return 300 - ((time.time() - last) % 300)


# ----------------------------------------------------------------------------------------------- sondes : fenêtres et analyses
def window_records_local(path, t0, t1):
    lo, hi = iso_ms(t0), iso_ms(t1)
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for l in fh:
            if '"ts": "' not in l:
                continue
            i = l.find('"ts": "') + 7
            ts = l[i:i + 24]
            if lo <= ts <= hi:
                try:
                    out.append(json.loads(l))
                except ValueError:
                    pass
    return out


def window_records_remote(host, path, t0, t1, sudo=False):
    """Extrait les lignes d'un JSONL distant dont le « ts » est dans [t0, t1] (comparaison lexicographique ISO)."""
    cmd = "%sawk -F'\"' -v a='%s' -v b='%s' '$4>=a && $4<=b' %s" % ("sudo -n " if sudo else "", iso_ms(t0), iso_ms(t1), path)
    rc, out = ssh(host, cmd, timeout=120)
    recs = []
    for l in out.splitlines():
        try:
            recs.append(json.loads(l))
        except ValueError:
            pass
    return recs


def split_hors_gel(recs, stalls):
    """(tous, hors gel d'E/S hôte) — E6."""
    return recs, [r for r in recs if not ha01_hoststall.inside(stalls, epoch_of(r["ts"]), 5, 10)]


def analyse_vantage(recs, stalls):
    raw, kept = split_hors_gel(recs, stalls)
    return {"raw": ha01_report.analyse(raw), "hors": ha01_report.analyse(kept), "n_exclus": len(raw) - len(kept),
            "echecs_exclus": sum(1 for r in raw if not r.get("ok") and ha01_hoststall.inside(stalls, epoch_of(r["ts"]), 5, 10))}


def fmt_probe(a, name):
    """« 99.9 % / 3.0 s / p95 212 ms » pour une sonde d'une analyse ha01_report (ou « — »)."""
    v = a.get(name)
    if not v:
        return "—"
    return "%s %% / %s s / p95 %s ms" % (v["availability_pct"], v["longest_failure_s"], v["lat_ms_p95"])


# ----------------------------------------------------------------------------------------------- contexte d'exécution et rapport
class Run:
    def __init__(self, name):
        self.stamp = now().strftime("%Y%m%dT%H%M%SZ")
        self.dir = os.path.join(RES, "%s-%s" % (name, self.stamp))
        os.makedirs(self.dir, exist_ok=True)
        self._log = open(os.path.join(self.dir, "run.log"), "a", encoding="utf-8")

    def say(self, msg):
        line = "%s %s" % (iso(), msg)
        print(line, flush=True)
        self._log.write(line + "\n")
        self._log.flush()

    def save(self, name, content):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
            fh.write(content if isinstance(content, str) else json.dumps(content, indent=1, ensure_ascii=False, default=str))

    def jsonl(self, name):
        return open(os.path.join(self.dir, name), "a", encoding="utf-8")


def write_report(run, title, rows, extra_md="", summary=None):
    """rows = [(critère, ok, mesure)] ; écrit summary.md + summary.json ; retourne True si tout est ✅."""
    md = ["# %s" % title, "", "| Critère | Résultat | Mesure |", "|---|---|---|"]
    md += ["| %s | %s | %s |" % (c, "✅" if ok else "❌", str(v).replace("|", "/")) for c, ok, v in rows]
    md += ["", extra_md]
    run.save("summary.md", "\n".join(md).rstrip("\n") + "\n")        # pas de ligne vide en fin de fichier (git diff --check)
    js = dict(summary or {})
    js["criteria"] = [{"c": c, "ok": ok, "v": v} for c, ok, v in rows]
    run.save("summary.json", js)
    return all(ok for _, ok, _ in rows)
