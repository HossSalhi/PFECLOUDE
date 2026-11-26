#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HELIOS - watchdog hote "executeur de mise sous tension" (VMDOWN-POLICY-20261105).

Role UNIQUE : executer, sur ordre authentifie du backend, `vmrun start` pour une VM que la politique
autorise (AUTO), apres avoir PROUVE par `vmrun -T ws list` que la VM est eteinte.
Il n'a AUCUNE initiative : pas de boucle autonome, pas de sonde du cluster, pas de session SSH, pas de
connexion a HELIOS, aucun identifiant en dur. Un seul acteur declenche un redemarrage : le backend.

Contrat HTTP (VERSION_CONTRAT = 1), ecoute sur HELIOS_WATCHDOG_BIND:HELIOS_WATCHDOG_PORT (192.168.148.1:9099) :
  GET  /health                -> ouvert, sans secret (vivacite).
  GET  /vm/state              -> etat d'alimentation de TOUTES les VM de la table (un seul `vmrun list`).
  GET  /vm/<nom>/state        -> etat d'alimentation d'une VM + preuves (`evidence`) + garde-fous (`watchdog`).
  POST /restart/<nom>         -> demarrage idempotent d'une VM AUTO ; en-tetes X-Helios-Token (obligatoire),
                                 X-Helios-Incident (entier, obligatoire), X-Helios-Request-Id (optionnel).
