"""Redimensionne la RAM d'UN noeud control-plane SANS couper le cluster (grace au HA).
stop gracieux -> edit memsize dans le .vmx -> start -> attente Ready (restart containerd si
CNI 'not initialized') -> verif etcd 3/3 + CNPG. A lancer UN noeud a la fois.
Usage: python _ha_resize_node.py <k8s-master|k8s-worker1|k8s-worker2> <memMB>"""
import paramiko, shlex, subprocess, sys, time, re, os

VMRUN=r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
VMDIR=r"C:\Users\houss\Documents\Virtual Machines"
KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"
NODES={
 "k8s-master": ("192.168.148.153", os.path.join(VMDIR,"k8s-master","k8s-master.vmx")),
 "k8s-worker1":("192.168.148.158", os.path.join(VMDIR,"k8s-worker1","k8s-worker1.vmx")),
 "k8s-worker2":("192.168.148.155", os.path.join(VMDIR,"k8s-worker2","k8s-worker2.vmx")),
}
NODE=sys.argv[1]; NEWMEM=sys.argv[2]
IP,VMX=NODES[NODE]

def conn(ip,t=12):
    k=paramiko.Ed25519Key.from_private_key_file(KEY)
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip,username="kube",pkey=k,timeout=t,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,t=40):
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
def root(c,chain,t=80):
    i,o,e=c.exec_command("sudo -S -p '' bash -c "+shlex.quote(chain),timeout=t); i.write("kube\n"); i.flush()
    return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
def vmrun(*a,t=120):
    # ne JAMAIS crasher sur un timeout vmrun (stop soft peut pendre) -> on retourne None
    # et la logique appelante bascule sur stop hard via is_running().
    try:
        return subprocess.run([VMRUN,*a],capture_output=True,text=True,timeout=t)
    except Exception:
        return None
def is_running():
    return VMX.lower() in vmrun("list").stdout.lower()

print(f"== resize {NODE} ({IP}) -> {NEWMEM} Mo ==")
m=conn(MASTER)
print("  etat cluster avant:", run(m,f"kubectl get node {NODE} --no-headers 2>/dev/null"))
# 1. stop gracieux (soft), fallback hard
print("  stop gracieux...")
vmrun("stop",VMX,"soft",t=90)
for _ in range(12):
    if not is_running(): break
    time.sleep(5)
if is_running():
    print("  (soft timeout) -> stop hard"); vmrun("stop",VMX,"hard",t=60)
    for _ in range(8):
        if not is_running(): break
        time.sleep(3)
print("  VM off:", not is_running())
# 2. edit memsize dans le .vmx
with open(VMX,"r",encoding="utf-8",errors="replace") as f: txt=f.read()
old=re.search(r'memsize\s*=\s*"(\d+)"',txt)
txt2=re.sub(r'memsize\s*=\s*"\d+"',f'memsize = "{NEWMEM}"',txt)
with open(VMX,"w",encoding="utf-8") as f: f.write(txt2)
print(f"  memsize {old.group(1) if old else '?'} -> {NEWMEM} (ecrit dans .vmx)")
# 3. start
print("  start...")
r=vmrun("start",VMX,"nogui",t=120); print("   vmrun:", (r.stdout+r.stderr).strip() or "started")
# 4. attente Ready (+ restart containerd si CNI pas initialise)
print("  attente Ready...")
ready=False; kicked=False
for i in range(24):
    time.sleep(15)
    st=run(m,f"kubectl get node {NODE} --no-headers 2>/dev/null").split()
    st=st[1] if len(st)>1 else "?"
    print(f"   {15*(i+1):3d}s : {st}")
    if st=="Ready": ready=True; break
    if st=="NotReady" and i>=4 and not kicked:
        # CNI 'not initialized' apres reboot -> kick containerd+kubelet
        try:
            w=conn(IP); print("   kick containerd+kubelet:", root(w,"systemctl restart containerd; sleep 3; systemctl restart kubelet; echo OK")); w.close(); kicked=True
        except Exception as e: print("   ssh kick echec:", str(e)[:40])
# 5. verif etcd + cnpg (kube user = a un kubeconfig ; root/sudo n'en a pas -> localhost:8080)
print("  etcd membres sains/3:", run(m,"kubectl -n kube-system exec etcd-k8s-master -- etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key endpoint health --cluster 2>&1 | grep -c 'is healthy'"))
print("  CNPG:", run(m,"kubectl get cluster -n pfe --no-headers 2>&1 | tail -1"))
print("  RESULTAT:", "Ready OK" if ready else "PAS Ready (a verifier)")
m.close()
