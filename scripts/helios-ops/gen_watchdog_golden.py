# -*- coding: utf-8 -*-
"""VMDOWN-POLICY-20261105 - contrat HTTP « golden » du watchdog hote.

Rejoue des scenarios contre le VRAI watchdog (infra/helios-sot/watchdog/helios-vm-watchdog.py) avec le faux
vmrun, normalise les valeurs volatiles et produit un fichier JSON par scenario dans infra/helios-sot/contract/.

Ces fichiers sont le CONTRAT partage :
  - cote Python : test_watchdog_contract.py verifie que le watchdog reel produit EXACTEMENT ces reponses ;
  - cote Java   : WatchdogClientTest sert ces reponses a WatchdogClient (faux serveur) et verifie sa lecture.

Usage :  python gen_watchdog_golden.py --write     (regenere les fichiers)
         python gen_watchdog_golden.py --check     (compare, code 1 si ecart ; n'ecrit rien)

Le jeton de test et les chemins temporaires n'apparaissent jamais dans les fichiers.
"""
import copy
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_watchdog_vm_policy as base  # noqa: E402  (Fx, TOKEN, MOD)

GOLDEN_DIR = os.path.join(base.REPO, "infra", "helios-sot", "contract")

ISO_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
INT_KEYS = {"age_ms", "duration_ms", "host_ram_mb", "retry_after_s", "uptime_s"}
POSITIVE_ONLY_INT_KEYS = {"cooldown_remaining_s", "backoff_remaining_s"}
HEX_KEYS = {"vmx_sha256_12", "list_sha256_12", "version"}


def normalize(value, key=None):
    if isinstance(value, dict):
        return {k: normalize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, key) for v in value]
    if isinstance(value, str):
        if ISO_RE.match(value):
            return "<iso>"
        if key == "request_id":
            return "<id>"
        if key in HEX_KEYS and re.fullmatch(r"[0-9a-f]{12}", value):
            return "<hex12>"
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        if key in INT_KEYS:
            return "<int>"
        if key in POSITIVE_ONLY_INT_KEYS:
            return "<int>" if value > 0 else 0
    return value


class Req:
    def __init__(self, method, path, incident=None, auth="valid", headers=None):
        self.method, self.path, self.incident, self.auth, self.headers = method, path, incident, auth, headers or {}

    def run(self, fx):
        token = {"valid": True, "missing": False, "invalid": "mauvais-jeton"}.get(self.auth, True)
        return fx.call(self.method, self.path, token=token, incident=self.incident, headers=self.headers)

    def describe(self):
        headers = dict(self.headers)
        if self.incident is not None:
            headers["X-Helios-Incident"] = str(self.incident)
        return {"method": self.method, "path": self.path, "headers": headers, "auth": self.auth}


