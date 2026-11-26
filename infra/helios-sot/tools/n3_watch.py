# -*- coding: utf-8 -*-
"""SUIVI EN DIRECT (lecture seule) d'un essai réel N3 : chronologie horodatée de la chaîne d'auto-rallumage d'une VM.

Il ne modifie rien : il interroge toutes les 10 s l'état d'alimentation servi par le watchdog (via un pod backend VIVANT),
le nœud Kubernetes, le bail du leader, l'incident du périmètre de la VM, ses actions RESTART_VM, son historique, les preuves
de récupération, le journal du watchdog et les lignes VmPowerWatch du backend, et n'imprime que les CHANGEMENTS.

  python infra/helios-sot/tools/n3_watch.py --vm k8s-worker1 [--max-min 25]
Fin : incident clos (RESOLVED / RESOLUTION_FAILED / CLOSED / ARCHIVED) + VM RUNNING + nœud Ready (puis ~40 s de marge),
ou délai dépassé. Sortie : une ligne `T+mm:ss  [source] message` par changement, puis un résumé.
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("n3_preflight", os.path.join(HERE, "n3_preflight.py"))
n3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(n3)
so = n3.so

WD_LOG = r"C:\Users\houss\helios-state\helios-watchdog.log"
FINAL = ("RESOLVED", "RESOLUTION_FAILED", "CLOSED", "ARCHIVED")
INTERESTING = ("VmPowerWatch", "RESTART_VM", "RESTART_NODE_VM", "execute-remediation", "verify-remediation", "Node-Remediation")


class Watch:
    def __init__(self, vm, max_min):
        self.vm, self.deadline = vm, time.time() + max_min * 60
        # "worker" (.155 .158) et "control-plane" (.153, depuis le 17 nov. 2026) = nœuds Kubernetes (incident NODE_NOT_READY : l'état du nœud est
        # SUIVI) ; "infra" (.162) = aucune notion de nœud.
        self.kind = n3.TARGETS[vm]["kind"]
        self.t_start = time.time()
        self.t0_local = (dt.datetime.now() - dt.timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
        self.seen = set()
        self.events = []          # (secondes depuis le début, source, message)
        self.wd_offset = os.path.getsize(WD_LOG) if os.path.isfile(WD_LOG) else 0
        self.final_since = None
        self.last = {}

    def emit(self, source, msg, dedup=True):
        """Émet une ligne horodatée. `dedup` (défaut) : une même ligne n'est émise qu'UNE fois (journaux répétés sur des fenêtres qui se
        chevauchent). Les CHANGEMENTS D'ÉTAT passent par `change()` avec dedup=False : « Ready=True » → « Ready=Unknown » → « Ready=True »
        doit donner TROIS lignes (défaut corrigé le 18 nov. 2026 : le retour à Ready de .153 n'avait pas été ré-émis)."""
        key = (source, msg)
        if dedup and key in self.seen:
            return
        self.seen.add(key)
        t = int(time.time() - self.t_start)
        self.events.append((t, source, msg))
        print(f"T+{t // 60:02d}:{t % 60:02d}  {dt.datetime.now():%H:%M:%S}  [{source}] {msg}", flush=True)

    def change(self, source, value):
        """N'émet que si la valeur change."""
        if self.last.get(source) != value:
            self.last[source] = value
            self.emit(source, str(value), dedup=False)

    # ── sources ──────────────────────────────────────────────────────────────────────────────────────────────
    def live_pod(self):
        rc, out, _ = so.kube("get", "pods", "-n", so.NS, "-o", "json", timeout=30)
        best = None
        for it in (json.loads(out).get("items", []) if rc == 0 else []):
            n = it["metadata"]["name"]
            cs = (it["status"].get("containerStatuses") or [{}])[0]
            if n.startswith("backend-") and it["status"].get("phase") == "Running" and cs.get("ready"):
                off_target = it["spec"].get("nodeName") != self.vm
                if best is None or (off_target and not best[0]):
                    best = (off_target, n)
        return best[1] if best else None

    def watchdog(self, pod):
        cmd = ('curl -s -m 8 -H "X-Helios-Token: $REMEDIATION_MASTER_WATCHDOG_TOKEN" '
               f'"$REMEDIATION_MASTER_WATCHDOG_URL/vm/{self.vm}/state"')
        rc, out, _ = so.kube("exec", "-n", so.NS, pod, "--", "sh", "-c", cmd, timeout=30)
        try:
            d = json.loads(out)
        except Exception:  # noqa: BLE001
            return
        w = d.get("watchdog") or {}
        self.change("vm", f"{self.vm} : {d.get('power_state')} (raison {d.get('state_reason')}) refus={w.get('refusal_codes')}")
        if w.get("last_start"):
            self.change("watchdog.last_start", json.dumps(w["last_start"], ensure_ascii=False))
        self.state = d.get("power_state")

    def node(self):
        rc, out, _ = so.kube("get", "node", self.vm, "-o", "jsonpath={.status.conditions[?(@.type==\"Ready\")].status}", timeout=25)
        self.node_ready = out.strip() == "True"
        self.change("noeud", f"{self.vm} Ready={out.strip() or 'inconnu'}")

    def database(self):
        rc, out, _ = so.kube("get", "pods", "-n", so.NS, "-l", "cnpg.io/instanceRole=primary", "-o", "jsonpath={.items[0].metadata.name}", timeout=25)
        primary = out.strip()
        if not primary:
            self.change("base", "primaire PostgreSQL introuvable")
            return
        sql = ("select 'lease', holder||'|'||extract(epoch from (now() at time zone 'Africa/Tunis' - renewed_at))::int from leader_lease where lease_name='aiops-scheduler';\n"
               f"select 'inc', id||'|'||status||'|'||created_at from incident where {n3.scope_condition(self.vm)} and created_at >= '{self.t0_local}' order by id desc limit 1;\n")
        r = n3._psql(primary, sql, [])
        lease = str(r.get("lease", "")).split("|")
        if lease and lease[0]:
            self.change("leader", lease[0][-18:] if len(lease) > 0 else "?")
        inc = str(r.get("inc", ""))
        if not inc:
            return
        iid, status, created = inc.split("|")[0], inc.split("|")[1], inc.split("|")[2]
        self.change("incident", f"#{iid} {status} (créé {created})")
        self.incident_status = status
        sql2 = (f"select 'act', id||'|'||status||'|'||coalesce(executed_by,'-')||'|'||coalesce(left(executor_result,150),'-')||'|'||coalesce(left(failed_reason,150),'-') from incident_action where incident_id={iid} and type='RESTART_VM' order by id;\n"
                f"select 'hist', id||'|'||status||'|'||left(comment,190) from incident_history where incident_id={iid} order by id;\n"
                f"select 'evid', id||'|'||captured_at||'|exit='||coalesce(exit_code::text,'-')||'|'||source from incident_terminal_evidence where incident_id={iid} order by id;\n"
                f"select 'veri', id||'|'||result||'|'||verified_at from incident_verification where incident_id={iid} order by id;\n")
        rc, out2, _ = so.kube("exec", "-i", "-n", so.NS, primary, "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "pfe_db",
                              "-At", "-F", "|", "-f", "-", timeout=40, stdin=sql2)
        for line in out2.splitlines():
            if "|" in line:
                k, v = line.split("|", 1)
                label = {"act": "action RESTART_VM", "hist": "historique", "evid": "preuve", "veri": "vérification"}.get(k, k)
                self.emit(label, v)

    def watchdog_log(self):
        if not os.path.isfile(WD_LOG):
            return
        size = os.path.getsize(WD_LOG)
        if size < self.wd_offset:
            self.wd_offset = 0
        with open(WD_LOG, "rb") as f:
            f.seek(self.wd_offset)
            chunk = f.read().decode("utf-8", "replace")
            self.wd_offset = f.tell()
        for line in chunk.splitlines():
            if "/restart/" in line or self.vm in line:
                self.emit("journal watchdog", line.strip()[:260])

    def backend_logs(self):
        rc, out, _ = so.kube("get", "pods", "-n", so.NS, "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}", timeout=25)
        for pod in [p for p in out.split() if p.startswith("backend-")]:
            rc, lg, _ = so.kube("logs", "-n", so.NS, pod, "--since=40s", timeout=30)
            if rc != 0:
                continue
            for line in lg.splitlines():
                if any(k in line for k in INTERESTING) and "tasks_scheduled" not in line:
                    self.emit(f"log {pod[-5:]}", line[11:230])

    # ── boucle ───────────────────────────────────────────────────────────────────────────────────────────────
    def run(self):
        self.state, self.node_ready, self.incident_status = None, None, None
        if self.kind not in n3.NODE_KINDS:
            self.node_ready = True   # sans objet pour une VM d'infrastructure : ne doit pas bloquer la fin du suivi
        print(f"Suivi de {self.vm} démarré à {dt.datetime.now():%H:%M:%S} (max {int((self.deadline - time.time()) / 60)} min)", flush=True)
        while time.time() < self.deadline:
            try:
                pod = self.live_pod()
                if pod:
                    self.watchdog(pod)
                if self.kind in n3.NODE_KINDS:
                    self.node()
                self.database()
                self.watchdog_log()
                self.backend_logs()
            except Exception as ex:  # noqa: BLE001 - un suivi ne doit jamais s'arrêter sur une sonde
                self.emit("suivi", f"sonde en erreur : {type(ex).__name__}: {ex}")
            done = self.incident_status in FINAL and self.state == "RUNNING" and self.node_ready
            if done:
                self.final_since = self.final_since or time.time()
                if time.time() - self.final_since >= 40:
                    break
            time.sleep(10)
        self.summary()

    def summary(self):
        print("\n==== RÉSUMÉ ====", flush=True)
        first = lambda src, frag: next((t for t, s, m in self.events if s == src and frag in m), None)
        for label, t in (("VM vue POWERED_OFF", first("vm", "POWERED_OFF")), ("nœud NotReady", first("noeud", "Ready=False") or first("noeud", "Ready=Unknown")),
                         ("incident créé", first("incident", "#")), ("action RESTART_VM", first("action RESTART_VM", "|")),
                         ("VM de nouveau RUNNING", next((t for t, s, m in self.events if s == "vm" and "RUNNING" in m and first("vm", "POWERED_OFF") is not None and t > (first("vm", "POWERED_OFF") or 0)), None)),
                         ("nœud Ready", next((t for t, s, m in self.events if s == "noeud" and "Ready=True" in m
                                              and (first("noeud", "Ready=Unknown") or first("noeud", "Ready=False")) is not None
                                              and t > (first("noeud", "Ready=Unknown") or first("noeud", "Ready=False") or 0)), None)),
                         ("incident clos", next((t for t, s, m in self.events if s == "incident" and any(f in m for f in FINAL)), None))):
            if self.kind not in n3.NODE_KINDS and label.startswith("nœud"):
                continue
            print(f"  {label:<24} {'T+%02d:%02d' % (t // 60, t % 60) if t is not None else '— (non observé)'}", flush=True)
        print(f"  état final : incident={self.incident_status} VM={self.state} nœud Ready={self.node_ready}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vm", required=True)
    ap.add_argument("--max-min", type=int, default=25)
    a = ap.parse_args(argv)
    if a.vm not in n3.TARGETS:
        print("REFUSÉ : VM non éligible (cibles : " + ", ".join(n3.TARGETS) + " ; k8s-monitoring (.160) est en détection seule).")
        return 2
    Watch(a.vm, a.max_min).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
