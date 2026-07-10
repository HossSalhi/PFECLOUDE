"""
HELIOS — Lanceur SÛR de la Phase 2 (test réel E2E dans le cluster).

Allume le cluster GRADUELLEMENT avec un GARDE-FOU RAM (protège l'hôte du gel en
cascade documenté), attend que le cluster soit sain, puis déclenche le test réel
DANS le cluster (crash loop podinfo) via prod_test_in_cluster.

aiCamunda (.161) doit déjà tourner (l'IA de prod). Ce script démarre master + workers
+ monitoring, un par un, en vérifiant la RAM libre après chacun.

    python scripts/launch_phase2.py            # démarrage gradué + test
    python scripts/launch_phase2.py --min-only # master + 1 worker + .161 (RAM minimale)
    python scripts/launch_phase2.py --force     # ignore le garde-fou RAM (déconseillé)
"""
from __future__ import annotations
import ctypes, subprocess, sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko

VMRUN = r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
VMDIR = Path(r"C:\Users\houss\Documents\Virtual Machines")
KEY = "C:/Users/houss/.ssh/id_ed25519"
MASTER = "192.168.148.153"

# (nom, vmx, RAM Go, requis pour --min-only)
VMS = [
    ("k8s-master",     VMDIR / "k8s-master/k8s-master.vmx",         2.5, True),
    ("k8s-worker1",    VMDIR / "k8s-worker1/k8s-worker1.vmx",       3.0, True),
    ("k8s-worker2",    VMDIR / "k8s-worker2/k8s-worker2.vmx",       3.0, False),
    ("k8s-monitoring", VMDIR / "k8s-monitoring/k8s-monitoring.vmx", 2.0, False),
]
RAM_FLOOR_GB = 1.0  # on n'allume pas une VM si la RAM libre passerait sous ce seuil


def free_gb() -> float:
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS(); m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 1e9


def vmrun(*args):
    return subprocess.run([VMRUN, "-T", "ws", *args], capture_output=True, text=True)


def running():
    out = vmrun("list").stdout
    return out


def master_ssh():
    key = paramiko.Ed25519Key.from_private_key_file(KEY)
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(MASTER, username="kube", pkey=key, timeout=8, allow_agent=False, look_for_keys=False)
    return c


def wait_master(deadline=240):
    t = time.time() + deadline
    while time.time() < t:
        try:
            c = master_ssh(); c.close(); return True
        except Exception:
            print("   ... master SSH pas encore prêt"); time.sleep(10)
    return False


def cluster_ready(deadline=300):
    t = time.time() + deadline
    while time.time() < t:
        try:
            c = master_ssh()
            _, o, _ = c.exec_command("kubectl get nodes --no-headers 2>/dev/null", timeout=20)
            lines = [l for l in o.read().decode().splitlines() if l.strip()]
            ready = sum(1 for l in lines if " Ready" in l)
            _, o2, _ = c.exec_command("kubectl get pods -n pfe -l app=podinfo --no-headers 2>/dev/null | grep -c Running", timeout=20)
            podinfo = o2.read().decode().strip()
            _, o3, _ = c.exec_command("kubectl get pods -n pfe --no-headers 2>/dev/null | grep -E 'postgres|backend' | grep -c Running", timeout=20)
            core = o3.read().decode().strip()
            c.close()
            print(f"   nodes Ready={ready}/{len(lines)} | podinfo Running={podinfo} | postgres+backend Running={core}")
            if ready >= 2 and podinfo == "1" and int(core or 0) >= 1:
                return True
        except Exception as e:
            print("   ... cluster pas encore prêt", type(e).__name__)
        time.sleep(15)
    return False


def main():
    min_only = "--min-only" in sys.argv
    force = "--force" in sys.argv
    todo = [v for v in VMS if (not min_only or v[3])]
    need = sum(v[2] for v in todo)
    fg = free_gb()
    print(f"RAM libre hôte : {fg:.1f} Go | VMs à démarrer : {', '.join(v[0] for v in todo)} (~{need:.1f} Go)")
    if fg < need + RAM_FLOOR_GB and not force:
        print(f"\n⛔ GARDE-FOU : {fg:.1f} Go libre < {need+RAM_FLOOR_GB:.1f} Go requis.")
        print("   Libère de la RAM (ferme navigateur/IDE) puis relance, OU --min-only, OU --force (risqué).")
        sys.exit(2)

    for name, vmx, ram, _ in todo:
        if name in running():
            print(f"[skip] {name} déjà démarrée"); continue
        fg = free_gb()
        if fg < ram + RAM_FLOOR_GB and not force:
            print(f"⛔ RAM libre {fg:.1f} Go insuffisante pour {name} ({ram} Go). Arrêt sûr.")
            sys.exit(2)
        print(f"[on] {name} (RAM libre {fg:.1f} Go)")
        r = vmrun("start", str(vmx), "nogui")
        if r.returncode != 0:
            print("   ERREUR vmrun:", r.stderr[:200]); sys.exit(1)
        time.sleep(20)  # laisser la VM booter avant la suivante

    print("\nAttente du master (SSH)...")
    if not wait_master():
        print("⛔ master injoignable."); sys.exit(1)
    print("Attente cluster sain (nodes Ready + podinfo + postgres)...")
    if not cluster_ready():
        print("⚠️ cluster pas confirmé sain dans le délai. Vérifie kubectl get pods -A. Test non lancé.")
        sys.exit(1)

    print("\n✅ Cluster prêt. Lancement du test réel E2E (crash loop dans le cluster)...\n")
    rc = subprocess.run([sys.executable, str(Path(__file__).parent / "prod_test_in_cluster.py")])
    sys.exit(rc.returncode)


if __name__ == "__main__":
    main()
