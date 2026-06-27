"""Rallume les 5 VM dans l'ordre apres un reboot hote, puis verifie le HA.
Ordre : cluster (master, worker1, worker2) d'abord avec attente etcd, puis
aiCamunda + monitoring. Sequentiel pour eviter un pic RAM hote.
Usage: python _ha_start_all.py"""
import paramiko, subprocess, socket, time, shlex, os

VMRUN=r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
VMDIR=r"C:\Users\houss\Documents\Virtual Machines"
KEY="C:/Users/houss/.ssh/id_ed25519"
CLUSTER=[("k8s-master","192.168.148.153"),("k8s-worker1","192.168.148.158"),("k8s-worker2","192.168.148.155")]
EXTRA=[("k8s-aiCamunda","192.168.148.161"),("k8s-monitoring","192.168.148.160")]
def vmx(n): return os.path.join(VMDIR,n,n+".vmx")
def ping(ip): return 'TTL=' in subprocess.run(['ping','-n','1','-w','1500',ip],capture_output=True,text=True).stdout
def start(n):
    r=subprocess.run([VMRUN,"start",vmx(n),"nogui"],capture_output=True,text=True,timeout=150)
    return (r.stdout+r.stderr).strip() or ("started rc=%d"%r.returncode)
def wait_ping(ip,label,maxs=240):
    for i in range(maxs//10):
        time.sleep(10)
        if ping(ip): print(f"   {label} reseau OK ({10*(i+1)}s)"); return True
    print(f"   {label} PAS de reseau apres {maxs}s"); return False
def w1root(chain,t=45):
    k=paramiko.Ed25519Key.from_private_key_file(KEY)
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.148.158",username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
    i,o,e=c.exec_command("sudo -S -p '' bash -c "+shlex.quote("KUBECONFIG=/etc/kubernetes/admin.conf "+chain),timeout=t); i.write("kube\n"); i.flush()
    r=(o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip(); c.close(); return r

print("== 1. demarrage du CLUSTER (master, worker1, worker2) ==")
for n,ip in CLUSTER:
    print(f"  start {n}:", start(n)); wait_ping(ip,n)
print("== 2. attente Ready + etcd 3/3 (via worker1) ==")
for i in range(20):
    time.sleep(15)
    try:
        nodes=w1root("kubectl get nodes --no-headers 2>&1")
        eh=w1root("kubectl -n kube-system exec etcd-k8s-worker1 -- etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key endpoint health --cluster 2>&1").count("is healthy")
        ready=nodes.count(" Ready")
        print(f"   {15*(i+1):3d}s : Ready={ready}/3  etcd={eh}/3")
        if ready>=3 and eh>=3: break
    except Exception as ex: print(f"   {15*(i+1)}s : API pas prete ({str(ex)[:40]})")
print("== 3. demarrage aiCamunda + monitoring ==")
for n,ip in EXTRA:
    print(f"  start {n}:", start(n)); wait_ping(ip,n)
print("== 4. verif finale ==")
print(w1root("kubectl get nodes --no-headers 2>&1"))
print("CNPG:", w1root("kubectl get cluster -n pfe --no-headers 2>&1 | tail -1"))
print("backend/frontend:", w1root("kubectl get deploy -n pfe 2>&1 | grep -E 'backend|frontend'"))
print("pods Pending:", w1root("kubectl get pods -n pfe --no-headers 2>&1 | grep -c Pending"))
print("DONE - les 5 VM rallumees, verifier ci-dessus.")