Les routes /vm/* et /restart/* exigent : IP appelante autorisee (HELIOS_ALLOWED_CALLERS ; defaut : les 3 noeuds
du cluster ; .153 appelle ET, depuis la consigne du 2026-11-17, est une cible AUTO comme .155/.158) ET jeton valide
(fichier HELIOS_WATCHDOG_TOKEN_FILE, lu a chaque requete, comparaison a temps constant).

Politique (table fermee, noms EXACTS ; copie de infra/helios-vm-policy.yaml, parite verifiee par les tests) :
  AUTO (k8s-worker2, k8s-worker1, k8s-lb2, k8s-master : .153 est passee de DENY_RESTART a AUTO le 2026-11-17 sur demande
  de l'utilisateur) | DETECT_ONLY (k8s-monitoring : VM de supervision, consigne finale du 2026-11-06 -> 403 DETECTION_ONLY,
  jamais de `vmrun start`) | DENY_RESTART (mode CONSERVE et honore : aucune VM de la table ne l'utilise aujourd'hui ; 403
  NOT_AUTHORIZED) | toute autre cible : 404 UNKNOWN_VM. Aucun `vmrun start` n'est jamais lance pour un mode different d'AUTO.

Garanties de /restart : refus tant que l'etat n'est pas prouve POWERED_OFF (UNKNOWN => jamais de demarrage) ;
un seul demarrage a la fois par VM ; idempotence par incident, PERSISTEE (survit a un redemarrage du watchdog) ;
cooldown de succes et backoff d'echec persistants ; drapeau de maintenance ; garde RAM de l'hote.
Le client ne doit JAMAIS rejouer un POST /restart.

Variables d'environnement : voir la classe Config (valeurs par defaut = production).
"""
import argparse
import errno
import hashlib
import hmac
import json
import logging
import logging.handlers
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION_CONTRAT = 1
SOURCE_ETAT = "vmrun -T ws list"

# --- BEGIN VM_POLICY (parite testee avec infra/helios-vm-policy.yaml et VmPowerPolicy.java) ---
VM_POLICY = {
    "k8s-worker2": {"ip": "192.168.148.155", "mode": "AUTO"},
    "k8s-worker1": {"ip": "192.168.148.158", "mode": "AUTO"},
    "k8s-lb2": {"ip": "192.168.148.162", "mode": "AUTO"},
    "k8s-monitoring": {"ip": "192.168.148.160", "mode": "DETECT_ONLY"},
    "k8s-master": {"ip": "192.168.148.153", "mode": "AUTO"},
}
# --- END VM_POLICY ---

DEFAULT_VMRUN = r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
VM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
LIST_HEADER_RE = re.compile(r"Total running VMs:\s*(\d+)", re.IGNORECASE)
# HA-155-20261122 : le fichier de verrou <vmx>.lck\M<n>.lck contient "...<pid>-<horodatage>(<exe>) ..." (octets de controle compris).
LOCK_OWNER_RE = re.compile(rb"(\d{1,10})-(\d{1,20})\(([A-Za-z0-9_.-]{1,64}\.exe)\)")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_STARTED_AT = time.time()


# ------------------------------------------------------------------------------------------------
# Utilitaires
# ------------------------------------------------------------------------------------------------
def iso(ts):
    """Horodatage ISO 8601 UTC, millisecondes (ex. 2026-11-05T16:00:00.123Z)."""
    ms = int(round((ts % 1) * 1000))
    if ms >= 1000:
        ms = 999
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % ms


def sha12(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def norm_path(path):
    return os.path.normcase(os.path.normpath(str(path).strip().strip('"')))


def _env_float(env, key, default):
    try:
        return float(env.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _parse_cmd(raw, default):
    """HELIOS_VMRUN : chemin simple OU tableau JSON (ex. ["python", "fake_vmrun.py"]) pour les tests."""
    if not raw:
        return [default]
    raw = raw.strip()
    if raw.startswith("["):
        cmd = json.loads(raw)
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
            raise ValueError("HELIOS_VMRUN: tableau JSON de chaines non vides attendu")
        return cmd
    return [raw]


def lock_error(out):
    """La sortie de `vmrun start` signale-t-elle un verrou de fichier ("The file is already in use") ?"""
    low = (out or "").lower()
    return "in use" in low or "locked" in low or "lock" in low


def default_process_alive(pid, exe):
    """Le processus <pid> existe-t-il encore ET porte-t-il ce nom d'executable ? Faux si indeterminable (jamais d'exception)."""
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"], capture_output=True,
                               text=True, errors="replace", timeout=8, stdin=subprocess.DEVNULL,
                               creationflags=_NO_WINDOW)
            return re.search(r'"%s","%d"' % (re.escape(exe), pid), r.stdout or "", re.IGNORECASE) is not None
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def default_free_ram_mb():
    """RAM physique DISPONIBLE de l'hote en Mo, ou None si indeterminable (=> refus, fail-closed)."""
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return int(st.ullAvailPhys // (1024 * 1024))
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        return None
    return None


# ------------------------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------------------------
class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.vmrun_cmd = _parse_cmd(env.get("HELIOS_VMRUN"), DEFAULT_VMRUN)
        self.vm_root = env.get("HELIOS_VM_ROOT", r"C:\Users\houss\Documents\Virtual Machines")
        self.bind = env.get("HELIOS_WATCHDOG_BIND", "192.168.148.1")
        self.port = int(env.get("HELIOS_WATCHDOG_PORT", "9099"))
        self.token_file = env.get("HELIOS_WATCHDOG_TOKEN_FILE", r"C:\Users\houss\.helios-watchdog-token")
        self.state_dir = env.get("HELIOS_STATE_DIR", r"C:\Users\houss\helios-state")
        self.maintenance_dir = env.get("HELIOS_MAINTENANCE_DIR", r"C:\Users\houss\helios-maintenance")
        self.log_file = env.get("HELIOS_WATCHDOG_LOG", os.path.join(self.state_dir, "helios-watchdog.log"))
        # Appelants = adresses des NOEUDS du cluster (les pods backend sortent avec l'IP de leur noeud, natOutgoing).
        # .153 est autorisee a APPELER (le backend a 2 pods en anti-affinite stricte : l'un d'eux peut etre sur .153) ;
        # depuis le 17 nov. 2026 elle est AUSSI une cible AUTO (k8s-master) : quand elle est eteinte, les appelants sont
        # .155/.158. Liste identique a celle de Set-HeliosWatchdogFirewall.ps1 (parite testee). Decision du 5 nov. 2026.
        self.allowed_callers = frozenset(
            c.strip() for c in env.get(
                "HELIOS_ALLOWED_CALLERS", "192.168.148.153,192.168.148.155,192.168.148.158").split(",")
            if c.strip())
        self.min_free_ram_mb = int(env.get("HELIOS_MIN_FREE_RAM_MB", "1500"))
        self.list_timeout_s = _env_float(env, "HELIOS_VMRUN_LIST_TIMEOUT_S", 15)
        self.start_timeout_s = _env_float(env, "HELIOS_VMRUN_START_TIMEOUT_S", 90)
        self.start_confirm_s = _env_float(env, "HELIOS_START_CONFIRM_S", 20)
        self.confirm_poll_s = _env_float(env, "HELIOS_START_CONFIRM_POLL_S", 2)
        self.respond_wait_s = _env_float(env, "HELIOS_RESPOND_WAIT_S", 25)
        # HA-155-20261122 : verrou de configuration <vmx>.lck tenu par un autre processus (typiquement l'interface VMware
        # Workstation, VM ouverte dans un onglet). Attente BORNEE (reste sous respond_wait_s : la reponse est definitive) :
        # 0 = un seul essai, comme avant.
        self.lock_wait_s = _env_float(env, "HELIOS_LOCK_WAIT_S", 20)
        self.lock_retry_s = max(0.05, _env_float(env, "HELIOS_LOCK_RETRY_S", 3))
        self.cooldown_s = _env_float(env, "HELIOS_COOLDOWN_S", 240)
        self.list_cache_s = _env_float(env, "HELIOS_LIST_CACHE_S", 2)
        steps = env.get("HELIOS_BACKOFF_STEPS_S", "10,20,40,60")
        self.backoff_steps = tuple(float(x) for x in steps.split(",") if x.strip()) or (10.0,)
        self.incident_history = int(env.get("HELIOS_INCIDENT_HISTORY", "200"))
        self.socket_timeout_s = _env_float(env, "HELIOS_HTTP_TIMEOUT_S", 20)


# ------------------------------------------------------------------------------------------------
# vmrun : etat d'alimentation (single-flight + cache court) et demarrage
# ------------------------------------------------------------------------------------------------
class ListResult:
    __slots__ = ("ok", "reason", "paths", "count", "raw_sha12", "observed_at", "duration_ms")

    def __init__(self, ok, reason, paths, count, raw_sha12, observed_at, duration_ms):
        self.ok, self.reason, self.paths, self.count = ok, reason, paths, count
        self.raw_sha12, self.observed_at, self.duration_ms = raw_sha12, observed_at, duration_ms


class VmrunClient:
    def __init__(self, cfg, clock):
        self.cfg = cfg
        self.clock = clock
        self._lock = threading.Lock()  # tenu pendant l'execution : un seul `vmrun list` a la fois (single-flight)
        self._cache = None

    def list_running(self, max_age_s=None):
        """`vmrun -T ws list`, partage entre appelants concurrents pendant list_cache_s. max_age_s=0 => frais."""
        max_age = self.cfg.list_cache_s if max_age_s is None else max_age_s
        with self._lock:
            cached = self._cache
            if cached is not None and max_age > 0 and 0 <= self.clock() - cached.observed_at <= max_age:
                return cached
            result = self._exec_list()
            self._cache = result
            return result

    def _exec_list(self):
        observed_at = self.clock()
        t0 = time.monotonic()

        def fin(ok, reason, paths=None, count=None, raw=None):
            return ListResult(ok, reason, paths or [], count, raw, observed_at,
                              int((time.monotonic() - t0) * 1000))

        cmd = list(self.cfg.vmrun_cmd) + ["-T", "ws", "list"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                               timeout=self.cfg.list_timeout_s, stdin=subprocess.DEVNULL,
                               creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return fin(False, "VMRUN_TIMEOUT")
        except FileNotFoundError:
            return fin(False, "VMRUN_ABSENT")
        except Exception:
            return fin(False, "VMRUN_ERROR")
        if r.returncode != 0:
            return fin(False, "VMRUN_RC_%s" % r.returncode)
        text = (r.stdout or "").replace("\r", "")
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not lines:
            return fin(False, "FORMAT_INVALIDE")
        match = LIST_HEADER_RE.fullmatch(lines[0])
        if not match:
            return fin(False, "FORMAT_INVALIDE")
        count = int(match.group(1))
        paths = lines[1:]
        raw = sha12(text.strip())
        if len(paths) != count:
            return fin(False, "INCOHERENT", count=count, raw=raw)
        return fin(True, "OK", [norm_path(p) for p in paths], count, raw)

    def start(self, vmx):
        """Lance `vmrun -T ws start <vmx> nogui`. Retourne (code_retour|None, sortie|motif)."""
        cmd = list(self.cfg.vmrun_cmd) + ["-T", "ws", "start", vmx, "nogui"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                               timeout=self.cfg.start_timeout_s, stdin=subprocess.DEVNULL,
                               creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return None, "VMRUN_START_TIMEOUT"
        except FileNotFoundError:
            return None, "VMRUN_ABSENT"
        except Exception as exc:
            return None, "VMRUN_ERROR: %s" % exc
        return r.returncode, ((r.stdout or "") + " " + (r.stderr or "")).strip()


# ------------------------------------------------------------------------------------------------
# Etat persistant (cooldown, backoff, idempotence par incident)
# ------------------------------------------------------------------------------------------------
FAILURE_STATUSES = ("FAILED", "LOCK_BLOCKED", "STATE_UNKNOWN")


class StateStore:
    def __init__(self, directory, cfg, clock, event_log):
        self.cfg, self.clock, self.event_log = cfg, clock, event_log
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, "vm-power-state.json")
        self.lock = threading.RLock()
        self.data = self._load()

    @staticmethod
    def _fresh():
        return {"schema": 1, "presumed_cooldown_until": 0.0, "vms": {}, "incidents": {}}

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or data.get("schema") != 1 \
                    or not isinstance(data.get("vms"), dict) or not isinstance(data.get("incidents"), dict):
                raise ValueError("schema invalide")
        except FileNotFoundError:
            return self._fresh()
        except Exception as exc:
            # Fichier corrompu : on le conserve pour analyse et on presume un cooldown actif (fail-safe).
            now = self.clock()
            quarantine = "%s.corrupt-%s" % (self.path, datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            try:
                os.replace(self.path, quarantine)
            except OSError:
                pass
            data = self._fresh()
            data["presumed_cooldown_until"] = now + self.cfg.cooldown_s
            self.event_log(event="STATE_CORRUPT", detail=str(exc)[:200], quarantine=os.path.basename(quarantine))
            self._write(data)
            return data
        # Demarrages interrompus par l'arret du processus : consideres comme executes (jamais rejoues).
        interrupted = False
        now = self.clock()
        for key, rec in data["incidents"].items():
            if isinstance(rec, dict) and rec.get("status") == "STARTING":
                rec["status"] = "INTERRUPTED"
                vm = data["vms"].setdefault(rec.get("vm", ""), {})
                vm["cooldown_until"] = max(float(vm.get("cooldown_until", 0.0)), now + self.cfg.cooldown_s)
                interrupted = True
        if interrupted:
            self._write(data)
        return data

    def _write(self, data):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def save(self):
        with self.lock:
            incidents = self.data["incidents"]
            while len(incidents) > self.cfg.incident_history:
                incidents.pop(next(iter(incidents)))
            self._write(self.data)

    def vm(self, name):
        return self.data["vms"].setdefault(name, {"cooldown_until": 0.0, "backoff_until": 0.0,
                                                  "backoff_n": 0, "last_start": None})

    def incident(self, incident):
        return self.data["incidents"].get(str(incident))

    def record_incident(self, incident, vm, status, request_id, detail=None):
        self.data["incidents"][str(incident)] = {"vm": vm, "status": status, "request_id": request_id,
                                                  "at": self.clock(), "detail": detail}


# ------------------------------------------------------------------------------------------------
# Coeur : decisions
# ------------------------------------------------------------------------------------------------
class StartJob:
    def __init__(self, vm, incident, request_id):
        self.vm, self.incident, self.request_id = vm, incident, request_id
        self.done = threading.Event()
        self.status = None  # STARTED | ALREADY_RUNNING | STATE_UNKNOWN | LOCK_BLOCKED | FAILED
        self.detail = None


class Watchdog:
    def __init__(self, cfg=None, clock=time.time, free_ram_mb=default_free_ram_mb, process_alive=default_process_alive):
        self.cfg = cfg or Config()
        self.clock = clock
        self.free_ram_mb = free_ram_mb
        self.process_alive = process_alive
        os.makedirs(self.cfg.state_dir, exist_ok=True)
        os.makedirs(self.cfg.maintenance_dir, exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(self.cfg.log_file)), exist_ok=True)
        self._dlog = logging.getLogger("helios.vmwatchdog.%d" % id(self))
        self._dlog.setLevel(logging.INFO)
        self._dlog.propagate = False
        self._handlers = [logging.handlers.RotatingFileHandler(
            self.cfg.log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")]
        for h in self._handlers:
            h.setFormatter(logging.Formatter("%(message)s"))
            self._dlog.addHandler(h)
        self.vmrun = VmrunClient(self.cfg, clock)
        self.lock = threading.RLock()
        self.jobs = {}  # vm -> StartJob en cours
        self.store = StateStore(self.cfg.state_dir, self.cfg, clock, self.event)

    # -- journal : une ligne JSON par decision, JAMAIS de jeton -------------------------------------
    def event(self, **fields):
        rec = {"ts": iso(self.clock())}
        rec.update(fields)
        try:
            self._dlog.info(json.dumps(rec, sort_keys=True, ensure_ascii=True))
        except Exception:
            pass

    def close(self):
        for h in self._handlers:
            try:
                self._dlog.removeHandler(h)
                h.close()
            except Exception:
                pass

    # -- jeton ----------------------------------------------------------------------------------
    def read_token(self):
        try:
            with open(self.cfg.token_file, encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""

    # -- etat d'alimentation --------------------------------------------------------------------
    def vmx_path(self, vm):
        return os.path.join(self.cfg.vm_root, vm, vm + ".vmx")

    def lock_owner(self, vm):
        """Proprietaire du verrou de configuration <vmx>.lck : {"pid", "exe", "alive"} ou None. LECTURE SEULE : le verrou n'est
        ni modifie ni supprime, aucun processus n'est touche."""
        directory = self.vmx_path(vm) + ".lck"
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return None
        for name in names:
            if not (name.upper().startswith("M") and name.lower().endswith(".lck")):
                continue
            try:
                with open(os.path.join(directory, name), "rb") as f:
                    match = LOCK_OWNER_RE.search(f.read(1024))
            except OSError:
                continue
            if match:
                pid, exe = int(match.group(1)), match.group(3).decode("ascii", "replace")
                return {"pid": pid, "exe": exe, "alive": bool(self.process_alive(pid, exe))}
        return None

    def lock_diagnosis(self, vm):
        """(proprietaire|None, texte ASCII) : QUI tient <vm>.vmx.lck et que faire. Texte repris dans le detail de la reponse
        (donc dans l'historique de l'incident cote backend)."""
        owner = self.lock_owner(vm)
        name = os.path.basename(self.vmx_path(vm)) + ".lck"
        if owner is None:
            return None, "PROPRIETAIRE_INTROUVABLE : verrou de %s non attribuable" % name
        exe, pid = owner["exe"], owner["pid"]
        if exe.lower() == "vmware.exe":
            if owner["alive"]:
                return owner, ("LOCK_HELD_BY_VMWARE_UI pid=%d : l'interface VMware Workstation tient %s (VM ouverte dans un "
                               "onglet) ; fermer l'interface ou l'onglet de cette VM, le demarrage automatique pourra alors "
                               "aboutir" % (pid, name))
            return owner, ("LOCK_STALE_VMWARE_UI pid=%d absent : verrou orphelin de %s, a traiter par un operateur "
                           "(jamais supprime automatiquement)" % (pid, name))
        return owner, "LOCK_HELD_BY_%s pid=%d (%s) : %s" % (exe, pid, "actif" if owner["alive"] else "absent", name)

    def maintenance_active(self, vm):
        """Fail-closed : dossier inaccessible => maintenance. Drapeau = <dossier>/<vm>.flag."""
        try:
            if not os.path.isdir(self.cfg.maintenance_dir):
                return True
            os.stat(os.path.join(self.cfg.maintenance_dir, vm + ".flag"))
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return True

    def derive(self, vm, lr):
        """(power_state, state_reason, evidence) pour une VM, d'apres un resultat de `vmrun list`."""
        vmx = self.vmx_path(vm)
        now = self.clock()
        ev = {"source": SOURCE_ETAT, "observed_at": iso(lr.observed_at),
              "age_ms": max(0, int((now - lr.observed_at) * 1000)), "duration_ms": lr.duration_ms,
              "running_count": lr.count, "vmx_matched": None,
              "vmx_sha256_12": sha12(norm_path(vmx)), "list_sha256_12": lr.raw_sha12}
        if not os.path.isfile(vmx):
            return "UNKNOWN", "VMX_ABSENT", ev
        if not lr.ok:
            return "UNKNOWN", lr.reason, ev
        matched = norm_path(vmx) in lr.paths
        ev["vmx_matched"] = matched
        return ("RUNNING" if matched else "POWERED_OFF"), "OK", ev

    def observe(self, vm, fresh=False):
        return self.derive(vm, self.vmrun.list_running(0 if fresh else None))

    def _remaining(self, until):
        return max(0.0, until - self.clock())

    def guards(self, vm, power_state):
        """Garde-fous du watchdog pour une VM : (refusal_codes, details). Aucun effet de bord."""
        entry = VM_POLICY[vm]
        codes, details = [], {}
        if entry["mode"] == "DETECT_ONLY":
            codes.append("DETECTION_ONLY")
        elif entry["mode"] != "AUTO":
            codes.append("NOT_AUTHORIZED")
        if power_state == "RUNNING":
            codes.append("STATE_RUNNING")
        elif power_state != "POWERED_OFF":
            codes.append("STATE_UNKNOWN")
        with self.lock:
            vm_state = self.store.vm(vm)
            starting = vm in self.jobs
            backoff = self._remaining(float(vm_state.get("backoff_until", 0.0)))
            cooldown = max(self._remaining(float(vm_state.get("cooldown_until", 0.0))),
                           self._remaining(float(self.store.data.get("presumed_cooldown_until", 0.0))))
            last_start = vm_state.get("last_start")
        maintenance = self.maintenance_active(vm)
        ram = self.free_ram_mb()
        if maintenance:
            codes.append("MAINTENANCE")
        if starting:
            codes.append("ALREADY_STARTING")
        if backoff > 0:
            codes.append("BACKOFF")
        if cooldown > 0:
            codes.append("COOLDOWN_ACTIVE")
        if ram is None:
            codes.append("HOST_RAM_UNKNOWN")
        elif ram < self.cfg.min_free_ram_mb:
            codes.append("HOST_RAM_LOW")
        details.update(maintenance=maintenance, starting=starting, backoff=backoff, cooldown=cooldown,
                       ram=ram, last_start=last_start)
        return codes, details

    def state_view(self, vm):
        power_state, reason, ev = self.observe(vm)
        codes, d = self.guards(vm, power_state)
        last = d["last_start"]
        return {
            "vm": vm, "mode": VM_POLICY[vm]["mode"], "power_state": power_state, "state_reason": reason,
            "evidence": ev,
            "watchdog": {
                "maintenance": d["maintenance"], "starting": d["starting"], "start_allowed": not codes,
                "refusal_codes": codes,
                "cooldown_remaining_s": int(math.ceil(d["cooldown"])),
                "backoff_remaining_s": int(math.ceil(d["backoff"])),
                "last_start": ({"at": iso(last["at"]), "incident": last.get("incident"),
                                "result": last.get("result")} if last else None),
                "host_ram_mb": d["ram"],
            },
        }

    def states_view(self):
        lr = self.vmrun.list_running()
        out = {}
        for vm in VM_POLICY:
            power_state, reason, ev = self.derive(vm, lr)
            codes, d = self.guards(vm, power_state)
            out[vm] = {"vm": vm, "mode": VM_POLICY[vm]["mode"], "power_state": power_state,
                       "state_reason": reason, "evidence": ev,
                       "watchdog": {"maintenance": d["maintenance"], "starting": d["starting"],
                                    "start_allowed": not codes, "refusal_codes": codes,
                                    "cooldown_remaining_s": int(math.ceil(d["cooldown"])),
                                    "backoff_remaining_s": int(math.ceil(d["backoff"])),
                                    "last_start": ({"at": iso(d["last_start"]["at"]),
                                                    "incident": d["last_start"].get("incident"),
                                                    "result": d["last_start"].get("result")}
                                                   if d["last_start"] else None),
                                    "host_ram_mb": d["ram"]}}
        return {"generated_at": iso(self.clock()), "vms": out}

    def health(self):
        exe = self.cfg.vmrun_cmd[0]
        found = os.path.isfile(exe) or shutil.which(exe) is not None
        try:
            with open(__file__, "rb") as f:
                version = hashlib.sha256(f.read()).hexdigest()[:12]
        except Exception:
            version = "inconnue"
        return {"status": "ok", "auth_configured": bool(self.read_token()), "vmrun": "ok" if found else "absent",
                "version": version, "contract": VERSION_CONTRAT, "uptime_s": int(time.time() - _STARTED_AT)}

    # -- POST /restart --------------------------------------------------------------------------
    def _reply(self, code, vm, request_id, incident, status, **extra):
        body = {"vm": vm, "node": vm, "status": status, "request_id": request_id}
        if incident is not None:
            body["incident"] = incident
        if code == 200 and status == "STARTED":
            body["started"] = True
        elif code == 202 or (code == 200 and extra.get("noop")):
            pass  # demarrage en cours / sans effet : pas de champ "started"
        else:
            body["started"] = False
        body.update(extra)
        return code, body

    def restart(self, vm, incident_raw, request_id, caller):
        """Retourne (code_http, corps). Le SEUL chemin qui peut lancer `vmrun start`."""
        def done(code, status, incident=None, state_before=None, **extra):
            self.event(vm=vm, incident=incident, request_id=request_id, caller=caller, http=code,
                       decision=status, state_before=state_before)
            return self._reply(code, vm, request_id, incident, status, **extra)

        entry = VM_POLICY.get(vm)
        if entry is None:
            return done(404, "UNKNOWN_VM", error="VM inconnue", known=sorted(VM_POLICY))
        if entry["mode"] == "DETECT_ONLY":
            return done(403, "DETECTION_ONLY", error="detection seule : aucun redemarrage automatique")
        if entry["mode"] != "AUTO":
            return done(403, "NOT_AUTHORIZED", error="aucune autorisation de redemarrage")

        if incident_raw is None or not str(incident_raw).strip():
            return done(400, "INCIDENT_REQUIRED", error="en-tete X-Helios-Incident obligatoire")
        if not re.fullmatch(r"[0-9]{1,18}", str(incident_raw).strip()) or int(str(incident_raw).strip()) <= 0:
            return done(400, "INCIDENT_INVALID", error="X-Helios-Incident doit etre un entier positif")
        incident = int(str(incident_raw).strip())

        # Idempotence PAR INCIDENT (persistee) : un incident rejoue ne declenche jamais un second demarrage.
        with self.lock:
            prev = self.store.incident(incident)
            if prev is not None:
                if prev.get("vm") != vm:
                    return done(409, "INCIDENT_CONFLICT", incident, error="incident deja associe a une autre VM")
                status = prev.get("status")
                if status == "STARTING" and vm in self.jobs:
                    return done(200, "ALREADY_STARTING", incident, noop=True, reason="ALREADY_STARTING")
                if status in FAILURE_STATUSES:
                    code = 500 if status == "FAILED" else 503
                    return done(code, status, incident, replayed=True, previous_status=status,
                                detail=prev.get("detail"), error="echec precedent de cet incident, non rejoue")
                return done(200, "ALREADY_EXECUTED", incident, noop=True, reason="ALREADY_EXECUTED",
                            previous_status=status, replayed=True)

        if self.maintenance_active(vm):
            return done(423, "MAINTENANCE", incident, error="maintenance active : demarrage refuse")

        power_state, reason, _ev = self.observe(vm)
        if power_state == "RUNNING":
            with self.lock:
                self.store.record_incident(incident, vm, "ALREADY_RUNNING", request_id, "RUNNING_AVANT_DEMARRAGE")
                self.store.save()
            return done(200, "ALREADY_RUNNING", incident, state_before=power_state, noop=True,
                        reason="ALREADY_RUNNING")
        if power_state != "POWERED_OFF":
            return done(503, "STATE_UNKNOWN", incident, state_before=power_state, state_reason=reason,
                        retry_after_s=5, error="etat d'alimentation non prouve : aucun demarrage")

        with self.lock:
            if vm in self.jobs:
                return done(200, "ALREADY_STARTING", incident, state_before=power_state, noop=True,
                            reason="ALREADY_STARTING")
            vm_state = self.store.vm(vm)
            backoff = self._remaining(float(vm_state.get("backoff_until", 0.0)))
            if backoff > 0:
                return done(503, "BACKOFF", incident, state_before=power_state,
                            retry_after_s=max(1, int(math.ceil(backoff))), error="backoff apres echec")
            cooldown = max(self._remaining(float(vm_state.get("cooldown_until", 0.0))),
                           self._remaining(float(self.store.data.get("presumed_cooldown_until", 0.0))))
            if cooldown > 0:
                return done(503, "COOLDOWN_ACTIVE", incident, state_before=power_state,
                            retry_after_s=max(1, int(math.ceil(cooldown))), error="cooldown actif")
            ram = self.free_ram_mb()
            if ram is None:
                return done(503, "HOST_RAM_UNKNOWN", incident, state_before=power_state, retry_after_s=30,
                            error="RAM de l'hote indeterminable : aucun demarrage")
            if ram < self.cfg.min_free_ram_mb:
                return done(503, "HOST_RAM_LOW", incident, state_before=power_state, retry_after_s=60,
                            host_ram_mb=ram, min_free_ram_mb=self.cfg.min_free_ram_mb,
                            error="RAM de l'hote insuffisante : aucun demarrage")
            # Reservation PERSISTEE avant tout `vmrun start` : un crash en plein demarrage ne sera jamais rejoue.
            # Si elle ne peut pas etre ecrite (disque plein, fichier verrouille), AUCUN demarrage : sans trace
            # durable l'idempotence par incident ne serait plus garantie. Rien n'est laisse en memoire.
            job = StartJob(vm, incident, request_id)
            self.store.record_incident(incident, vm, "STARTING", request_id)
            try:
                self.store.save()
            except OSError as exc:
                self.store.data["incidents"].pop(str(incident), None)
                self.event(vm=vm, incident=incident, request_id=request_id, event="STATE_SAVE_FAILED",
                           phase="reservation", detail=str(exc)[:200])
                return done(503, "STATE_NOT_PERSISTED", incident, state_before=power_state, retry_after_s=30,
                            error="etat durable non ecrivable : aucun demarrage")
            self.jobs[vm] = job

        threading.Thread(target=self._run_start, args=(job,), name="start-" + vm, daemon=True).start()
        if not job.done.wait(timeout=self.cfg.respond_wait_s):
            return done(202, "STARTING", incident, state_before=power_state)
        return self._job_reply(job, done, power_state)

    def _job_reply(self, job, done, state_before):
        if job.status == "STARTED":
            return done(200, "STARTED", job.incident, state_before=state_before, state_after="RUNNING")
        if job.status == "ALREADY_RUNNING":
            return done(200, "ALREADY_RUNNING", job.incident, state_before=state_before, state_after="RUNNING",
                        noop=True, reason="ALREADY_RUNNING", detail=job.detail)
        if job.status in ("STATE_UNKNOWN", "LOCK_BLOCKED"):
            return done(503, job.status, job.incident, state_before=state_before, retry_after_s=10,
                        detail=job.detail, error="demarrage non confirme")
        return done(500, "FAILED", job.incident, state_before=state_before, detail=job.detail)

    def _run_start(self, job):
        try:
            job.status, job.detail = self._start_sequence(job.vm)
        except Exception as exc:  # jamais de thread orphelin sans resultat
            job.status, job.detail = "FAILED", "EXCEPTION: %s" % exc
            self.event(vm=job.vm, incident=job.incident, event="START_EXCEPTION",
                       trace=traceback.format_exc()[-500:])
        finally:
            try:
                with self.lock:
                    now = self.clock()
                    vm_state = self.store.vm(job.vm)
                    if job.status in ("STARTED", "ALREADY_RUNNING"):
                        vm_state["cooldown_until"] = now + self.cfg.cooldown_s
                        vm_state["backoff_n"] = 0
                        vm_state["backoff_until"] = 0.0
                        vm_state["last_start"] = {"at": now, "incident": job.incident, "result": job.status}
                    else:
                        steps = self.cfg.backoff_steps
                        n = int(vm_state.get("backoff_n", 0))
                        vm_state["backoff_until"] = now + steps[min(n, len(steps) - 1)]
                        vm_state["backoff_n"] = n + 1
                        vm_state["last_start"] = {"at": now, "incident": job.incident, "result": job.status}
                    self.store.record_incident(job.incident, job.vm, job.status, job.request_id, job.detail)
                    try:
                        self.store.save()
                    except OSError as exc:
                        # L'etat reste exact EN MEMOIRE pour la vie du processus ; seule la trace durable manque.
                        self.event(vm=job.vm, incident=job.incident, event="STATE_SAVE_FAILED",
                                   phase="fin", detail=str(exc)[:200])
                    finally:
                        self.jobs.pop(job.vm, None)
                self.event(vm=job.vm, incident=job.incident, request_id=job.request_id, event="START_DONE",
                           result=job.status, detail=(job.detail or "")[:300])
            finally:
                job.done.set()

    def _start_sequence(self, vm):
        """Prouve POWERED_OFF (etat FRAIS), lance `vmrun start`, confirme RUNNING. (statut, detail).

        HA-155-20261122 : si `vmrun start` echoue parce que le verrou de configuration <vmx>.lck est tenu par un autre
        processus (cas constate le 22 nov. : l'interface VMware Workstation, VM ouverte dans un onglet), on DIAGNOSTIQUE le
        proprietaire (lecture seule) puis on retente pendant lock_wait_s AU PLUS : le verrou peut se liberer. Chaque nouvel
        essai repart d'un etat d'alimentation FRAIS et du drapeau de maintenance. Un seul point d'appel a `vmrun start`."""
        power_state, reason, _ev = self.observe(vm, fresh=True)
        if power_state == "RUNNING":
            return "ALREADY_RUNNING", "RUNNING_AVANT_DEMARRAGE"
        if power_state != "POWERED_OFF":
            return "STATE_UNKNOWN", "etat avant demarrage: %s" % reason
        diag, lock_deadline = None, None
        while True:
            code, out = self.vmrun.start(self.vmx_path(vm))
            self.event(vm=vm, event="VMRUN_START", rc=code, out=(out or "")[:300], retry=lock_deadline is not None)
            if code == 0 or not lock_error(out):
                break
            if lock_deadline is None:
                owner, diag = self.lock_diagnosis(vm)
                self.event(vm=vm, event="LOCK_DIAGNOSED", owner=owner, diagnosis=diag)
                lock_deadline = time.monotonic() + self.cfg.lock_wait_s
            if time.monotonic() + self.cfg.lock_retry_s >= lock_deadline:
                break
            time.sleep(self.cfg.lock_retry_s)
            if self.maintenance_active(vm):
                return "LOCK_BLOCKED", ("maintenance posee pendant l'attente du verrou | %s" % diag)[:300]
            again, areason, _ = self.observe(vm, fresh=True)
            if again == "RUNNING":
                return "ALREADY_RUNNING", "RUNNING_PENDANT_ATTENTE_DU_VERROU"
            if again != "POWERED_OFF":
                return "STATE_UNKNOWN", "etat pendant l'attente du verrou: %s" % areason
        if code != 0:
            after, areason, _ = self.observe(vm, fresh=True)
            if after == "RUNNING":
                return "ALREADY_RUNNING", "RUNNING_APRES_RC_NON_NUL"
            if after != "POWERED_OFF":
                return "STATE_UNKNOWN", "etat apres echec de vmrun: %s" % areason
            if lock_error(out):
                return "LOCK_BLOCKED", (("%s | %s" % (diag, out)) if diag else (out or ""))[:300]
            return "FAILED", "VMRUN_RC_%s: %s" % (code, (out or "")[:300])
        deadline = time.monotonic() + self.cfg.start_confirm_s
        last = "UNKNOWN"
        while True:
            last, lreason, _ = self.observe(vm, fresh=True)
            if last == "RUNNING":
                return "STARTED", "CONFIRME_DANS_VMRUN_LIST"
            if time.monotonic() >= deadline:
                break
            time.sleep(self.cfg.confirm_poll_s)
        if last == "UNKNOWN":
            return "STATE_UNKNOWN", "verification apres demarrage non concluante"
        return "FAILED", "DEMARRAGE_NON_CONFIRME apres %ss" % self.cfg.start_confirm_s


# ------------------------------------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------------------------------------
class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # Windows : SO_REUSEADDR autoriserait un second ecouteur sur le port

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_handler(wd):
    class Handler(BaseHTTPRequestHandler):
        timeout = wd.cfg.socket_timeout_s
        server_version = "HeliosVmWatchdog"
        sys_version = ""

        def log_message(self, *args):
            pass

        def _send(self, code, obj, extra_headers=None):
            body = json.dumps(obj, ensure_ascii=True).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _drain_body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if 0 < n <= 65536:
                    self.rfile.read(n)
            except Exception:
                pass

        def _route(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                return "health", None, ("GET",)
            if path == "/vm/state":
                return "states", None, ("GET",)
            m = re.fullmatch(r"/vm/([^/]+)/state", path)
            if m:
                return "state", m.group(1), ("GET",)
            m = re.fullmatch(r"/restart/([^/]+)", path)
            if m:
                return "restart", m.group(1), ("POST",)
            return None, None, ()

        def _gate(self):
            """None si autorise, sinon (code, corps). IP appelante d'abord, puis jeton (fail-closed)."""
            caller = self.client_address[0]
            if caller not in wd.cfg.allowed_callers:
                wd.event(caller=caller, path=self.path.split("?", 1)[0], http=403, decision="CALLER_NOT_ALLOWED")
                return 403, {"status": "CALLER_NOT_ALLOWED", "error": "appelant non autorise"}
            expected = wd.read_token()
            if not expected:
                wd.event(caller=caller, path=self.path.split("?", 1)[0], http=503, decision="AUTH_NOT_CONFIGURED")
                return 503, {"status": "AUTH_NOT_CONFIGURED", "error": "authentification watchdog non configuree"}
            sent = self.headers.get("X-Helios-Token", "")
            if not sent or not hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8")):
                wd.event(caller=caller, path=self.path.split("?", 1)[0], http=401, decision="REJECTED")
                return 401, {"status": "REJECTED", "error": "jeton absent ou invalide"}
            return None

        def _handle(self, method):
            try:
                kind, name, methods = self._route()
                if kind is None:
                    self._drain_body()
                    return self._send(404, {"status": "NOT_FOUND"})
                if method not in methods:
                    self._drain_body()
                    return self._send(405, {"status": "METHOD_NOT_ALLOWED"}, {"Allow": ", ".join(methods)})
                if kind == "health":
                    return self._send(200, wd.health())
                denied = self._gate()
                if denied:
                    self._drain_body()
                    return self._send(*denied)
                if kind == "states":
                    return self._send(200, wd.states_view())
                if name is None or not VM_NAME_RE.match(name):
                    self._drain_body()
                    return self._send(400, {"status": "INVALID_VM_NAME", "error": "nom de VM invalide"})
                if kind == "state":
                    if name not in VM_POLICY:
                        return self._send(404, {"status": "UNKNOWN_VM", "known": sorted(VM_POLICY)})
                    return self._send(200, wd.state_view(name))
                # POST /restart/<nom>
                self._drain_body()
                request_id = self.headers.get("X-Helios-Request-Id", "")
                if not REQUEST_ID_RE.match(request_id or ""):
                    request_id = uuid.uuid4().hex[:16]
                code, body = wd.restart(name, self.headers.get("X-Helios-Incident"), request_id,
                                        self.client_address[0])
                extra = {"Retry-After": str(body["retry_after_s"])} if "retry_after_s" in body else None
                return self._send(code, body, extra)
            except Exception:
                wd.event(event="HANDLER_EXCEPTION", path=self.path.split("?", 1)[0],
                         trace=traceback.format_exc()[-800:])
                try:
                    self._send(500, {"status": "INTERNAL_ERROR"})
                except Exception:
                    pass

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_HEAD(self):
            self._handle("HEAD")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

        def do_PATCH(self):
            self._handle("PATCH")

        def do_OPTIONS(self):
            self._handle("OPTIONS")

    return Handler


def make_server(wd, bind=None, port=None):
    return _Server((bind or wd.cfg.bind, wd.cfg.port if port is None else port), make_handler(wd))


# ------------------------------------------------------------------------------------------------
# Demarrage du processus
# ------------------------------------------------------------------------------------------------
def check_report(cfg):
    """Diagnostic LECTURE SEULE (aucun demarrage, aucune ecoute) pour --check."""
    tok = ""
    try:
        with open(cfg.token_file, encoding="utf-8") as f:
            tok = f.read().strip()
    except Exception:
        pass
    exe = cfg.vmrun_cmd[0]
    return {
        "contract": VERSION_CONTRAT, "bind": "%s:%s" % (cfg.bind, cfg.port),
        "vmrun_found": bool(os.path.isfile(exe) or shutil.which(exe)),
        "token_configured": bool(tok), "token_length": len(tok),
        "allowed_callers": sorted(cfg.allowed_callers), "state_dir": cfg.state_dir,
        "state_dir_exists": os.path.isdir(cfg.state_dir), "maintenance_dir": cfg.maintenance_dir,
        "vmx": {vm: os.path.isfile(os.path.join(cfg.vm_root, vm, vm + ".vmx")) for vm in VM_POLICY},
        "modes": {vm: e["mode"] for vm, e in VM_POLICY.items()},
        "min_free_ram_mb": cfg.min_free_ram_mb, "free_ram_mb": default_free_ram_mb(),
    }


def serve(cfg=None, wait_bind_s=300.0, retry_s=5.0):
    cfg = cfg or Config()
    wd = Watchdog(cfg)
    deadline = time.monotonic() + wait_bind_s
    server = None
    while server is None:
        try:
            server = make_server(wd)
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, 10048, errno.EACCES, 10013):
                wd.event(event="AUTRE_INSTANCE", detail=str(exc))
                wd.close()
                return 0  # une instance tourne deja : sortie propre
            if time.monotonic() >= deadline:
                wd.event(event="BIND_ECHEC", detail=str(exc), bind=cfg.bind, port=cfg.port)
                wd.close()
                return 2  # le planificateur relancera (interface VMnet8 pas prete)
            wd.event(event="BIND_ATTENTE", detail=str(exc), bind=cfg.bind, port=cfg.port)
            time.sleep(retry_s)
    wd.event(event="READY", bind=cfg.bind, port=server.server_address[1], contract=VERSION_CONTRAT,
             modes={vm: e["mode"] for vm, e in VM_POLICY.items()})
    try:
        server.serve_forever()
    finally:
        wd.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="HELIOS - watchdog hote (executeur de mise sous tension)")
    parser.add_argument("--check", action="store_true", help="diagnostic lecture seule, sans ecoute ni demarrage")
    parser.add_argument("--wait-bind-s", type=float, default=300.0,
                        help="attente maximale de l'interface d'ecoute (VMnet8) avant d'echouer (code 2)")
    args = parser.parse_args(argv)
    try:
        cfg = Config()
    except Exception as exc:  # variable d'environnement invalide : echec explicite (le planificateur relancera)
        sys.stderr.write("configuration invalide: %s\n" % exc)
        return 3
    if args.check:
        print(json.dumps(check_report(cfg), indent=1, sort_keys=True))
        return 0
    try:
        return serve(cfg, wait_bind_s=args.wait_bind_s)
    except Exception:
        # Dernier recours : laisser une trace dans le journal (le processus tourne sans console).
        try:
            with open(cfg.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": iso(time.time()), "event": "FATAL",
                                    "trace": traceback.format_exc()[-1500:]}, sort_keys=True) + "\n")
        except Exception:
            pass
        return 3


if __name__ == "__main__":
    sys.exit(main())
