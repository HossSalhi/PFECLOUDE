"""Copie les images control-plane du master vers w1/w2 (air-gap registre).
master -> export tar -> local -> w1/w2 -> import."""
import paramiko, shlex, os, tempfile

KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"; WORKERS=["192.168.148.158","192.168.148.155"]

def conn(ip):
    k=paramiko.Ed25519Key.from_private_key_file(KEY)
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip,username="kube",pkey=k,timeout=15,allow_agent=False,look_for_keys=False); return c
def root(c,chain,t=300):
    i,o,e=c.exec_command("sudo -S -p '' bash -c "+shlex.quote(chain),timeout=t); i.write("kube\n"); i.flush()
    return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()

m=conn(MASTER)
# refs control-plane (filtre les images du control-plane + pause + coredns)
refs=root(m, "crictl images -o json | python3 -c \"import sys,json;d=json.load(sys.stdin);[print(i['repoTags'][0]) for i in d['images'] if i.get('repoTags') and any(x in i['repoTags'][0] for x in ['kube-apiserver','kube-controller-manager','kube-scheduler','etcd','pause','coredns'])]\"")
refs=[r for r in refs.split() if r.startswith("registry.k8s.io")]
print("images a copier:", refs)
print("== export sur master ==")
print(root(m, "ctr -n k8s.io images export /tmp/cp-images.tar "+" ".join(refs)+" && ls -la /tmp/cp-images.tar"))
local=os.path.join(tempfile.gettempdir(),"cp-images.tar")
sm=m.open_sftp(); sm.get("/tmp/cp-images.tar", local); sm.close()
print("recupere local:", os.path.getsize(local)//1024//1024, "MB")
m.close()
for ip in WORKERS:
    print(f"== import sur {ip} ==")
    w=conn(ip)
    sw=w.open_sftp(); sw.put(local,"/tmp/cp-images.tar"); sw.close()
    print(root(w, "ctr -n k8s.io images import /tmp/cp-images.tar 2>&1 | tail -2"))
    w.close()
print("DONE images copiees")