def _wait_not_starting(fx, vm, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not fx.state(vm)[1]["watchdog"]["starting"]:
            return
        time.sleep(0.1)


def _prior_start_then_off(fx):
    fx.restart_vm("k8s-worker1", 301)
    fx.set_running()


# k8s-monitoring (.160) est REELLEMENT en DETECT_ONLY dans la table de production (consigne finale du 2026-11-06) :
# les scenarios « detection seule » ne simulent plus rien.
def _simuler_mode(vm, mode):
    """Simule le mode d'une VM (la table est restauree par run_scenario) : DENY_RESTART n'est plus celui d'aucune VM reelle."""
    base.MOD.VM_POLICY[vm]["mode"] = mode


SCENARIOS = [
    # ---- GET /vm/<nom>/state ----
    dict(name="state-running", setup=lambda fx: fx.set_running("k8s-worker1"),
         req=Req("GET", "/vm/k8s-worker1/state")),
    dict(name="state-powered-off", req=Req("GET", "/vm/k8s-worker1/state")),
    dict(name="state-unknown-timeout", env={"HELIOS_VMRUN_LIST_TIMEOUT_S": "0.6"},
         setup=lambda fx: fx.fake(list_mode="hang"), req=Req("GET", "/vm/k8s-worker1/state")),
    dict(name="state-detect-only", req=Req("GET", "/vm/k8s-monitoring/state")),
    # DENY_RESTART : mode CONSERVE par le watchdog mais plus utilise par aucune VM depuis le 2026-11-17 (k8s-master est AUTO) :
    # le scenario SIMULE le mode (la table est restauree par run_scenario) pour garder ce contrat vu par le client Java.
    dict(name="state-deny-restart", setup=lambda fx: _simuler_mode("k8s-master", "DENY_RESTART"),
         req=Req("GET", "/vm/k8s-master/state")),
    dict(name="state-master-auto", req=Req("GET", "/vm/k8s-master/state")),
    dict(name="state-unknown-vm", req=Req("GET", "/vm/lb2/state")),
    dict(name="state-bad-token", req=Req("GET", "/vm/k8s-worker1/state", auth="invalid")),
    dict(name="states-all", setup=lambda fx: fx.set_running("k8s-worker1", "k8s-master"), req=Req("GET", "/vm/state")),
    dict(name="health", req=Req("GET", "/health", auth="missing")),
    # ---- POST /restart/<nom> : succes et no-op ----
    dict(name="restart-started", req=Req("POST", "/restart/k8s-worker1", incident=101)),
    dict(name="restart-started-lb2", req=Req("POST", "/restart/k8s-lb2", incident=102)),
    dict(name="restart-started-master", req=Req("POST", "/restart/k8s-master", incident=116)),   # .153 AUTO (consigne du 2026-11-17)
    dict(name="restart-already-running", setup=lambda fx: fx.set_running("k8s-worker1"),
         req=Req("POST", "/restart/k8s-worker1", incident=103)),
    dict(name="restart-already-executed", prior=[Req("POST", "/restart/k8s-worker1", incident=104)],
         setup=lambda fx: fx.set_running(), req=Req("POST", "/restart/k8s-worker1", incident=104)),
    dict(name="restart-starting", env={"HELIOS_RESPOND_WAIT_S": "0.3"},
         setup=lambda fx: fx.fake(start_mode="slow", start_delay_s=1.5), req=Req("POST", "/restart/k8s-worker1",
                                                                                incident=105), after=_wait_not_starting),
    dict(name="restart-already-starting", env={"HELIOS_RESPOND_WAIT_S": "0.3"},
         setup=lambda fx: fx.fake(start_mode="slow", start_delay_s=1.5),
         prior=[Req("POST", "/restart/k8s-worker1", incident=106)],
         req=Req("POST", "/restart/k8s-worker1", incident=107), after=_wait_not_starting),
    # ---- POST /restart/<nom> : refus de politique ----
    dict(name="restart-detection-only", req=Req("POST", "/restart/k8s-monitoring", incident=111)),
    dict(name="restart-not-authorized", setup=lambda fx: _simuler_mode("k8s-master", "DENY_RESTART"),
         req=Req("POST", "/restart/k8s-master", incident=112)),
    dict(name="restart-unknown-vm", req=Req("POST", "/restart/lb2", incident=113)),
    dict(name="restart-invalid-name", req=Req("POST", "/restart/a%20b", incident=114)),
    dict(name="restart-incident-required", req=Req("POST", "/restart/k8s-worker1")),
    dict(name="restart-incident-invalid", req=Req("POST", "/restart/k8s-worker1", incident="abc")),
    dict(name="restart-incident-conflict", prior=[Req("POST", "/restart/k8s-worker1", incident=115)],
         req=Req("POST", "/restart/k8s-worker2", incident=115)),
    # ---- POST /restart/<nom> : authentification ----
    dict(name="restart-bad-token", req=Req("POST", "/restart/k8s-worker1", incident=121, auth="invalid")),
    dict(name="restart-missing-token", req=Req("POST", "/restart/k8s-worker1", incident=122, auth="missing")),
    dict(name="restart-auth-not-configured", setup=lambda fx: fx.write_token(""),
         req=Req("POST", "/restart/k8s-worker1", incident=123)),
    dict(name="restart-caller-not-allowed", env={"HELIOS_ALLOWED_CALLERS": "10.9.9.9"},
         req=Req("POST", "/restart/k8s-worker1", incident=124)),
    # ---- POST /restart/<nom> : garde-fous de l'hote ----
    dict(name="restart-maintenance", setup=lambda fx: fx.flag("k8s-lb2"), req=Req("POST", "/restart/k8s-lb2",
                                                                                incident=131)),
    dict(name="restart-state-unknown", setup=lambda fx: fx.fake(list_mode="garbage"),
         req=Req("POST", "/restart/k8s-worker1", incident=132)),
    dict(name="restart-ram-low", ram=1000, req=Req("POST", "/restart/k8s-worker1", incident=133)),
    dict(name="restart-cooldown", prior=[Req("POST", "/restart/k8s-worker1", incident=134)],
         setup=lambda fx: fx.set_running(), req=Req("POST", "/restart/k8s-worker1", incident=135)),
    dict(name="restart-backoff", setup=lambda fx: fx.fake(start_mode="rc1"),
         prior=[Req("POST", "/restart/k8s-worker1", incident=136)], req=Req("POST", "/restart/k8s-worker1",
                                                                          incident=137)),
    dict(name="restart-lock-blocked", setup=lambda fx: fx.fake(start_mode="lock"),
         req=Req("POST", "/restart/k8s-worker1", incident=138)),
    # ---- POST /restart/<nom> : echec de demarrage ----
    dict(name="restart-failed", setup=lambda fx: fx.fake(start_mode="rc1"),
         req=Req("POST", "/restart/k8s-worker1", incident=141)),
    dict(name="restart-failed-replayed", setup=lambda fx: fx.fake(start_mode="rc1"),
         prior=[Req("POST", "/restart/k8s-worker1", incident=142)], req=Req("POST", "/restart/k8s-worker1",
                                                                          incident=142)),
]


def run_scenario(sc):
    saved_policy = copy.deepcopy(base.MOD.VM_POLICY)   # un scenario peut simuler un mode : on restaure TOUJOURS la table
    fx = base.Fx(ram=sc.get("ram", 8000), **sc.get("env", {}))
    try:
        if "setup" in sc and sc["name"] not in ("restart-already-executed", "restart-cooldown"):
            sc["setup"](fx)
        for prior in sc.get("prior", []):
            prior.run(fx)
        if sc["name"] in ("restart-already-executed", "restart-cooldown"):
            sc["setup"](fx)  # la VM s'eteint de nouveau APRES le premier demarrage
        status, body, _ = sc["req"].run(fx)
        if "after" in sc:
            sc["after"](fx, "k8s-worker1")
        result = {"case": sc["name"], "request": sc["req"].describe(),
                  "response": {"status": status, "body": normalize(body)}}
        if "note" in sc:
            result["note"] = sc["note"]
        return result
    finally:
        fx.close()
        base.MOD.VM_POLICY.clear()
        base.MOD.VM_POLICY.update(saved_policy)


def build_all():
    return {sc["name"]: run_scenario(sc) for sc in SCENARIOS}


def dump(golden):
    return json.dumps(golden, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main(argv):
    mode = argv[0] if argv else "--check"
    built = build_all()
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    problems = 0
    for name, golden in built.items():
        path = os.path.join(GOLDEN_DIR, name + ".json")
        text = dump(golden)
        if mode == "--write":
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            continue
        try:
            with open(path, encoding="utf-8") as f:
                current = f.read()
        except FileNotFoundError:
            current = None
        if current != text:
            problems += 1
            print("ECART:", name)
    if mode == "--write":
        print("ecrit: %d fichiers dans %s" % (len(built), GOLDEN_DIR))
        return 0
    extra = sorted(set(n[:-5] for n in os.listdir(GOLDEN_DIR) if n.endswith(".json")) - set(built))
    for n in extra:
        problems += 1
        print("FICHIER ORPHELIN:", n)
    print("golden %s (%d scenarios)" % ("OK" if not problems else "EN ECART", len(built)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
