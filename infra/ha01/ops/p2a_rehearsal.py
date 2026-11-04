#!/usr/bin/env python3
"""
HA-01 Phase 2a — RÉPÉTITION DE REPRISE par taint NoExecute (graceful, réversible, SANS arrêt de VM).

  python p2a_rehearsal.py --node k8s-worker1 --label w1 [--duration 600] [--switchover] [--dry-run]

Principe : le nœud cible reste vivant mais tous ses pods non tolérants sont évincés (taint ha01-rehearsal=1:NoExecute).
Les survivants doivent absorber la charge : replanification, mémoire, bascule des Services, trafic 20 req/s.
PAS de cordon (spec.unschedulable) => HELIOS ne déclenche pas NODE_CORDONED -> UNCORDON_NODE.
Si le nœud cible héberge le primaire PostgreSQL, --switchover est OBLIGATOIRE (bascule contrôlée d'abord).

Abandon automatique (retrait du taint) si : MemAvailable du survivant < 250 Mo, de .153 < 250 Mo, quorum etcd dégradé,
écritures canari muettes > 240 s, sonde web en échec > 90 s, RAM hôte disponible < 600 Mo.
Critères de réussite : cf. infra/ha01/README-HA-01.md (§ Phase 2a) — évalués et écrits dans summary.md.
"""
import argparse
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
sys.path.insert(0, PROBES)
import ha01_report  # noqa: E402
import ha01_canary_report  # noqa: E402
import ha01_hoststall  # noqa: E402

# Sortie toujours en UTF-8 (défaut Windows = cp1252 : les symboles ✅/❌ font planter print(), même redirigé vers un fichier).
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

NODE_IP = {"k8s-master": "192.168.148.153", "k8s-worker1": "192.168.148.158", "k8s-worker2": "192.168.148.155"}
IPS = ["192.168.148.153", "192.168.148.155", "192.168.148.158"]
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR"]
TAINT = "ha01-rehearsal=1:NoExecute"
ETCD_EPS = ",".join("https://192.168.148.%s:2379" % s for s in ("153", "155", "158", "160", "162"))
ETCD = ("sudo -n env ETCDCTL_API=3 etcdctl --endpoints=%s --cacert=/etc/kubernetes/pki/etcd/ca.crt "
        "--cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key --command-timeout=6s" % ETCD_EPS)


def now():
    return datetime.now(timezone.utc)


