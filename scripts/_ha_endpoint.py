"""Etape A du HA in-place : recreer kubeadm-config avec controlPlaneEndpoint=VIP,
regenerer le cert apiserver (SAN VIP), redemarrer l'apiserver, VERIFIER que l'API survit.
Rollback : si l'API ne revient pas, on restaure apiserver.crt/key.preha + le manifest."""
import paramiko, shlex, socket, time

KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"; VIP="192.168.148.150"
CFG = """apiVersion: kubeadm.k8s.io/v1beta3
kind: ClusterConfiguration
kubernetesVersion: v1.29.15
controlPlaneEndpoint: "192.168.148.150:6443"
networking:
  serviceSubnet: "10.96.0.0/12"
  podSubnet: "192.168.0.0/16"
apiServer:
  certSANs:
  - "192.168.148.150"
  - "192.168.148.153"
  - "192.168.148.158"
  - "192.168.148.155"
  - "k8s-master"
  - "kubernetes"
  - "kubernetes.default"
  - "kubernetes.default.svc"
  - "kubernetes.default.svc.cluster.local"
"""

def conn(ip):
    k=paramiko.Ed25519Key.from_private_key_file(KEY)
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip,username="kube",pkey=k,timeout=15,allow_agent=False,look_for_keys=False); return c
def root(c,chain,t=90):
    i,o,e=c.exec_command("sudo -S -p '' bash -c "+shlex.quote(chain),timeout=t); i.write("kube\n"); i.flush()
    return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
def api(ip):
    try:
        with socket.create_connection((ip,6443),timeout=4): return True
    except Exception: return False

c=conn(MASTER)
print("== 1. ecrire /tmp/kubeadm-ha.yaml ==")
root(c, "cat > /tmp/kubeadm-ha.yaml <<'EOFCFG'\n"+CFG+"\nEOFCFG")
print("   taille:", root(c,"wc -l /tmp/kubeadm-ha.yaml"))
print("== 2. upload-config kubeadm (cree le configmap) ==")
print(root(c,"kubeadm init phase upload-config kubeadm --config /tmp/kubeadm-ha.yaml 2>&1 | tail -3"))
print("== 3. regen cert apiserver avec SAN VIP ==")
print(root(c,"rm -f /etc/kubernetes/pki/apiserver.crt /etc/kubernetes/pki/apiserver.key && "
            "kubeadm init phase certs apiserver --config /tmp/kubeadm-ha.yaml 2>&1 | tail -3"))
print("   nouveaux SANs:", root(c,"openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -text | grep -A1 'Alternative Name' | tail -1"))
print("== 4. restart apiserver (mv manifest out/in) ==")
root(c,"mv /etc/kubernetes/manifests/kube-apiserver.yaml /tmp/kube-apiserver.yaml")
time.sleep(18)
root(c,"mv /tmp/kube-apiserver.yaml /etc/kubernetes/manifests/kube-apiserver.yaml")
print("== 5. attente API (via IP master ET via VIP) ==")
okm=okv=False
for i in range(20):
    time.sleep(10)
    okm=api(MASTER); okv=api(VIP)
    print(f"  {10*(i+1)}s : master:6443={'UP' if okm else 'down'}  VIP:6443={'UP' if okv else 'down'}")
    if okm and okv: break
if okm and okv:
    print("== 6. verif kubectl + SAN via VIP ==")
    print(root(c,"kubectl get nodes --no-headers 2>&1 | head -3"))
    print("=> ETAPE A OK : endpoint VIP + cert regeneres, API repond via master ET VIP.")
else:
    print("!! API ne revient pas -> ROLLBACK cert + manifest")
    print(root(c,"cp -a /etc/kubernetes/pki/apiserver.crt.preha /etc/kubernetes/pki/apiserver.crt; "
                "cp -a /etc/kubernetes/pki/apiserver.key.preha /etc/kubernetes/pki/apiserver.key; "
                "mv /tmp/kube-apiserver.yaml /etc/kubernetes/manifests/ 2>/dev/null; echo ROLLED_BACK"))
c.close()
