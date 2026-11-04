#!/usr/bin/env python3
"""
HA-01 — échantillonneur d'état (lecture seule), 1 ligne JSONL toutes les N secondes.

Collecte : RAM/charge/pression CPU de chaque VM (SSH), nœuds K8s, pods non sains, placement des pods
de `pfe`, état CNPG, santé des 5 membres etcd, état des serveurs HAProxy (.160/.162), RAM disponible
de l'hôte Windows. Le cluster est interrogé via .155, puis .158, et .153 EN DERNIER RECOURS
(.153 est le nœud fragile : on ne l'interroge pas inutilement).

  python ha01_sampler.py --out samples.jsonl [--interval 10] [--duration 0]
  python ha01_sampler.py --summarize samples.jsonl
Arrêt : fichier STOP-<nom> à côté de --out, --duration, ou Ctrl+C.
"""
import argparse
import csv
import ctypes
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR",
       "-o", "ServerAliveInterval=3", "-o", "ServerAliveCountMax=3"]
VM_IPS = ["192.168.148.153", "192.168.148.155", "192.168.148.158", "192.168.148.160", "192.168.148.162"]
CLUSTER_VIA = ["192.168.148.155", "192.168.148.158", "192.168.148.153"]
NODE_CMD = "cat /proc/loadavg; awk '/MemAvailable/{print $2}' /proc/meminfo; head -1 /proc/pressure/cpu 2>/dev/null; date -u +%s"
ETCD_EPS = ",".join("https://192.168.148.%s:2379" % s for s in ("153", "155", "158", "160", "162"))
CLUSTER_CMD = r"""
export KUBECONFIG=$HOME/.kube/config
echo '##NODES'; kubectl get nodes --no-headers --request-timeout=6s 2>&1
echo '##NOTOK'; kubectl get pods -A --no-headers --request-timeout=6s 2>&1 | awk '$4!="Running" && $4!="Completed"{print $1"/"$2" "$4}'
echo '##PFE'; kubectl -n pfe get pods --no-headers -o custom-columns=N:.metadata.name,S:.status.phase,NODE:.spec.nodeName --request-timeout=6s 2>&1
echo '##CNPG'; kubectl -n pfe get cluster.postgresql.cnpg.io postgres -o jsonpath='{.status.phase}|{.status.currentPrimary}|{.status.readyInstances}' --request-timeout=6s 2>&1; echo
echo '##ETCD'; sudo -n ETCDCTL_API=3 etcdctl --endpoints=%s --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key --command-timeout=4s endpoint health -w json 2>/dev/null | head -c 1500
echo
""" % ETCD_EPS


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def host_avail_mb():
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return int(m.ullAvailPhys / 1048576)


_LAST_ERR = {}