def iso(t=None):
    return (t or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def host_avail_mb():
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                    ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return int(m.ullAvailPhys / 1048576)


def ssh(ip, cmd, timeout=45):
    try:
        r = subprocess.run(SSH + ["kube@" + ip, cmd], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except subprocess.TimeoutExpired:
        return 124, ""


class Cluster:
    def __init__(self, target):
        self.target = target
        self.survivors = [ip for n, ip in NODE_IP.items() if n != target and n != "k8s-master"] + ["192.168.148.153"]

    def via(self):
        for ip in self.survivors:
            rc, _ = ssh(ip, "kubectl get --raw=/readyz --request-timeout=6s", timeout=15)
            if rc == 0:
                return ip
        raise SystemExit("aucun apiserver survivant joignable")

    def k(self, args, timeout=50):
        rc, out = ssh(self.via(), "kubectl --request-timeout=30s " + args, timeout)
        return rc, out

    def mem(self, ip):
        rc, out = ssh(ip, "awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo", timeout=15)
        return int(out) if rc == 0 and out.isdigit() else None

    def etcd_ok(self):
        rc, out = ssh(self.via(), ETCD + " endpoint health -w json 2>/dev/null", timeout=25)
        try:
            return sum(1 for e in json.loads(out) if e.get("health"))
        except Exception:  # noqa: BLE001
            return 0

    def primary(self):
        rc, out = self.k("-n pfe get pods -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name} {.items[0].spec.nodeName}'")
        return out.replace("'", "").split() if rc == 0 else []

    def cnpg_state(self):
        rc, out = self.k("-n pfe get cluster.postgresql.cnpg.io postgres -o jsonpath='{.status.phase}|{.status.currentPrimary}|{.status.readyInstances}'")
        return out.replace("'", "")


def tail_probe_fail_run(probe_file, name="web", sec=120):
    """Durée (s) de l'échec ininterrompu EN COURS pour une sonde (0 si dernier échantillon OK)."""
    try:
        with open(probe_file, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 60000))
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


def deadman_arm(ip, node, label, seconds):
    """Filet de sécurité côté NŒUD, indépendant de ce script ET de la session : une minuterie systemd (root, sur le worker
    non drainé) retire le taint de répétition au bout de `seconds` s même si ce script est tué ou si la session s'arrête
    (répétition sans surveillance). Retourne True seulement si la minuterie est vérifiée « active »."""
    d = {"u": "ha01-untaint-%s" % label, "s": int(seconds), "n": node, "t": TAINT}
    cmd = ("sudo -n systemctl stop %(u)s.timer %(u)s.service 2>/dev/null; sudo -n systemctl reset-failed %(u)s.service 2>/dev/null; "
           "sudo -n systemd-run --quiet --unit=%(u)s --on-active=%(s)ds --property=Type=oneshot "
           "--description='HA-01 filet de securite : retire le taint de repetition' "
           "/usr/bin/env KUBECONFIG=/etc/kubernetes/admin.conf /usr/bin/kubectl --request-timeout=30s taint node %(n)s %(t)s- "
           ">/dev/null 2>&1; sudo -n systemctl is-active %(u)s.timer" % d)
    rc, out = ssh(ip, cmd, timeout=30)
    return bool(out) and out.strip().splitlines()[-1] == "active"


def deadman_disarm(ip, label):
    u = "ha01-untaint-%s" % label
    return ssh(ip, "sudo -n systemctl stop %s.timer %s.service 2>/dev/null; sudo -n systemctl reset-failed %s.service 2>/dev/null; echo ok" % (u, u, u), timeout=30)


def survivor_canary(node):
    """Canari qui FAIT FOI pendant la répétition : celui du worker NON drainé (celui du worker drainé est évincé : attendu)."""
    return "ha01-canary-w2" if node == "k8s-worker1" else "ha01-canary-w1"


def _ep(ts):
    from datetime import datetime as _dt
    return _dt.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z" if "." in ts else "%Y-%m-%dT%H:%M:%S%z").timestamp()


def last_ack_age(cl, dep="ha01-canary"):
    rc, out = cl.k("-n pfe logs deploy/%s --tail=3" % dep, timeout=40)
    last = None
    for l in out.splitlines():
        p = l.split(" ", 1)
        if len(p) == 2 and p[1].strip().isdigit():
            try:
                last = float(p[0])
            except ValueError:
                pass
    return None if last is None else time.time() - last


def preflight(cl, a, probe_file, samples_file):
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
    rc, out = cl.k("get nodes --no-headers")
    ready = [l.split()[0] for l in out.splitlines() if len(l.split()) > 1 and l.split()[1] == "Ready"]
    add("3 noeuds Ready", len(ready) == 3, ready)
    rc, out = cl.k("get nodes -o jsonpath='{range .items[*]}{.metadata.name}{\" \"}{.spec.taints[*].key}{\"\\n\"}{end}'")
    add("aucun taint de répétition résiduel", "ha01-rehearsal" not in out, out.replace("\n", " | "))
    st = cl.cnpg_state()
    add("CNPG sain 2/2", st.startswith("Cluster in healthy state") and st.endswith("|2"), st)
    rc, out = cl.k("get pods -A --no-headers")
    bad = [l for l in out.splitlines() if len(l.split()) > 3 and l.split()[3] not in ("Running", "Completed")]
    add("aucun pod non sain", not bad, bad[:5])
    rc, out = cl.k("-n pfe get pods -l ha01/role=canary --no-headers")
    add("canaris des workers (ha01-canary-w1 et -w2) Running",
        all(any(l.startswith(n) and " Running " in " " + l + " " for l in out.splitlines()) for n in ("ha01-canary-w1", "ha01-canary-w2")),
        out.replace("\n", " | ")[:200])
    age_c = last_ack_age(cl, survivor_canary(a.node))
    add("canari du worker NON drainé (%s) écrit depuis < 30 s" % survivor_canary(a.node), age_c is not None and age_c < 30, None if age_c is None else round(age_c, 1))
    age_p = time.time() - os.path.getmtime(probe_file) if os.path.exists(probe_file) else 9e9
    age_s = time.time() - os.path.getmtime(samples_file) if os.path.exists(samples_file) else 9e9
    add("harnais actif (sondes < 15 s, échantillonneur < 60 s)", age_p < 15 and age_s < 60, "probe %.0fs sampler %.0fs" % (age_p, age_s))
    h = host_avail_mb()
    add("RAM hôte disponible >= 2000 Mo", h >= 2000, "%d Mo" % h)
    m153 = cl.mem("192.168.148.153")
    other = [ip for n, ip in NODE_IP.items() if n not in (a.node, "k8s-master")][0]
    mo = cl.mem(other)
    add(".153 MemAvailable >= 500 Mo", (m153 or 0) >= 500, m153)
    add("survivant worker MemAvailable >= 600 Mo", (mo or 0) >= 600, mo)
    add("etcd 5/5 sain", cl.etcd_ok() == 5, cl.etcd_ok())
    pr = cl.primary()
    on_target = bool(pr) and pr[1] == a.node
    add("primaire PostgreSQL hors cible OU --switchover demandé", (not on_target) or a.switchover, pr)
    return checks, pr


def promote(cl, instance, out):
    """Switchover contrôlé vers `instance` via le plugin kubectl-cnpg (installé sur .155/.158 par la Phase 1)."""
    for ip in cl.survivors:
        rc, o = ssh(ip, "test -x /home/kube/bin/kubectl-cnpg && /home/kube/bin/kubectl-cnpg promote postgres %s -n pfe" % instance, timeout=60)
        if rc == 0:
            out.write("switchover vers %s demandé via .%s : %s\n" % (instance, ip.split('.')[-1], o))
            break
    else:
        raise SystemExit("plugin kubectl-cnpg indisponible sur les survivants (installer via Phase 1 / C7)")
    t0 = time.time()
    while time.time() - t0 < 240:
        time.sleep(4)
        st = cl.cnpg_state()
        if st.startswith("Cluster in healthy state") and ("|%s|2" % instance) in st:
            return instance, time.time() - t0
    raise SystemExit("switchover non abouti en 240 s: %s" % cl.cnpg_state())


def do_switchover(cl, pr, out):
    return promote(cl, "postgres-5" if pr[0] == "postgres-7" else "postgres-7", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True, choices=["k8s-worker1", "k8s-worker2"])
    ap.add_argument("--label", required=True)
    ap.add_argument("--duration", type=int, default=600)
    ap.add_argument("--rps", type=float, default=20)
    ap.add_argument("--switchover", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe-file", default=os.path.normpath(os.path.join(HERE, "..", "results", "2026-11-03", "probe-baseline-p0.jsonl")))
    ap.add_argument("--samples-file", default=os.path.normpath(os.path.join(HERE, "..", "results", "2026-11-03", "samples-baseline-p0.jsonl")))
    a = ap.parse_args()
    ts = now().strftime("%Y%m%dT%H%M%SZ")
    outdir = os.path.normpath(os.path.join(HERE, "..", "results", "2026-11-03", "p2a-%s-%s" % (a.label, ts)))
    os.makedirs(outdir, exist_ok=True)
    log = open(os.path.join(outdir, "run.log"), "a", encoding="utf-8")

    def say(msg):
        line = "%s %s" % (iso(), msg)
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    cl = Cluster(a.node)
    checks, pr = preflight(cl, a, a.probe_file, a.samples_file)
    json.dump(checks, open(os.path.join(outdir, "preflight.json"), "w"), indent=1, ensure_ascii=False)
    for c in checks:
        say("PREFLIGHT %-58s %s  %s" % (c["check"], "OK " if c["ok"] else "KO ", c["detail"]))
    if not all(c["ok"] for c in checks):
        say("STOP: préconditions non satisfaites — aucune action effectuée.")
        return 3
    if a.dry_run:
        say("dry-run: préconditions OK, aucune action.")
        return 0

    survivor = [ip for n, ip in NODE_IP.items() if n not in (a.node, "k8s-master")][0]
    auth_canary = survivor_canary(a.node)
    etcd_bad_n = 0
    t_start = now()
    # --- charge
    load_out = os.path.join(outdir, "load.jsonl")
    load_dur = a.duration + 900 + 120
    load = subprocess.Popen([sys.executable, os.path.join(PROBES, "ha01_load.py"), "--rps", str(a.rps), "--duration", str(load_dur), "--out", load_out],
                            stdout=open(os.path.join(outdir, "load-summary.json"), "w"), stderr=subprocess.STDOUT)
    say("charge %s req/s démarrée (pid %s) ; stabilisation 30 s" % (a.rps, load.pid))
    time.sleep(30)
    sw_info = None
    if pr and pr[1] == a.node:
        say("le primaire %s est sur %s : switchover contrôlé" % (pr[0], a.node))
        rep, dt = do_switchover(cl, pr, log)
        sw_info = {"new_primary": rep, "seconds_to_healthy": round(dt, 1)}
        say("switchover terminé: %s" % sw_info)
        time.sleep(30)
    aborted = None

    def untaint():
        cl.k("taint node %s ha01-rehearsal=1:NoExecute-" % a.node)

    # --- état avant
    rc, before = cl.k("get pods -A -o wide --no-headers")
    open(os.path.join(outdir, "pods-before.txt"), "w", encoding="utf-8").write(before)
    # --- filet de sécurité : sans lui, AUCUN taint n'est posé
    dm_seconds = a.duration + 300
    if not deadman_arm(survivor, a.node, a.label, dm_seconds):
        say("STOP: filet de sécurité (minuterie systemd de retrait du taint) NON armé sur %s — aucun taint posé." % survivor)
        load.terminate()
        return 4
    say("filet armé sur %s : le taint sera retiré automatiquement dans %d s si ce script disparaît" % (survivor, dm_seconds))
    # --- taint
    t_taint = now()
    rc, o = cl.k("taint node %s %s --overwrite" % (a.node, TAINT))
    say("TAINT posé sur %s : %s" % (a.node, o))
    timeline = open(os.path.join(outdir, "timeline.jsonl"), "w", encoding="utf-8")
    snap_done = False
    ack_bad_since = None
    t_end = time.time() + a.duration
    try:
        while time.time() < t_end:
            cyc = time.time()
            res = {}
            ths = []

            def m(ip):
                res[ip] = cl.mem(ip)
            for ip in (survivor, "192.168.148.153"):
                th = threading.Thread(target=m, args=(ip,))
                th.start()
                ths.append(th)
            for th in ths:
                th.join(30)
            h = host_avail_mb()
            etcd = cl.etcd_ok()
            age = last_ack_age(cl, auth_canary)
            web_run = tail_probe_fail_run(a.probe_file, "web")
            # anti-rebond : un gel d'E/S de l'hôte (~5 s, cf. ha01_hoststall.py) fait répondre « malade » à etcdctl pendant un
            # cycle ; une vraie dégradation d'etcd persiste -> abandon seulement après 2 cycles consécutifs.
            etcd_bad_n = etcd_bad_n + 1 if etcd < 4 else 0
            rec = {"ts": iso(), "t_plus_s": int(time.time() - t_taint.timestamp()), "mem_survivor": res.get(survivor), "mem_153": res.get("192.168.148.153"),
                   "host_mb": h, "etcd_ok": etcd, "canary_ack_age_s": None if age is None else round(age, 1), "web_fail_run_s": web_run}
            timeline.write(json.dumps(rec) + "\n")
            timeline.flush()
            if not snap_done and rec["t_plus_s"] >= 90:
                rc, snap = cl.k("get pods -A -o wide --no-headers")
                open(os.path.join(outdir, "pods-T+90s.txt"), "w", encoding="utf-8").write(snap)
                snap_done = True
            why = None
            if (rec["mem_survivor"] or 9999) < 250:
                why = "MemAvailable survivant %s Mo < 250" % rec["mem_survivor"]
            elif (rec["mem_153"] or 9999) < 250:
                why = "MemAvailable .153 %s Mo < 250" % rec["mem_153"]
            elif etcd_bad_n >= 2:
                why = "etcd dégradé (%s/5) sur 2 cycles consécutifs" % etcd
            elif h < 600:
                why = "RAM hôte %s Mo < 600" % h
            elif web_run > 90:
                why = "sonde web en échec depuis %s s" % web_run
            if age is not None and age > 240:
                ack_bad_since = ack_bad_since or time.time()
                why = why or "écritures canari muettes depuis %.0f s" % age
            if why:
                aborted = why
                say("ABANDON: %s" % why)
                break
            d = 5 - (time.time() - cyc)
            if d > 0:
                time.sleep(d)
    finally:
        t_untaint = now()
        untaint()
        say("TAINT retiré de %s%s" % (a.node, " (ABANDON)" if aborted else ""))
        deadman_disarm(survivor, a.label)
        timeline.close()
    # --- pods avant retrait / récupération
    rc, atend = cl.k("get pods -A -o wide --no-headers")
    open(os.path.join(outdir, "pods-after-untaint.txt"), "w", encoding="utf-8").write(atend)
    t_rec0 = time.time()
    recovered = None
    while time.time() - t_rec0 < 900:
        time.sleep(10)
        rc, o = cl.k("get pods -A --no-headers")
        bad = [l for l in o.splitlines() if len(l.split()) > 3 and l.split()[3] not in ("Running", "Completed")]
        st = cl.cnpg_state()
        if not bad and st.startswith("Cluster in healthy state") and st.endswith("|2"):
            recovered = time.time() - t_rec0
            break
    say("RÉCUPÉRATION complète en %s s" % ("%.0f" % recovered if recovered else "> 900 (NON)"))
    if sw_info and recovered:
        # état de production : le primaire revient là où il était (indispensable avant de répéter sur l'autre worker)
        cur = cl.primary()
        if cur and cur[0] != pr[0]:
            try:
                rep2, dt2 = promote(cl, pr[0], log)
                sw_info["retour_primaire"] = {"vers": rep2, "s": round(dt2, 1)}
                say("primaire rétabli sur %s en %.0f s" % (rep2, dt2))
            except SystemExit as e:
                say("ATTENTION: retour du primaire non abouti: %s" % e)
    time.sleep(45)
    t_stop = now()
    load.terminate()
    # --- analyses
    recs = []
    for pf in [a.probe_file]:
        with open(pf, encoding="utf-8") as fh:
            for l in fh:
                try:
                    r = json.loads(l)
                except ValueError:
                    continue
                if iso(t_taint)[:19] <= r["ts"][:19] <= iso(t_untaint)[:19]:
                    recs.append(r)
    # Gels d'E/S de l'hôte pendant la répétition (cause exogène, cf. ha01_hoststall.py / écart E6) : les mesures qui les
    # chevauchent sont classées « hors gel » ; les chiffres BRUTS restent publiés à côté.
    stalls = ha01_hoststall.windows(t_start.timestamp(), t_stop.timestamp())
    pa_raw = ha01_report.analyse(recs)
    pa = ha01_report.analyse([r for r in recs if not ha01_hoststall.inside(stalls, _ep(r["ts"]), 5, 10)])
    # canari du worker non drainé (celui du worker drainé est évincé : attendu). Journal d'abord, liste des id ENSUITE.
    log_txt = ha01_canary_report.fetch_log(auth_canary)
    ids_txt = ha01_canary_report.fetch_ids()
    open(os.path.join(outdir, "canary-%s.log" % auth_canary), "w", encoding="utf-8").write(log_txt)
    open(os.path.join(outdir, "ids.txt"), "w", encoding="utf-8").write(ids_txt)
    ca = ha01_canary_report.analyse(log_txt, ids_txt, t_start.timestamp(), t_stop.timestamp(), exclude=stalls or [], exclude_after_s=60)
    tl = [json.loads(l) for l in open(os.path.join(outdir, "timeline.jsonl"), encoding="utf-8")]
    mem_s = min([r["mem_survivor"] for r in tl if r["mem_survivor"] is not None] or [0])
    mem_153 = min([r["mem_153"] for r in tl if r["mem_153"] is not None] or [0])
    host_min = min([r["host_mb"] for r in tl] or [0])
    # OOM / évictions mémoire
    rc, oom = cl.k("get pods -A -o json")
    n_oom = 0
    try:
        for p in json.loads(oom)["items"]:
            for c in p["status"].get("containerStatuses", []):
                lt = c.get("lastState", {}).get("terminated", {})
                if lt.get("reason") == "OOMKilled" and lt.get("finishedAt", "") >= iso(t_taint):
                    n_oom += 1
    except Exception:  # noqa: BLE001
        n_oom = -1
    rc, ev = cl.k("get events -A --field-selector reason=Evicted --no-headers")
    mem_ev = [l for l in ev.splitlines() if "memory" in l.lower()]
    rc, trans = cl.k("get nodes --no-headers")
    # critères
    def pct(n):
        return pa.get(n, {}).get("availability_pct")

    def longest(n):
        return pa.get(n, {}).get("longest_failure_s")

    def raw(n):
        return "brut %s %% / %s s" % (pa_raw.get(n, {}).get("availability_pct"), pa_raw.get(n, {}).get("longest_failure_s"))
    crit = [
        ("gels d'E/S de l'hôte pendant la répétition (informatif ; mesures concernées classées hors gel)", True, ha01_hoststall.describe(stalls)),
        ("web >= 98 % et série d'échecs <= 10 s (hors gels)", (pct("web") or 0) >= 98 and (longest("web") or 0) <= 10, "%s %% / %s s ; %s" % (pct("web"), longest("web"), raw("web"))),
        ("api_app >= 98 % et série <= 10 s (hors gels)", (pct("api_app") or 0) >= 98 and (longest("api_app") or 0) <= 10, "%s %% / %s s ; %s" % (pct("api_app"), longest("api_app"), raw("api_app"))),
        ("kube_api >= 99 % et trou <= 15 s (hors gels)", (pct("kube_api") or 0) >= 99 and (longest("kube_api") or 0) <= 15, "%s %% / %s s ; %s" % (pct("kube_api"), longest("kube_api"), raw("kube_api"))),
        ("canari %s : trou d'écriture <= 120 s (hors gels)" % auth_canary, ca["max_gap_hors_gel_s"] <= 120, "%s s (brut %s s)" % (ca["max_gap_hors_gel_s"], ca["max_gap_s"])),
        ("canari %s : 0 écriture acquittée perdue (jamais exemptée)" % auth_canary, ca.get("acked_lost", 1) == 0, "%s" % ca.get("acked_lost")),
        ("MemAvailable survivant >= 300 Mo", mem_s >= 300, "%s Mo" % mem_s),
        ("MemAvailable .153 >= 500 Mo", mem_153 >= 500, "%s Mo" % mem_153),
        ("RAM hôte >= 1500 Mo", host_min >= 1500, "%s Mo" % host_min),
        ("0 OOMKilled", n_oom == 0, n_oom),
        ("0 éviction pour mémoire", not mem_ev, len(mem_ev)),
        ("pas d'abandon", aborted is None, aborted),
        ("récupération complète <= 15 min", recovered is not None, "%s s" % (round(recovered) if recovered else None)),
    ]
    summary = {"label": a.label, "node": a.node, "t_taint": iso(t_taint), "t_untaint": iso(t_untaint), "switchover": sw_info,
               "probes": pa, "probes_raw": pa_raw, "host_stalls": stalls, "canary_deploy": auth_canary, "canary": ca, "mem_survivor_min": mem_s, "mem_153_min": mem_153, "host_min_mb": host_min,
               "oom": n_oom, "recovered_s": recovered, "criteria": [{"c": c, "ok": ok, "v": v} for c, ok, v in crit]}
    json.dump(summary, open(os.path.join(outdir, "summary.json"), "w"), indent=1, ensure_ascii=False, default=str)
    md = ["# Répétition %s — %s" % (a.label, a.node), "", "Fenêtre taint : %s → %s" % (iso(t_taint), iso(t_untaint)), ""]
    md += ["| Critère | Résultat | Mesure |", "|---|---|---|"] + ["| %s | %s | %s |" % (c, "✅" if ok else "❌", v) for c, ok, v in crit]
    open(os.path.join(outdir, "summary.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    say("RÉSULTAT: %d/%d critères satisfaits → %s" % (sum(1 for _, ok, _ in crit if ok), len(crit), outdir))
    for c, ok, v in crit:
        say("  %s %-52s %s" % ("OK" if ok else "KO", c, v))
    return 0 if all(ok for _, ok, _ in crit) else 1


if __name__ == "__main__":
    sys.exit(main())
