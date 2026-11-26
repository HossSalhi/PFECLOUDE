# -*- coding: utf-8 -*-
"""VMDOWN-POLICY-20261105 - verification LECTURE SEULE du watchdog cible contre le VRAI vmrun.exe.

Prouve, sans rien demarrer ni arreter, que le watchdog cible lit correctement l'etat REEL des 5 VM de la politique
(format de `vmrun -T ws list`, chemins .vmx reels, preuves). Il ne fait QUE des GET sur un serveur ephemere en
127.0.0.1 avec un jeton aleatoire jetable : jamais le jeton de production, jamais 192.168.148.1:9099, jamais POST.
`VmrunClient.start` est neutralise : si le code tentait un demarrage, le script echouerait immediatement.

Usage : python check_watchdog_real_vmrun_readonly.py        (code 0 = OK)
"""
import http.client
import importlib.util
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHDOG = os.path.join(HERE, "..", "..", "infra", "helios-sot", "watchdog", "helios-vm-watchdog.py")

spec = importlib.util.spec_from_file_location("helios_vm_watchdog_readonly", WATCHDOG)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def forbidden_start(self, vmx):
    raise AssertionError("DEMARRAGE INTERDIT dans la verification en lecture seule : " + vmx)


mod.VmrunClient.start = forbidden_start   # garde de securite : aucun `vmrun start` ne peut partir d'ici


def main():
    tmp = tempfile.mkdtemp(prefix="helios-readonly-")
    token = secrets.token_hex(16)
    token_file = os.path.join(tmp, "token")
    with open(token_file, "w", encoding="utf-8") as f:
        f.write(token)
    env = {
        "HELIOS_WATCHDOG_BIND": "127.0.0.1", "HELIOS_WATCHDOG_PORT": "0", "HELIOS_ALLOWED_CALLERS": "127.0.0.1",
        "HELIOS_WATCHDOG_TOKEN_FILE": token_file, "HELIOS_STATE_DIR": os.path.join(tmp, "state"),
        "HELIOS_MAINTENANCE_DIR": os.path.join(tmp, "maint"), "HELIOS_WATCHDOG_LOG": os.path.join(tmp, "wd.log"),
    }   # HELIOS_VMRUN et HELIOS_VM_ROOT : valeurs REELLES par defaut (lecture seule)
    wd = mod.Watchdog(mod.Config(env))
    srv = mod.make_server(wd)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def get(path, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        try:
            conn.request("GET", path, headers={"X-Helios-Token": token} if auth else {})
            r = conn.getresponse()
            return r.status, json.loads(r.read() or b"{}")
        finally:
            conn.close()

    problems = []
    try:
        code, health = get("/health", auth=False)
        print("GET /health -> %s vmrun=%s auth_configured=%s contract=%s" % (
            code, health.get("vmrun"), health.get("auth_configured"), health.get("contract")))
        if code != 200 or health.get("vmrun") != "ok":
            problems.append("vmrun reel introuvable (/health)")

        code, allv = get("/vm/state")
        print("GET /vm/state -> %s (%d VM)" % (code, len(allv.get("vms", {}))))
        print("%-16s %-13s %-12s %-8s %-8s %-8s %-6s %s" % (
            "VM", "mode", "power_state", "reason", "matched", "dur_ms", "allow", "refusals"))
        hashes = set()
        for vm in mod.VM_POLICY:
            c, body = get("/vm/%s/state" % vm)
            ev, g = body.get("evidence", {}), body.get("watchdog", {})
            hashes.add(ev.get("list_sha256_12"))
            print("%-16s %-13s %-12s %-8s %-8s %-8s %-6s %s" % (
                vm, body.get("mode"), body.get("power_state"), body.get("state_reason"), ev.get("vmx_matched"),
                ev.get("duration_ms"), g.get("start_allowed"), ",".join(g.get("refusal_codes", []))))
            if c != 200:
                problems.append("%s: HTTP %s" % (vm, c))
            if body.get("state_reason") != "OK":
                problems.append("%s: state_reason=%s" % (vm, body.get("state_reason")))
            if body.get("power_state") not in ("RUNNING", "POWERED_OFF"):
                problems.append("%s: power_state=%s" % (vm, body.get("power_state")))
            if body.get("power_state") == "RUNNING" and g.get("start_allowed"):
                problems.append("%s: allumee mais start_allowed=True" % vm)
        print("RAM hote disponible (Mo): %s ; seuil de garde: %s" % (
            g.get("host_ram_mb"), wd.cfg.min_free_ram_mb))
        if len(hashes) != 1:
            problems.append("les preuves ne partagent pas le meme releve vmrun list (hashes=%s)" % hashes)
        if not any(v.get("power_state") == "RUNNING" for v in allv.get("vms", {}).values()):
            problems.append("aucune VM RUNNING : format vmrun probablement non reconnu")
    finally:
        srv.shutdown()
        srv.server_close()
        wd.close()
        shutil.rmtree(tmp, ignore_errors=True)

    if problems:
        print("\nECARTS:")
        for p in problems:
            print(" -", p)
        return 1
    print("\nOK : lecture reelle conforme ; aucun demarrage tente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
