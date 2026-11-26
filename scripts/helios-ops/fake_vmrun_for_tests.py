# -*- coding: utf-8 -*-
"""Faux `vmrun` pour les tests du watchdog (VMDOWN-POLICY-20261105).

N'accede a AUCUNE VM, n'appelle JAMAIS le vrai vmrun.exe. Il imite seulement `vmrun -T ws list` et
`vmrun -T ws start <vmx> nogui`, d'apres un fichier d'etat JSON, et journalise chaque appel.

Usage (le watchdog l'appelle avec HELIOS_VMRUN = ["python", "fake_vmrun_for_tests.py",
"--fake-state", <etat.json>, "--fake-log", <journal.jsonl>]) :
    fake_vmrun_for_tests.py --fake-state S --fake-log L -T ws list
    fake_vmrun_for_tests.py --fake-state S --fake-log L -T ws start <vmx> nogui

Fichier d'etat :
  running     : liste des chemins .vmx allumes
  list_mode   : ok | rc1 | garbage | incoherent | empty | hang
  list_delay_s: delai avant de repondre a `list`
  start_mode  : ok | rc1 | lock | lock_then_ok | noeffect | slow | hang | rc1_but_runs
  start_delay_s: delai avant d'agir pour `start` (modes ok/slow)
  lock_failures: (mode lock_then_ok) nombre d'appels `start` qui echouent sur le verrou avant de reussir (HA-155-20261122)
  on_start_create_flag: chemin d'un fichier cree a CHAQUE appel `start` (simule la pose d'un drapeau de maintenance)
  on_start_set_running: si vrai, l'appel `start` ajoute la VM aux VM allumees meme s'il echoue (allumage manuel simule)
Journal : une ligne JSON par appel {"ts", "kind": list|start|other, "argv": [...]}.
"""
import json
import os
import sys
import time


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, data):
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def main(argv):
    state_path = log_path = None
    args = list(argv)
    while args and args[0] in ("--fake-state", "--fake-log"):
        flag, value = args[0], args[1]
        args = args[2:]
        if flag == "--fake-state":
            state_path = value
        else:
            log_path = value
    kind = "other"
    if args[:2] == ["-T", "ws"] and len(args) >= 3:
        kind = args[2] if args[2] in ("list", "start") else "other"
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "kind": kind, "argv": args}) + "\n")
    state = _read(state_path)

    if kind == "list":
        mode = state.get("list_mode", "ok")
        time.sleep(float(state.get("list_delay_s", 0)))
        if mode == "hang":
            time.sleep(60)
        if mode == "rc1":
            sys.stderr.write("Error: Unable to connect to the VMware service\n")
            return 1
        running = state.get("running", [])
        if mode == "garbage":
            sys.stdout.write("this is not a vmrun listing\r\n")
            return 0
        if mode == "empty":
            return 0
        count = len(running) + (1 if mode == "incoherent" else 0)
        sys.stdout.write("Total running VMs: %d\r\n" % count)
        for vmx in running:
            sys.stdout.write(vmx + "\r\n")
        return 0

    if kind == "start":
        mode = state.get("start_mode", "ok")
        vmx = args[3] if len(args) > 3 else ""
        # Evenements simules PENDANT l'appel (HA-155-20261122), deterministes (pas de minuteur) :
        if state.get("on_start_create_flag"):          # un operateur pose le drapeau de maintenance
            open(state["on_start_create_flag"], "w").close()
        if state.get("on_start_set_running") and vmx and vmx not in state.setdefault("running", []):
            state["running"].append(vmx)               # la VM est allumee a la main (interface) pendant que vmrun echoue
            _write(state_path, state)
        if mode == "hang":
            time.sleep(60)
        if mode == "lock":
            sys.stderr.write("Error: The virtual machine is in use by another process (locked)\n")
            return 1
        if mode == "lock_then_ok":
            # le verrou se libere apres `lock_failures` echecs : l'appel suivant se comporte comme le mode "ok"
            restants = int(state.get("lock_failures", 0))
            if restants > 0:
                state["lock_failures"] = restants - 1
                _write(state_path, state)
                sys.stderr.write("Error: The file is already in use\n")
                return 1
            mode = "ok"
        if mode == "rc1":
            sys.stderr.write("Error: The operation was canceled\n")
            return 1
        time.sleep(float(state.get("start_delay_s", 0)) if mode in ("ok", "slow") else 0)
        if mode == "noeffect":
            return 0
        state = _read(state_path)  # releire : l'etat a pu changer pendant le delai
        if vmx and vmx not in state.setdefault("running", []):
            state["running"].append(vmx)
            _write(state_path, state)
        if mode == "rc1_but_runs":
            sys.stderr.write("Error: warning reported but the VM is up\n")
            return 1
        return 0

    sys.stderr.write("fake vmrun: commande non geree: %r\n" % (args,))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
