#!/usr/bin/env python3
"""
HA-01 Phase 4 — ARRÊT RÉEL d'UNE VM (panne brutale, `vmrun stop hard`) et mesure de la haute disponibilité.

  python p4_vmstop.py --vm 160|155|158 --label L --confirmed [--observe 600] [--stabilize 1800] [--dry-run]
  Ordre du plan : .160, puis .155, puis .158 — une VM à la fois, 30 min de stabilisation entre deux.

Scénarios : .160 = k8s-monitoring (HAProxy principal + DNS n°1 + Prometheus + membre etcd) · .155 = k8s-worker2 (leader etcd,
réplique PostgreSQL) · .158 = k8s-worker1 (PRIMAIRE PostgreSQL). .153 (control-plane) et .161 (IA/Camunda) ne sont JAMAIS éteintes.

GARDE-FOUS (aucun n'est contournable) :
  1. refuse de s'exécuter (code 3, rien fait) sans --confirmed ET sans results/2026-11-03/CONFIRMATION-<vm>.txt de moins de 6 h, écrit
     par l'assistant APRÈS la confirmation explicite de l'utilisateur qui nomme la VM et ses conséquences. Jamais depuis un cron.
  2. préflight complet (cluster sain, harnais, RAM hôte >= 2000 Mo sur 10 min, aucun gel d'E/S récent, canaris vivants, vmx vérifié par
     SSH, agent hôte :9999 suspendu — D7) ; sinon rien n'est éteint.
  3. filet de survie INDÉPENDANT de la session : processus Windows détaché (vm_revive.ps1) qui rallume la VM après observe+900 s si
     personne ne l'a désarmé. Sans filet armé, aucun arrêt.
  4. abandon automatique avec relance immédiate : quorum etcd menacé, .153 NotReady > 2 min, .153 MemAvailable < 250 Mo, RAM hôte <
     600 Mo, sonde par nom en échec > 60 s, canari survivant muet > 180 s.
  5. `finally` : la VM est rallumée si elle est éteinte, l'agent hôte est rétabli, le filet est désarmé.

Mesure (tableau §F du plan) : 3 points de vue (Windows ; .161 sans cache DNS ; .161 avec cache) en BRUT et HORS gel d'E/S hôte (E6,
détecteur en direct car Prometheus est sur .160), canaris des workers (journaux captés EN DIRECT : celui du worker éteint disparaît
avec son pod), nœuds, etcd (délai de retour à 4/4, élections), mémoire, OOM/évictions, placement (pods Pending documentés), bascule
DNS, HELIOS (journal du Bridge archivé), puis relance et mesures de retour, puis stabilisation.
Code retour : 0 = tout ✅ · 1 = au moins un ❌ · 2 = abandon · 3 = préflight KO (rien éteint) · 4 = filet non armé (rien éteint).
"""
import argparse
import json
import os
import subprocess
import sys
import time

import ha01_ops as O
import p2b_inject as P2B

D = O.ha01_hoststall
VMRUN = r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
VMX_DIR = r"C:\Users\houss\Documents\Virtual Machines"
REVIVE = os.path.join(O.HERE, "vm_revive.ps1")
PAUSE = os.path.join(O.HERE, "helios-pause.ps1")
VANTAGES = ("Windows", ".161 sans cache", ".161 avec cache")
VMS = {
    "160": {"folder": "k8s-monitoring", "node": None, "kind": "lb", "web_min": 99.0, "survivor_lb": "162",
            "alive_canaries": ["ha01-canary-w1", "ha01-canary-w2"], "dead_canaries": []},
    # 4e scénario (perte de .162 = LB ACTIF du DNS). `dnswatch` active : sondes de bascule DNS (Windows + dnsmasq direct + nœud), abandons spécifiques (LB survivant .160,
    # sondes « hosts » web/api_app), mémoire de .160 surveillée, préflight spécifique p5_162_preflight.py exigé. Les autres scénarios n'utilisent RIEN de cela.
    "162": {"folder": "k8s-lb2", "node": None, "kind": "lb", "web_min": 99.0, "survivor_lb": "160", "dnswatch": True,
            "alive_canaries": ["ha01-canary-w1", "ha01-canary-w2"], "dead_canaries": []},
    "155": {"folder": "k8s-worker2", "node": "k8s-worker2", "kind": "worker", "web_min": 98.0,
            "alive_canaries": ["ha01-canary-w1"], "dead_canaries": ["ha01-canary-w2"]},
    "158": {"folder": "k8s-worker1", "node": "k8s-worker1", "kind": "primary", "web_min": 98.0,
            "alive_canaries": ["ha01-canary-w2"], "dead_canaries": ["ha01-canary-w1"]},
}


def vmx_of(vm):
    return os.path.join(VMX_DIR, VMS[vm]["folder"], VMS[vm]["folder"] + ".vmx")


