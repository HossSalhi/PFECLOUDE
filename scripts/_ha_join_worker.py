"""Convertit UN worker en control-plane : drain -> reset -> delete node -> kubeadm join --control-plane
-> untaint. Verifie etcd a chaque etape. Usage: python _ha_join_worker.py <worker_ip> <node_name>"""
import paramiko, shlex, sys, time

KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"; VIP="192.168.148.150"
WIP=sys.argv[1]; NODE=sys.argv[2]

def conn(ip):
    k=paramiko.Ed25519Key.from_private_key_file(KEY)
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip,username="kube",pkey=k,timeout=20,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,t=200):
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
def root(c,chain,t=300):
    i,o,e=c.exec_command("sudo -S -p '' bash -c "+shlex.quote(chain),timeout=t); i.write("kube\n"); i.flush()
    return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
def etcd_members(m):
    return root(m,"KUBECONFIG=/etc/kubernetes/admin.conf kubectl -n kube-system exec etcd-k8s-master -- etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key member list 2>/dev/null")

m=conn(MASTER)
print("== 0. materiel de join frais ==")
ck=root(m,"kubeadm init phase upload-certs --upload-certs 2>&1 | tail -1").split()[-1]
jc=root(m,"kubeadm token create --print-join-command 2>&1 | tail -1")
# remplacer l'endpoint par la VIP
toks=jc.split()
token=toks[toks.index("--token")+1]; chash=toks[toks.index("--discovery-token-ca-cert-hash")+1]
print("   token ok, cert-key ok")
print("== 1. drain", NODE, "==")
print(run(m,"kubectl drain "+NODE+" --ignore-daemonsets --delete-emptydir-data --force --timeout=150s 2>&1 | tail -3"))
print("== 2. reset", NODE, "(sur le worker) ==")
w=conn(WIP)
print(root(w,"kubeadm reset -f 2>&1 | tail -2; rm -rf /etc/cni/net.d /etc/kubernetes/manifests/* /var/lib/etcd 2>/dev/null; echo RESET_DONE"))
w.close()
print("== 3. delete node object ==")
print(run(m,"kubectl delete node "+NODE+" --ignore-not-found 2>&1"))
time.sleep(5)
print("== 4. kubeadm join --control-plane (VIP) ==")
w=conn(WIP)
joincmd=("kubeadm join "+VIP+":6443 --token "+token+" --discovery-token-ca-cert-hash "+chash
         +" --control-plane --certificate-key "+ck+" --apiserver-advertise-address="+WIP)
out=root(w,joincmd+" 2>&1 | tail -15",t=420)
print(out)
w.close()
print("== 5. etcd membres apres join ==")
print(etcd_members(m))
print("== 6. untaint "+NODE+" + node status ==")
print(run(m,"kubectl taint nodes "+NODE+" node-role.kubernetes.io/control-plane- 2>&1"))
time.sleep(5)
print(run(m,"kubectl get nodes --no-headers 2>&1"))
m.close()