def ssh_run(ip, cmd, timeout=25):
    try:
        r = subprocess.run(SSH + ["kube@" + ip, cmd], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            _LAST_ERR[ip] = (r.stderr or "").strip()[:90]
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        _LAST_ERR[ip] = "timeout %ss" % timeout
        return 124, ""
    except Exception as e:  # noqa: BLE001
        _LAST_ERR[ip] = str(e)[:90]
        return 255, str(e)


def probe_vm(ip):
    rc, out = ssh_run(ip, NODE_CMD, timeout=14)
    if rc != 0:                      # une seule nouvelle tentative : l'hôte peut être momentanément saturé
        time.sleep(1.0)
        rc, out = ssh_run(ip, NODE_CMD, timeout=14)
    if rc != 0:
        return ip, {"up": False, "err": _LAST_ERR.get(ip, "")}
    ls = out.strip().splitlines()
    try:
        load1 = float(ls[0].split()[0])
        mem_mb = int(int(ls[1]) / 1024)
        psi = float(ls[2].split("avg10=")[1].split()[0]) if len(ls) > 2 and "avg10=" in ls[2] else None
        return ip, {"up": True, "load1": load1, "mem_avail_mb": mem_mb, "psi_cpu_some10": psi}
    except Exception:  # noqa: BLE001
        return ip, {"up": True, "parse_error": out[:80]}


def probe_cluster():
    for ip in CLUSTER_VIA:
        rc, out = ssh_run(ip, CLUSTER_CMD, timeout=30)
        if rc in (255, 124) or "##NODES" not in out:
            continue
        sec = {}
        cur = None
        for line in out.splitlines():
            if line.startswith("##"):
                cur = line[2:].strip()
                sec[cur] = []
            elif cur:
                sec[cur].append(line)
        nodes = {}
        for l in sec.get("NODES", []):
            p = l.split()
            if len(p) >= 2:
                nodes[p[0]] = p[1]
        etcd = None
        try:
            etcd = json.loads("".join(sec.get("ETCD", [])).strip())
            etcd = {e["endpoint"].split("//")[1].split(":")[0].split(".")[-1]: [e.get("health"), str(e.get("took", ""))] for e in etcd}
        except Exception:  # noqa: BLE001
            etcd = {"raw": "".join(sec.get("ETCD", []))[:200]}
        pfe = {}
        for l in sec.get("PFE", []):
            p = l.split()
            if len(p) >= 3:
                pfe[p[0]] = [p[1], p[2]]
        return {"via": ip, "nodes": nodes, "notok": [l for l in sec.get("NOTOK", []) if l.strip()][:15],
                "pfe": pfe, "cnpg": (sec.get("CNPG", [""])[0] if sec.get("CNPG") else ""), "etcd": etcd}
    return {"via": None}


def probe_haproxy(ip):
    try:
        with urllib.request.urlopen("http://%s:8404/;csv" % ip, timeout=3) as r:
            txt = r.read().decode()
        rd = csv.reader(io.StringIO(txt))
        hdr = next(rd)
        hdr[0] = hdr[0].lstrip("# ")
        ix = {h: i for i, h in enumerate(hdr)}
        res = {}
        for r in rd:
            if len(r) >= len(hdr) and r[ix["svname"]] not in ("FRONTEND", "BACKEND"):
                res["%s/%s" % (r[ix["pxname"]], r[ix["svname"]])] = r[ix["status"]]
        return ip, res
    except Exception as e:  # noqa: BLE001
        return ip, {"error": str(e)[:60]}


def cycle():
    with ThreadPoolExecutor(max_workers=4) as ex:   # peu de processus ssh.exe simultanés (hôte à RAM serrée)
        fv = [ex.submit(probe_vm, ip) for ip in VM_IPS]
        fc = ex.submit(probe_cluster)
        fh = [ex.submit(probe_haproxy, ip) for ip in ("192.168.148.160", "192.168.148.162")]
        vms = dict(f.result() for f in fv)
        cl = fc.result()
        hp = dict(f.result() for f in fh)
    return {"ts": now_iso(), "host_avail_mb": host_avail_mb(), "vms": {k.split(".")[-1]: v for k, v in vms.items()},
            "k8s": cl, "haproxy": {k.split(".")[-1]: v for k, v in hp.items()}}


def summarize(path):
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip() and '"vms"' in l]
    if not recs:
        print("aucun échantillon")
        return
    print("échantillons: %d  du %s au %s" % (len(recs), recs[0]["ts"], recs[-1]["ts"]))
    print("RAM hôte disponible (Mo): min=%d  médiane=%d" % (min(r["host_avail_mb"] for r in recs), sorted(r["host_avail_mb"] for r in recs)[len(recs) // 2]))
    for ip in sorted({k for r in recs for k in r["vms"]}):
        mem = [r["vms"][ip].get("mem_avail_mb") for r in recs if r["vms"].get(ip, {}).get("mem_avail_mb") is not None]
        ld = [r["vms"][ip].get("load1") for r in recs if r["vms"].get(ip, {}).get("load1") is not None]
        psi = [r["vms"][ip].get("psi_cpu_some10") for r in recs if r["vms"].get(ip, {}).get("psi_cpu_some10") is not None]
        down = sum(1 for r in recs if not r["vms"].get(ip, {}).get("up", False))
        print("  .%s  MemAvailable min=%s Mo  load1 max=%s  psi_cpu max=%s%%  injoignable=%d éch." % (
            ip, min(mem) if mem else "-", max(ld) if ld else "-", max(psi) if psi else "-", down))
    # transitions Ready par nœud
    prev = {}
    trans = {}
    for r in recs:
        for n, s in (r["k8s"].get("nodes") or {}).items():
            if n in prev and prev[n] != s:
                trans.setdefault(n, []).append((r["ts"], prev[n], s))
            prev[n] = s
    print("  transitions d'état des nœuds K8s:", {n: len(v) for n, v in trans.items()} or "aucune")
    for n, v in trans.items():
        print("    ", n, v[:6])
    notok = {}
    for r in recs:
        for l in r["k8s"].get("notok") or []:
            notok[l] = notok.get(l, 0) + 1
    print("  pods non sains (éch.):", dict(sorted(notok.items(), key=lambda x: -x[1])[:12]) or "aucun")
    unhealthy_etcd = sum(1 for r in recs if isinstance(r["k8s"].get("etcd"), dict) and any(v and v[0] is False for v in r["k8s"]["etcd"].values() if isinstance(v, (list, tuple))))
    print("  échantillons avec membre etcd non sain:", unhealthy_etcd)
    cn = {}
    for r in recs:
        cn[r["k8s"].get("cnpg", "")] = cn.get(r["k8s"].get("cnpg", ""), 0) + 1
    print("  CNPG (phase|primaire|prêtes):", cn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--duration", type=float, default=0)
    ap.add_argument("--summarize")
    a = ap.parse_args()
    if a.summarize:
        return summarize(a.summarize)
    if not a.out:
        sys.exit("--out requis")
    stop_file = os.path.join(os.path.dirname(os.path.abspath(a.out)), "STOP-" + os.path.basename(a.out))
    if os.path.exists(stop_file):
        os.remove(stop_file)
    t_end = time.time() + a.duration if a.duration else None
    with open(a.out, "a", encoding="utf-8") as out:
        out.write(json.dumps({"ts": now_iso(), "event": "start", "pid": os.getpid()}) + "\n")
        try:
            while True:
                t0 = time.time()
                rec = cycle()
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                if (t_end and time.time() >= t_end) or os.path.exists(stop_file):
                    break
                d = a.interval - (time.time() - t0)
                if d > 0:
                    time.sleep(d)
        except KeyboardInterrupt:
            pass
        out.write(json.dumps({"ts": now_iso(), "event": "stop"}) + "\n")
    if os.path.exists(stop_file):
        os.remove(stop_file)


if __name__ == "__main__":
    sys.exit(main())