def vmrun(*args, timeout=150):
    r = subprocess.run([VMRUN, "-T", "ws"] + list(args), capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


def vm_running(vm):
    rc, out = vmrun("list", timeout=40)
    return vmx_of(vm).lower() in out.lower()


def helios(action):
    r = subprocess.run(["powershell", "-NoProfile", "-File", PAUSE, "-Action", action], capture_output=True, text=True, timeout=120,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


def confirmation(vm, max_age_h=6):
    p = os.path.join(O.RES, "CONFIRMATION-%s.txt" % vm)
    if not os.path.exists(p):
        return False, "fichier absent: %s" % p
    age_h = (time.time() - os.path.getmtime(p)) / 3600
    txt = open(p, encoding="utf-8", errors="replace").read()
    ok = age_h <= max_age_h and ("VM: %s" % vm) in txt and len(txt.strip()) > 40
    return ok, "%s (%.1f h, %d octets)" % (os.path.basename(p), age_h, len(txt))


def ram_history():
    """(minimum des 3 points de contrôle, n) : RAM hôte disponible vue par l'échantillonneur à ~10 min, ~5 min et ~0 min avant l'appel
    (le plan exige « 3 mesures sur 10 min >= 2 Go »). n = nombre de points de contrôle trouvés (3 attendus)."""
    now = time.time()
    pts = []
    try:
        with open(O.SAMPLES, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 500000))
            for l in fh.read().decode("utf-8", "replace").splitlines():
                if '"host_avail_mb"' in l:
                    try:
                        r = json.loads(l)
                    except ValueError:
                        continue
                    pts.append((now - O.epoch_of(r["ts"]), r["host_avail_mb"]))
    except OSError:
        pass
    vals = []
    for target in (600, 300, 5):
        near = [(abs(age - target), v) for age, v in pts if age >= 0 and abs(age - target) <= 20]
        if near:
            vals.append(min(near)[1])
    return (min(vals) if vals else 0), len(vals)


def spawn_revive(vm, seconds, stopfile, logfile):
    inner = '-NoProfile -WindowStyle Hidden -File "%s" -Vmx "%s" -Seconds %d -StopFile "%s" -LogFile "%s"' % (REVIVE, vmx_of(vm), seconds, stopfile, logfile)
    ps = "$p = Start-Process -FilePath powershell -ArgumentList '%s' -WindowStyle Hidden -PassThru; $p.Id" % inner
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    pid = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    return int(pid) if pid.isdigit() else None


def pid_alive(pid):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", "[bool](Get-Process -Id %d -ErrorAction SilentlyContinue)" % pid],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip().lower() == "true"


class LogStream:
    """`kubectl logs -f` d'un canari lu depuis .153 (jamais éteinte) : capte les écritures acquittées jusqu'à l'instant de la panne."""

    def __init__(self, dep, path):
        self.dep, self.path = dep, path
        self.fh = open(path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(O.SSH + ["kube@192.168.148.153", "kubectl -n pfe logs -f deploy/%s --since=2m --request-timeout=0" % dep],
                                     stdout=self.fh, stderr=subprocess.DEVNULL)

    def stop(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        self.fh.close()

    def text(self):
        return open(self.path, encoding="utf-8", errors="replace").read()


def recent_p5_preflight(vm, max_age_min=30):
    """(ok, détail) : le dernier results/.../p5-<vm>-preflight-*.json existe, date de moins de `max_age_min` min et ne contient AUCUN FAIL."""
    import glob
    files = sorted(glob.glob(os.path.join(O.RES, "p5-%s-preflight-*.json" % vm)))
    if not files:
        return False, "aucun p5-%s-preflight-*.json : lancer ops/p5_%s_preflight.py" % (vm, vm)
    try:
        j = json.load(open(files[-1], encoding="utf-8"))
    except (OSError, ValueError):
        return False, "fichier illisible: %s" % files[-1]
    age = (time.time() - j.get("ts", 0)) / 60
    return (age <= max_age_min and bool(j.get("all_ok"))), "%s (%.0f min, %d PASS / %d WARN / %d FAIL)" % (os.path.basename(files[-1]), age, j.get("pass", 0), j.get("warn", 0), j.get("fail", 0))


def start_dnswatch(run, a, say):
    """4e scénario (perte de .162) : lance les sondes de BASCULE DNS pour `observe + 960` s (observation, relance, retour du DNS nominal).
    - Windows : résolveur système (cache DNS Windows + NRPT) puis connexion TCP vers la 1re adresse, 2 Hz ;
    - dnsmasq .160 et .162 en direct, 5 Hz, un fil par serveur ;
    - nœud .158 : glibc (/etc/resolv.conf : .160 puis .162, sans cache système), unité systemd transitoire bornée (RuntimeMaxSec).
    Ne modifie rien (requêtes DNS + connexions TCP sans données)."""
    dur = a.observe + 960
    script = os.path.join(O.PROBES, "ha01_dnswatch.py")
    st = {"files": {}, "procs": [], "node": None}
    cmds = {"windows": [sys.executable, script, "--mode", "os", "--name", "api.helios.orange.tn", "--port", "6443", "--period", "0.5", "--duration", str(dur)],
            "direct": [sys.executable, script, "--mode", "direct", "--servers", "160,162", "--period", "0.2", "--duration", str(dur)]}
    for k, cmd in cmds.items():
        f = os.path.join(run.dir, "dnswatch-%s.jsonl" % k)
        st["files"][k] = f
        try:
            st["procs"].append(subprocess.Popen(cmd + ["--out", f], creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))   # CREATE_NO_WINDOW
        except OSError as e:
            say("ATTENTION: sonde DNS %s non démarrée (%s)" % (k, e))
    src = open(script, encoding="utf-8").read()
    rc, out = O.ssh("158", "cat > /tmp/ha01_dnswatch.py && echo ok", timeout=40, stdin=src)
    unit, nfile = "ha01-dnswatch-node-%s" % a.label, "/tmp/ha01-dnswatch-node-%s.jsonl" % a.label
    if out.endswith("ok"):
        rc, out = O.ssh("158", "sudo -n systemd-run --quiet --unit=%s --property=RuntimeMaxSec=%d /usr/bin/python3 /tmp/ha01_dnswatch.py --mode os --name api.helios.orange.tn "
                               "--port 6443 --period 0.5 --duration %d --out %s; sleep 2; systemctl is-active %s.service" % (unit, dur + 120, dur, nfile, unit), timeout=40)
        if out.strip().endswith("active"):
            st["node"] = nfile
    say("sondes de bascule DNS : Windows %s, dnsmasq direct %s, nœud .158 %s" % (
        "OK" if len(st["procs"]) >= 1 else "NON", "OK" if len(st["procs"]) >= 2 else "NON", "OK" if st["node"] else "NON"))
    return st


def collect_dnswatch(run, st):
    """Rapatrie le fichier du nœud .158 dans le dossier d'essai (les fichiers Windows y sont déjà)."""
    if st and st.get("node"):
        rc, out = O.ssh("158", "cat %s" % st["node"], timeout=120)
        run.save("dnswatch-node.jsonl", out + "\n")


def _jl(path):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    for r in out:
        r["t"] = O.epoch_of(r["ts"])
    return out


def analyse_dnswatch(run_dir, t_stop, t_vm_start, dead, surv):
    """Mesures BRUTES de la bascule DNS (secondes relatives à l'arrêt `t_stop`, ou à la relance `t_vm_start` pour le fail-back). Pur : testable avec des fichiers fabriqués."""
    res = {}
    ip_dead, ip_surv = O.NET + dead, O.NET + surv
    d = _jl(os.path.join(run_dir, "dnswatch-direct.jsonl"))
    api = "api.helios.orange.tn"
    flips = [r["t"] for r in d if r.get("server") == surv and r.get("name") == api and r["t"] >= t_stop and r.get("ips") is not None and dead not in r["ips"]]
    res["flip_server_s"] = round(flips[0] - t_stop, 1) if flips else None
    dead_ans = [r["t"] for r in d if r.get("server") == dead and r["t"] >= t_stop and r.get("ips") is not None]
    res["dead_last_answer_s"] = round(dead_ans[-1] - t_stop, 1) if dead_ans else None
    fb = [r["t"] for r in d if t_vm_start and r.get("server") == surv and r.get("name") == api and r["t"] >= t_vm_start and r.get("ips") and dead in r["ips"]]
    res["failback_server_s"] = round(fb[0] - t_vm_start, 1) if fb else None
    for key, fname in (("windows", "dnswatch-windows.jsonl"), ("node", "dnswatch-node.jsonl")):
        rs = _jl(os.path.join(run_dir, fname))
        after = [r for r in rs if r["t"] >= t_stop]
        if not after:
            res[key] = {}
            continue
        fails = [r for r in after if not r.get("tcp_ok")]
        first_fail = fails[0]["t"] if fails else None
        recover = None
        if first_fail is not None:
            seq = [r for r in after if r["t"] >= first_fail]
            for i, r in enumerate(seq):
                if r.get("tcp_ok") and r.get("target") == ip_surv and all(x.get("tcp_ok") for x in seq[i:i + 3]):
                    recover = r["t"]
                    break
        stale = sum(1 for r in after if r.get("target") == ip_dead and (recover is None or r["t"] < recover))
        back = [r["t"] for r in rs if t_vm_start and r["t"] >= t_vm_start and r.get("target") == ip_dead and r.get("tcp_ok")]
        res[key] = {"n_echantillons": len(rs), "first_fail_s": None if first_fail is None else round(first_fail - t_stop, 1),
                    "recover_s": None if recover is None else round(recover - t_stop, 1),
                    "outage_s": None if (recover is None or first_fail is None) else round(recover - first_fail, 1),
                    "stale_answers": stale, "failback_s": round(back[0] - t_vm_start, 1) if back else None,
                    "max_resolve_ms": max((r.get("resolve_ms") or 0) for r in rs)}
    return res


def preflight(a, vm, say, dry):
    cfg = VMS[vm]
    chk = []

    def add(name, ok, detail):
        chk.append({"check": name, "ok": bool(ok), "detail": detail})
        say("PREFLIGHT %-72s %s  %s" % (name[:72], "OK " if ok else "KO ", str(detail)[:105]))
    ok, d = confirmation(vm)
    add("confirmation explicite de l'utilisateur pour la VM .%s (< 6 h)" % vm, ok or dry, d if not dry else "(dry-run) " + d)
    add("drapeau --confirmed", a.confirmed or dry, a.confirmed)
    if cfg.get("dnswatch"):
        # 4e scénario : le préflight SPÉCIFIQUE (ops/p5_162_preflight.py) doit avoir été exécuté TOUT VERT il y a moins de 30 min (même en lecture seule, il prouve la préparation)
        ok_p5, d_p5 = recent_p5_preflight(vm)
        add("préflight spécifique .%s (p5_162_preflight.py) tout PASS et de moins de 30 min" % vm, ok_p5 or dry, ("(dry-run) " if dry and not ok_p5 else "") + d_p5)
    ns = O.nodes_state()
    add("3 nœuds Ready", len(ns) == 3 and all(v == "Ready" for v in ns.values()), ns)
    add("etcd 5/5 sain", O.etcd_healthy() == 5, O.etcd_healthy())
    st = O.cnpg_state()
    add("CNPG sain 2/2", st.startswith("Cluster in healthy state") and st.endswith("|2"), st)
    bad = O.unhealthy_pods()
    add("aucun pod non sain", not bad, bad[:5])
    rc, out = O.kubectl("get nodes -o jsonpath='{range .items[*]}{.metadata.name}{\" \"}{.spec.taints[*].key}{\"\\n\"}{end}'")
    add("aucun taint de répétition résiduel", "ha01-rehearsal" not in out, out.replace("\n", " | "))
    for dep in ("ha01-canary-w1", "ha01-canary-w2"):
        age = O.canary_ack_age(dep)
        add("canari %s écrit depuis < 30 s" % dep, age is not None and age < 30, None if age is None else round(age, 1))
    add("harnais actif (sondes Windows < 15 s, échantillonneur < 60 s)", O.file_age(O.PROBE_WIN) < 15 and O.file_age(O.SAMPLES) < 60,
        "%.0fs/%.0fs" % (O.file_age(O.PROBE_WIN), O.file_age(O.SAMPLES)))
    rc, out = O.ssh("161", "systemctl is-active ha01-probe-161", timeout=20)
    add("sonde .161 (ha01-probe-161) active", out.strip() == "active", out)
    h = O.host_avail_mb()
    mn, n = ram_history()
    add("RAM hôte >= 2000 Mo maintenant ET à ~5 min et ~10 min (3 mesures, comme le plan)", h >= 2000 and n >= 3 and mn >= 2000, "maintenant %d Mo, min des 3 points %s Mo (n=%d)" % (h, mn, n))
    for s in ("153", "155", "158"):
        if s == vm:
            continue
        m = O.mem_avail(s)
        add(".%s MemAvailable >= %d Mo" % (s, 500 if s == "153" else 600), (m or 0) >= (500 if s == "153" else 600), m)
    ws = D.windows(time.time() - 600, time.time())
    add("aucun gel d'E/S de l'hôte dans les 10 dernières minutes", ws == [], D.describe(ws))
    for x in ("160", "162"):
        ok, dd = O.lb_direct_ok(x)
        up, bad2 = O.haproxy_all_up(x)
        add("LB .%s sert en direct et tous ses serveurs sont UP" % x, ok and up, "%s %s" % (dd, bad2))
    modes = {x: O.dns_state(x).get("mode") for x in ("160", "162")}
    add("mode DNS des 2 LB consigné (identique)", len(set(modes.values())) == 1 and None not in modes.values(), modes)
    add("vmrun présent", os.path.exists(VMRUN), VMRUN)
    add("fichier .vmx de la VM présent et VM en marche", os.path.exists(vmx_of(vm)) and vm_running(vm), vmx_of(vm))
    rc, out = O.ssh(vm, "hostname -I", timeout=20)
    add("VM .%s identifiée par SSH (hostname -I contient 192.168.148.%s)" % (vm, vm), ("192.168.148.%s" % vm) in out, out)
    rc, out = O.ssh("153", "kubectl --request-timeout=20s get --raw=/readyz", timeout=40)
    add("kubectl utilisable depuis .153 (flux des journaux de canaris)", out.strip() == "ok", out[:60])
    rc, out = helios("status")
    add("agent hôte HELIOS :9999 (état avant pause consigné)", rc == 0, out.replace("\n", " | ")[:150])
    return chk


def explain_etcd_dip(t_ev, ko):
    """Écart E17 : un contrôle de santé etcd isolé en échec est « expliqué » SEULEMENT si le seul membre fautif est .153 ET si l'échantillonneur montre,
    à ±45 s, une rafale de .153 (SSH expiré, load1 >= 10 ou PSI CPU >= 50 %). Retourne le texte d'explication, ou None (anomalie à conserver).
    Rien n'est effacé : l'anomalie brute est publiée à côté de l'explication."""
    if not ko or any(k != "153" for k in ko):
        return None
    best = None
    for r in O.window_records_local(O.SAMPLES, t_ev - 45, t_ev + 45):
        v = (r.get("vms") or {}).get("153") or {}
        if v.get("up") is False:
            return "SSH de .153 expiré à %s (rafale CPU)" % r["ts"][11:19]
        l1, psi = v.get("load1") or 0, v.get("psi_cpu_some10") or 0
        if l1 >= 10 or psi >= 50:
            if best is None or l1 + psi > best[0]:
                best = (l1 + psi, "rafale CPU de .153 à %s (load1=%.1f, PSI CPU=%.0f %%)" % (r["ts"][11:19], l1, psi))
    return best[1] if best else None


def evaluate(c):
    """Pur : critères du tableau §F. c = dict des mesures."""
    vm, cfg, stalls = c["vm"], VMS[c["vm"]], c["stalls"]
    rows, md = [], []
    rows.append(("gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6)", True, D.describe(stalls)))
    rows.append(("la VM .%s est bien arrêtée pendant la mesure (contrôle de l'injection)" % vm, c["stopped_ok"], c["stop_detail"]))
    per = {n: O.analyse_vantage(r, stalls) for n, r in c["vantages"].items()}
    for vname in VANTAGES:
        if vname not in per or not per[vname]["raw"]:
            rows.append(("[%s] données de sonde présentes" % vname, False, "aucune donnée"))
            continue
        h, r = per[vname]["hors"], per[vname]["raw"]
        for probe, minp, maxl in (("web", cfg["web_min"], 10), ("api_app", cfg["web_min"], 10), ("kube_api", 99.0, 15)):
            v = h.get(probe)
            rows.append(("[%s] %s >= %s %% et série d'échecs <= %s s (hors gels)" % (vname, probe, minp, maxl),
                         bool(v) and v["availability_pct"] >= minp and v["longest_failure_s"] <= maxl,
                         "hors gel: %s | BRUT: %s" % (O.fmt_probe(h, probe), O.fmt_probe(r, probe))))
        if vname == "Windows" and cfg["kind"] == "lb":
            slb = cfg.get("survivor_lb", "162")
            for p in ("lb%s_web" % slb, "lb%s_api" % slb):
                v = h.get(p)
                rows.append(("[Windows] LB survivant .%s en direct (%s) >= 99,5 %% (hors gels)" % (slb, p), bool(v) and v["availability_pct"] >= 99.5,
                             "hors gel: %s | BRUT: %s" % (O.fmt_probe(h, p), O.fmt_probe(r, p))))
    for dep, ca in c["canaries"].items():
        alive = dep in cfg["alive_canaries"]
        if cfg["kind"] == "primary":
            if alive:
                rows.append(("canari %s (survivant) : 1re écriture réussie après l'arrêt <= 120 s (plus long trou hors gel)" % dep, ca["max_gap_hors_gel_s"] <= 120,
                             "trou max hors gel=%.1fs (brut %.1fs)" % (ca["max_gap_hors_gel_s"], ca["max_gap_s"])))
            rows.append(("canari %s : écritures acquittées perdues <= 5 (1 s d'écriture à 5/s ; RPO de la réplication asynchrone)" % dep, ca["acked_lost"] <= 5,
                         "perdues=%d acquittées=%d" % (ca["acked_lost"], ca["acked"])))
        else:
            rows.append(("canari %s : 0 écriture acquittée perdue%s" % (dep, "" if alive else " (canari du worker éteint)"), ca["acked_lost"] == 0,
                         "perdues=%d acquittées=%d" % (ca["acked_lost"], ca["acked"])))
            if alive:
                rows.append(("canari %s (survivant) : 0 échec hors gel" % dep, ca["failures"] == 0,
                             "échecs hors gel=%d (bruts %d) trou max hors gel=%.1fs" % (ca["failures"], ca["failures_total"], ca["max_gap_hors_gel_s"])))
    unexpected = {n: v for n, v in c["notready_unexpected"].items()}
    rows.append(("nœuds : .153 et le survivant sans aucune transition NotReady", not unexpected, unexpected or "aucune"))
    if cfg["node"]:
        rows.append(("le nœud %s passe NotReady (comportement attendu)" % cfg["node"], c["dead_notready_after_s"] is not None, "après %s s" % c["dead_notready_after_s"]))
    rows.append(("etcd : 4/4 membres sains en <= 10 s après l'arrêt", c["etcd_ok_after_s"] is not None and c["etcd_ok_after_s"] <= 10, "%s s" % c["etcd_ok_after_s"]))
    rows.append(("etcd : <= 2 élections pendant le test (terme Raft)", c["term_after"] is not None and c["term_before"] is not None and c["term_after"] - c["term_before"] <= 2,
                 "terme %s -> %s, leader %s -> %s" % (c["term_before"], c["term_after"], c["leader_before"], c["leader_after"])))
    surv = [s for s in ("155", "158") if s != vm] + (["160"] if cfg.get("dnswatch") else [])      # .162 : le LB survivant .160 est aussi surveillé
    rows.append(("mémoire : survivants >= 300 Mo et .153 >= 500 Mo (minimum mesuré)", all(c["mem_min"].get(s, 9999) >= 300 for s in surv) and c["mem_min"].get("153", 9999) >= 500, c["mem_min"]))
    rows.append(("0 OOMKilled et 0 éviction pour mémoire pendant le test", c["oom"] == 0 and not c["mem_evictions"], "OOM=%s évictions=%s" % (c["oom"], len(c["mem_evictions"]))))
    rows.append(("RAM hôte >= 1500 Mo (minimum mesuré)", c["host_min"] >= 1500, "%s Mo" % c["host_min"]))
    rows.append(("placement : pods Pending documentés (anti-affinité = conséquence attendue, pas une panne)", True, c["pending_note"]))
    t = c["return"]
    if cfg["kind"] == "lb":
        rows.append(("retour : SSH, etcd 5/5 et HAProxy UP en <= 300 s après la relance", t.get("etcd5_s") is not None and t["etcd5_s"] <= 300 and t.get("lb_s") is not None and t["lb_s"] <= 300,
                     "ssh=%s etcd5=%s lb=%s" % (t.get("ssh_s"), t.get("etcd5_s"), t.get("lb_s"))))
        rows.append(("retour : DNS nominal (les 2 LB UP, publication nominale) <= 180 s après la relance", t.get("dns_s") is not None and t["dns_s"] <= 180, "%s s" % t.get("dns_s")))
    else:
        rows.append(("retour : nœud Ready <= 300 s et etcd 5/5 <= 300 s après la relance", t.get("ready_s") is not None and t["ready_s"] <= 300 and t.get("etcd5_s") is not None and t["etcd5_s"] <= 300,
                     "ssh=%s ready=%s etcd5=%s" % (t.get("ssh_s"), t.get("ready_s"), t.get("etcd5_s"))))
        rows.append(("retour : CNPG 2/2 sain <= 15 min après la relance", t.get("cnpg_s") is not None and t["cnpg_s"] <= 900, "%s s" % t.get("cnpg_s")))
    rows.append(("retour : aucun pod non sain (Pending résorbés)", t.get("pods_s") is not None, "%s s" % t.get("pods_s")))
    expl = c.get("stab_explained") or []
    rows.append(("stabilisation %d s : nœuds Ready, etcd 5/5, aucun pod non sain, sondes par nom OK (creux etcd isolés expliqués par une rafale CPU de .153 listés à part, E17)" % c["stabilize_s"],
                 not c["stab_anomalies"],
                 str(c["stab_anomalies"][:6] or "aucune anomalie inexpliquée") + ((" | expliquées (E17): %s" % expl[:6]) if expl else "")))
    rows.append(("pas d'abandon", c["abort"] is None, c["abort"]))
    md.append("## Bascule DNS (interrogation directe de dnsmasq toutes les secondes)")
    md.append("- mode du contrôleur : **%s** ; sortie du LB .160 des réponses de dnsmasq .162 : **%s**" % (c["dns_mode"], "—" if c["dns_switch_s"] is None else "%.0f s" % c["dns_switch_s"]))
    md.append("")
    if cfg.get("dnswatch"):
        # 4e scénario : critère de bascule du contrôleur + mesures BRUTES de la bascule vue par chaque type de client (hors critères du plan, publiées telles quelles)
        w = c.get("dnsw") or {}
        fl = w.get("flip_server_s")
        rows.append(("bascule DNS : le contrôleur survivant (.%s) cesse de publier .%s en <= 15 s après l'arrêt" % (cfg["survivor_lb"], vm), fl is not None and fl <= 15,
                     "bascule à %s s (modèle hors ligne : 8,1 à 11,1 s)" % ("—" if fl is None else "%.1f" % fl)))
        md.append("## Bascule DNS vue par chaque client (mesures brutes, `probes/ha01_dnswatch.py`)")
        for k, lab in (("windows", "Windows — résolveur système + cache DNS Windows + NRPT (`api.helios.orange.tn`)"), ("node", "Nœud .158 — glibc, /etc/resolv.conf (.160 puis .162), sans cache système")):
            x = w.get(k) or {}
            md.append("- **%s** : première erreur à T+%s s ; première connexion réussie vers le LB survivant à T+%s s ; coupure de **%s s** ; réponses périmées (.%s) : %s ; retour de .%s dans la réponse (fail-back) : T+%s s après la relance"
                      % (lab, x.get("first_fail_s", "—"), x.get("recover_s", "—"), x.get("outage_s", "—"), vm, x.get("stale_answers", "—"), vm, x.get("failback_s", "—")))
        md.append("- **dnsmasq survivant (.%s, 5 Hz)** : cesse de publier .%s à T+%s s ; dnsmasq .%s (mort) : dernière réponse à T+%s s" % (cfg["survivor_lb"], vm, "—" if fl is None else "%.1f" % fl, vm, w.get("dead_last_answer_s", "—")))
        md.append("")
    md.append("## Points de vue (hors gel | brut)")
    for vname in VANTAGES:
        if vname in per and per[vname]["raw"]:
            md.append("- **%s** — exclus E6 : %d échantillons dont %d échecs" % (vname, per[vname]["n_exclus"], per[vname]["echecs_exclus"]))
            for probe in sorted(per[vname]["raw"]):
                md.append("  - `%s` : %s | brut %s" % (probe, O.fmt_probe(per[vname]["hors"], probe), O.fmt_probe(per[vname]["raw"], probe)))
                runs = per[vname]["raw"][probe].get("failure_runs")
                if runs:
                    md.append("    - séries d'échecs (brut) : %s" % runs[:8])
    md.append("")
    md.append("## Chronologie mesurée (secondes après l'arrêt, sauf mention)")
    md.append("- nœud mort NotReady après : %s s ; etcd 4/4 après : %s s ; baux de nœuds (âge max, s) : %s" % (c["dead_notready_after_s"], c["etcd_ok_after_s"], c["lease_max"]))
    md.append("- CNPG pendant la panne : %s" % c["cnpg_timeline"])
    md.append("- retour (secondes après la relance) : %s" % c["return"])
    md.append("- pods non Running à T+90 s / T+300 s : %s" % c["pending_note"])
    return rows, "\n".join(md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vm", required=True, choices=sorted(VMS))
    ap.add_argument("--label", required=True)
    ap.add_argument("--confirmed", action="store_true")
    ap.add_argument("--observe", type=int, default=600)
    ap.add_argument("--stabilize", type=int, default=1800)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-cached-probe", action="store_true")
    a = ap.parse_args()
    vm, cfg = a.vm, VMS[a.vm]
    run = O.Run("p4-vmstop%s-%s" % (vm, a.label))
    say = run.say
    chk = preflight(a, vm, say, a.dry_run)
    run.save("preflight.json", chk)
    if not all(c["ok"] for c in chk):
        say("STOP: préconditions non satisfaites — AUCUNE VM éteinte.")
        return 3
    if a.dry_run:
        say("dry-run: préconditions OK (confirmation non exigée en dry-run) — aucune VM éteinte, aucune pause HELIOS, aucun filet armé.")
        return 0

    dns_mode = O.dns_state(cfg.get("survivor_lb", "162")).get("mode")         # contrôleur du LB SURVIVANT (.162 pour l'arrêt de .160 : inchangé ; .160 pour l'arrêt de .162)
    alive_eps = tuple(s for s in O.ETCD_ALL if s != vm)
    stopfile = os.path.join(run.dir, "REVIVE-DISARM.txt")
    revive_log = os.path.join(run.dir, "revive.log")
    streams, det, dns = [], None, None
    vm_stopped = False
    t_stop = t_vm_start = None
    abort = None
    rc_p, out_p = helios("pause")
    say("HELIOS (D7) : pause de l'agent hôte -> rc=%s %s" % (rc_p, out_p.replace("\n", " | ")[:140]))
    if rc_p != 0:
        say("STOP: la pause de l'agent hôte n'est pas effective — AUCUNE VM éteinte.")
        helios("resume")
        return 3
    revive_pid = spawn_revive(vm, a.observe + 900, stopfile, revive_log)
    if not revive_pid or not pid_alive(revive_pid):
        say("STOP: filet de survie (vm_revive.ps1) NON armé — AUCUNE VM éteinte.")
        helios("resume")
        return 4
    say("filet de survie armé (PID %s) : la VM sera rallumée automatiquement dans %d s si ce script disparaît" % (revive_pid, a.observe + 900))
    total = a.observe + a.stabilize + 1500
    cached_unit = "ha01-probe-161-cached-%s" % a.label
    cached_file = "/opt/helios-backups/ha01/probe161-cached-%s.jsonl" % a.label
    cached_ok = False if a.no_cached_probe else P2B.start_cached_probe(cached_unit, cached_file, total, say)
    say("sonde .161 avec cache DNS : %s" % ("démarrée" if cached_ok else "NON démarrée"))
    det = D.LiveDetector([O.NET + s for s in O.ETCD_ALL], period=5.0)
    det.start()
    dns = O.DnsPoller()
    dns.start()
    dw_state = start_dnswatch(run, a, say) if cfg.get("dnswatch") else None          # 4e scénario : sondes de bascule DNS (Windows, dnsmasq en direct, nœud .158)
    # Seul le canari du worker qui va être ÉTEINT est capté en direct (son journal disparaît avec son pod) ; les canaris survivants sont
    # lus en entier après coup (un flux passant par .153 pourrait casser pendant l'arrêt de .160).
    for dep in cfg["dead_canaries"]:
        streams.append(LogStream(dep, os.path.join(run.dir, "stream-%s.log" % dep)))
    timeline = run.jsonl("timeline.jsonl")
    term_before, leader_before = O.etcd_term_leader()
    notready_unexpected, mem_min, lease_max = {}, {}, {}
    host_min, etcd_bad_n, etcd_low_since = 99999, 0, None
    dead_notready_after_s = etcd_ok_after_s = None
    cnpg_timeline, pods_snap = [], {}
    ret, stab_anoms = {}, []
    stab_etcd_events, stab_explained = [], []
    stopped_ok, stop_detail = False, "arrêt non effectué"
    try:
        say("mesure de référence 40 s (détecteur de gels, DNS, flux de journaux des canaris, sonde avec cache)")
        time.sleep(40)
        ok_c, d_c = confirmation(vm)
        if not (ok_c and a.confirmed):
            raise SystemExit("confirmation invalide au dernier moment: %s" % d_c)
        say(">>> ARRÊT BRUTAL de la VM .%s (%s) : vmrun stop hard" % (vm, cfg["folder"]))
        t_stop = time.time()
        rc, out = vmrun("stop", vmx_of(vm), "hard", timeout=120)
        vm_stopped = True
        stopped_ok = not vm_running(vm)
        stop_detail = "vmrun rc=%s %s ; VM absente de `vmrun list`: %s" % (rc, out[:60], stopped_ok)
        say("arrêt : %s" % stop_detail)
        t_end = t_stop + a.observe
        canary_last_ok = {d: t_stop for d in cfg["alive_canaries"]}
        k = 0
        while time.time() < t_end:
            cyc = time.time()
            tp = int(cyc - t_stop)
            rec = {"ts": O.iso(), "t_plus_s": tp}
            ns = O.nodes_state()
            rec["nodes"] = ns
            for n, s in ns.items():
                if s != "Ready":
                    if n == cfg["node"]:
                        if dead_notready_after_s is None:
                            dead_notready_after_s = tp
                    else:
                        notready_unexpected.setdefault(n, [rec["ts"], rec["ts"]])[1] = rec["ts"]
            h = O.etcd_healthy(eps=alive_eps)
            rec["etcd"] = h
            if h >= 4 and etcd_ok_after_s is None:
                etcd_ok_after_s = tp
            etcd_bad_n = etcd_bad_n + 1 if h < 3 else 0
            if h < 4:
                etcd_low_since = etcd_low_since or cyc
            else:
                etcd_low_since = None
            hm = O.host_avail_mb()
            host_min = min(host_min, hm)
            rec["host_mb"] = hm
            if k % 3 == 0:
                for s in ("153", "155", "158") + (("160",) if cfg.get("dnswatch") else ()):
                    if s != vm:
                        m = O.mem_avail(s)
                        if m is not None:
                            mem_min[s] = min(mem_min.get(s, 9999), m)
                rec["mem"] = dict(mem_min)
            if cfg["kind"] != "lb" and k % 2 == 0:
                st = O.cnpg_state()
                if not cnpg_timeline or cnpg_timeline[-1][1] != st:
                    cnpg_timeline.append((tp, st))
                rec["cnpg"] = st
            for n, v in P2B.lease_ages().items():
                lease_max[n] = max(lease_max.get(n, 0), v)
            runs = {p: O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME}
            rec["fail_run_s"] = runs
            ages = {d: O.canary_ack_age(d) for d in cfg["alive_canaries"]} if k % 2 == 0 else {}
            rec["canary_age_s"] = {d: (None if v is None else round(v, 1)) for d, v in ages.items()}
            # Durée RÉELLE de mutisme d'un canari survivant : dernier instant où son dernier accusé avait <= 60 s. Une lecture illisible isolée (None) ne prouve rien : pendant
            # un basculement PostgreSQL le canari journalise des erreurs, donc `--tail=3` ne contient aucun accusé (cause de l'abandon à tort du 4 nov. 15:08Z, à T+14 s).
            for d, v in ages.items():
                if v is not None and v <= 60:
                    canary_last_ok[d] = cyc
            rec["p99_fsync_ms"] = {i.split(".")[-1]: round(v * 1000) for i, v in det.current().items() if v is not None}
            if tp >= 90 and "T90" not in pods_snap:
                rc, snap = O.kubectl("get pods -A -o wide --no-headers")
                pods_snap["T90"] = [l for l in snap.splitlines() if len(l.split()) > 3 and l.split()[3] not in ("Running", "Completed")]
                run.save("pods-T+90s.txt", snap)
            if tp >= 300 and "T300" not in pods_snap:
                rc, snap = O.kubectl("get pods -A -o wide --no-headers")
                pods_snap["T300"] = [l for l in snap.splitlines() if len(l.split()) > 3 and l.split()[3] not in ("Running", "Completed")]
                run.save("pods-T+300s.txt", snap)
            timeline.write(json.dumps(rec, ensure_ascii=False) + "\n")
            timeline.flush()
            why = None
            if etcd_bad_n >= 2:
                why = "quorum etcd menacé (%s membres sains sur 4 attendus) sur 2 cycles" % h
            elif etcd_low_since and cyc - etcd_low_since > 90 and tp > 40:
                why = "etcd à moins de 4 membres sains depuis plus de 90 s"
            elif mem_min.get("153", 9999) < 250:
                why = "MemAvailable .153 < 250 Mo"
            elif any(v < 250 for s, v in mem_min.items() if s in ("155", "158")):
                why = "MemAvailable du worker survivant < 250 Mo"
            elif hm < 600:
                why = "RAM hôte %s Mo < 600" % hm
            elif max(runs.values()) > 60:
                why = "sonde par nom en échec depuis %ss (vue Windows)" % max(runs.values())
            elif cfg.get("dnswatch") and max(runs["web"], runs["api_app"]) > 25:
                # .162 : `web` et `api_app` passent par le fichier hosts de Windows (.160 d'abord) : ils NE DOIVENT PAS être touchés. > 25 s = le LB survivant a un problème.
                why = "sonde web/api_app (fichier hosts, .160 d'abord) en échec depuis %ss : le LB survivant .%s ne sert pas" % (max(runs["web"], runs["api_app"]), cfg["survivor_lb"])
            elif cfg.get("dnswatch") and max(O.tail_fail_run(O.PROBE_WIN, "lb%s_web" % cfg["survivor_lb"]), O.tail_fail_run(O.PROBE_WIN, "lb%s_api" % cfg["survivor_lb"])) > 25:
                why = "LB survivant .%s en direct (web/API) en échec depuis plus de 25 s" % cfg["survivor_lb"]
            elif cfg.get("dnswatch") and mem_min.get(cfg["survivor_lb"], 9999) < 250:
                why = "MemAvailable du LB survivant .%s < 250 Mo" % cfg["survivor_lb"]
            else:
                for n, (t_first, t_last) in notready_unexpected.items():
                    if ns.get(n) != "Ready" and O.epoch_of(t_last) - O.epoch_of(t_first) > 120:
                        why = "nœud %s NotReady depuis plus de 2 min (non attendu)" % n
                for d in cfg["alive_canaries"]:
                    if cyc - canary_last_ok.get(d, t_stop) > 180:
                        why = "canari survivant %s muet depuis plus de 180 s (dernier accusé récent il y a %.0f s)" % (d, cyc - canary_last_ok.get(d, t_stop))
            if why:
                abort = why
                say("ABANDON: %s -> relance immédiate de la VM" % why)
                break
            k += 1
            dd = 5 - (time.time() - cyc)
            if dd > 0:
                time.sleep(dd)
        # ---------------------------------------------------------------- relance
        t_wait = time.time()
        while not abort and time.time() - t_wait < 900:
            h2 = O.host_avail_mb()
            if h2 >= 2000:
                break
            time.sleep(10)
        say("RELANCE de la VM .%s (RAM hôte %s Mo)" % (vm, O.host_avail_mb()))
        rc, out = vmrun("start", vmx_of(vm), "nogui", timeout=180)
        t_vm_start = time.time()
        say("vmrun start -> rc=%s %s ; en marche: %s" % (rc, out[:80], vm_running(vm)))
        t_t_ssh = t_ready = t_etcd = t_cnpg = t_pods = t_lb = t_dns = None
        streak = {}
        t0r = t_vm_start
        while time.time() - t0r < 1200:
            el = time.time() - t0r
            ssh_ok = O.ssh(vm, "true", timeout=15)[0] == 0
            ns = O.nodes_state()
            conds = {"ssh": ssh_ok, "ready": (cfg["node"] is None) or ns.get(cfg["node"]) == "Ready",
                     "etcd5": O.etcd_healthy() == 5, "pods": not O.unhealthy_pods()}
            if cfg["kind"] != "lb":
                st = O.cnpg_state()
                conds["cnpg"] = st.startswith("Cluster in healthy state") and st.endswith("|2")
            else:
                ok_l, _ = O.lb_direct_ok(vm)                 # le LB qui REVIENT (.160 pour l'arrêt de .160 : inchangé ; .162 pour l'arrêt de .162)
                up_l, _ = O.haproxy_all_up(vm)
                conds["lb"] = ok_l and up_l
                sts = {x: O.dns_state(x) for x in ("160", "162")}
                nominal = all({k2: v.get("status") for k2, v in (s.get("lbs") or {}).items()} == {"160": "UP", "162": "UP"} for s in sts.values())
                if dns_mode == "enforce":
                    nominal = nominal and all(s.get("publish") == [O.NET + O.DNS_PRIMARY] for s in sts.values())
                conds["dns"] = nominal
            for key, ok in conds.items():
                streak[key] = streak.get(key, 0) + 1 if ok else 0
                if streak[key] >= 2 and ("%s_s" % key) not in ret:
                    ret["%s_s" % key] = round(el)
            if all(("%s_s" % key) in ret for key in conds):
                break
            time.sleep(8)
        say("RETOUR mesuré (s après la relance): %s" % ret)
        # ---------------------------------------------------------------- stabilisation (lecture seule)
        if vm_running(vm):
            open(stopfile, "w").write("désarmé après relance confirmée %s\n" % O.iso())
            say("filet de survie désarmé (VM en marche)")
            rc_r, out_r = helios("resume")        # HELIOS actif pendant la stabilisation : on mesure le système tel qu'il tournera
            say("HELIOS : agent hôte rétabli -> rc=%s %s" % (rc_r, out_r.replace("\n", " | ")[:120]))
        t_s0 = time.time()
        say("STABILISATION %d s (lecture seule)" % a.stabilize)
        while time.time() - t_s0 < a.stabilize:
            ns = O.nodes_state()
            if len(ns) != 3 or any(v != "Ready" for v in ns.values()):
                stab_anoms.append("T+%ds nœuds: %s" % (time.time() - t_s0, ns))
            n_ok, ko = O.etcd_health_detail()
            if n_ok != 5:
                lab = "T+%ds" % (time.time() - t_s0)
                stab_anoms.append("%s etcd < 5 (membres KO: %s)" % (lab, ",".join("." + k for k in ko) or "?"))
                stab_etcd_events.append((time.time(), lab, ko))
            up = O.unhealthy_pods()
            if up:
                stab_anoms.append("T+%ds pods non sains: %s" % (time.time() - t_s0, up[:2]))
            r2 = {p: O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME}
            if max(r2.values()) > 10:
                stab_anoms.append("T+%ds sonde par nom en échec depuis %ss" % (time.time() - t_s0, max(r2.values())))
            time.sleep(30)
    finally:
        for s in streams:
            s.stop()
        if vm_stopped and not vm_running(vm):
            say("FINALLY: la VM .%s est éteinte -> relance d'urgence" % vm)
            vmrun("start", vmx_of(vm), "nogui", timeout=180)
        if vm_running(vm):
            try:
                open(stopfile, "w").write("désarmé en fin d'exécution %s\n" % O.iso())
            except OSError:
                pass
        rc_r, out_r = helios("resume")
        say("HELIOS : agent hôte rétabli -> rc=%s %s" % (rc_r, out_r.replace("\n", " | ")[:120]))
        if det:
            det.stop()
        if dns:
            dns.stop()
    if t_stop is None:
        say("aucun arrêt effectué.")
        return 4
    # ------------------------------------------------------------------ collecte et évaluation
    for t_ev, lab, ko in stab_etcd_events:         # écart E17 : creux etcd isolés dus à une rafale CPU de .153 (brut conservé, explication publiée)
        why = explain_etcd_dip(t_ev, ko)
        if why:
            stab_explained.append("%s etcd < 5 (KO: %s) — %s" % (lab, ",".join("." + k for k in ko), why))
            stab_anoms[:] = [x for x in stab_anoms if not x.startswith("%s etcd < 5" % lab)]
    t_a, t_b = t_stop, t_stop + a.observe
    stalls = list(det.windows()) + (D.windows(t_a - 60, t_b + 60) or [])
    vant = {"Windows": O.window_records_local(O.PROBE_WIN, t_a, t_b),
            ".161 sans cache": O.window_records_remote("161", O.P161_FILE, t_a, t_b)}
    vant[".161 avec cache"] = O.window_records_remote("161", cached_file, t_a, t_b, sudo=True) if cached_ok else []
    for nm, rs in vant.items():
        run.save("probes-%s.jsonl" % nm.replace(" ", "_").replace(".", ""), "\n".join(json.dumps(r, ensure_ascii=False) for r in rs) + "\n")
    logs = {}
    for dep in ("ha01-canary-w1", "ha01-canary-w2"):        # les JOURNAUX d'abord, la liste des id ENSUITE (sinon faux « écritures perdues »)
        st = next((s for s in streams if s.dep == dep), None)
        logs[dep] = st.text() if st else O.ha01_canary_report.fetch_log(dep)
        run.save("canary-%s.log" % dep, logs[dep])
    ids = O.ha01_canary_report.fetch_ids()
    canaries = {dep: O.ha01_canary_report.analyse(lg, ids, t_a - 5, t_b + 60, exclude=stalls, exclude_after_s=60) for dep, lg in logs.items()}
    term_after, leader_after = O.etcd_term_leader()
    rc, oomj = O.kubectl("get pods -A -o json", timeout=90)
    n_oom = 0
    try:
        for p in json.loads(oomj)["items"]:
            for cs in p["status"].get("containerStatuses", []):
                lt = cs.get("lastState", {}).get("terminated", {})
                if lt.get("reason") == "OOMKilled" and lt.get("finishedAt", "") >= O.iso(O.datetime.fromtimestamp(t_stop, O.timezone.utc)):
                    n_oom += 1
    except Exception:  # noqa: BLE001
        n_oom = -1
    rc, ev = O.kubectl("get events -A --field-selector reason=Evicted --no-headers")
    mem_ev = [l for l in ev.splitlines() if "memory" in l.lower()]
    dns_switch = dns.first_without("162", "160", t_stop) if (cfg["kind"] == "lb" and not cfg.get("dnswatch")) else None
    dnsw = None
    if dw_state:
        collect_dnswatch(run, dw_state)
        dnsw = analyse_dnswatch(run.dir, t_stop, t_vm_start, vm, cfg["survivor_lb"])
        run.save("dnswatch-analysis.json", dnsw)
    ctx = {"dnsw": dnsw, "vm": vm, "stalls": stalls, "vantages": vant, "canaries": canaries, "stopped_ok": stopped_ok, "stop_detail": stop_detail,
           "notready_unexpected": notready_unexpected, "dead_notready_after_s": dead_notready_after_s, "etcd_ok_after_s": etcd_ok_after_s,
           "term_before": term_before, "term_after": term_after, "leader_before": leader_before, "leader_after": leader_after,
           "mem_min": mem_min, "oom": n_oom, "mem_evictions": mem_ev, "host_min": host_min, "return": ret, "stabilize_s": a.stabilize,
           "stab_anomalies": stab_anoms, "stab_explained": stab_explained, "abort": abort, "dns_mode": dns_mode, "dns_switch_s": None if dns_switch is None else dns_switch - t_stop,
           "lease_max": lease_max, "cnpg_timeline": cnpg_timeline,
           "pending_note": {"T+90s": pods_snap.get("T90", []), "T+300s": pods_snap.get("T300", [])}}
    rows, extra = evaluate(ctx)
    run.save("dns.jsonl", "\n".join(json.dumps(r) for r in dns.records) + "\n")
    run.save("livedetector.json", {"points": [(t, n, p) for t, n, p in det.points], "windows": det.windows()})
    run.save("bridge-journal.txt", O.bridge_journal(t_stop - 120, time.time()))
    ok_all = O.write_report(run, "Arrêt réel de la VM .%s (%s) — %s" % (vm, cfg["folder"], O.iso(O.datetime.fromtimestamp(t_stop, O.timezone.utc))), rows, extra,
                            {"vm": vm, "label": a.label, "t_stop": t_stop, "t_vm_start": t_vm_start, "observe_s": a.observe, "abort": abort})
    say("RÉSULTAT: %d/%d critères satisfaits -> %s" % (sum(1 for _, ok, _ in rows if ok), len(rows), run.dir))
    for c, ok, v in rows:
        say("  %s %-95s %s" % ("OK" if ok else "KO", c[:95], str(v)[:120]))
    if abort:
        return 2
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
