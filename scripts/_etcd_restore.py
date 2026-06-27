"""Restauration etcd du master (DB bbolt corrompue). 2 phases.
  phase1 = extraire etcdutl de l'image + verifier la sauvegarde (SUR, lecture seule)
  phase2 = stop static pods + backup data corrompue + restore snapshot + restart (RECUPERATION)
Reversible : la data corrompue est deplacee (pas supprimee) en /var/lib/etcd.corrupt-<ts>."""
import paramiko, shlex, sys, time, socket

KEY = "C:/Users/houss/.ssh/id_ed25519"; MASTER = "192.168.148.153"
SNAP = "/opt/etcd-backups/etcd-20260623-180001.db"
IMG = "registry.k8s.io/etcd:3.5.16-0"

def conn():
    k = paramiko.Ed25519Key.from_private_key_file(KEY)
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(MASTER, username="kube", pkey=k, timeout=10, allow_agent=False, look_for_keys=False)
    return c

def root(c, chain, t=200):
    cmd = "sudo -S -p '' bash -c " + shlex.quote(chain)
    i, o, e = c.exec_command(cmd, timeout=t); i.write("kube\n"); i.flush()
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()

def api_up():
    try:
        with socket.create_connection((MASTER, 6443), timeout=4):
            return True
    except Exception:
        return False

phase = sys.argv[1] if len(sys.argv) > 1 else "phase1"
c = conn()

if phase == "phase1":
    print("== copie etcdutl/etcdctl depuis l'image ==")
    print(root(c, f"mkdir -p /mnt/etcdimg && ctr -n k8s.io images mount {IMG} /mnt/etcdimg && "
                  f"cp /mnt/etcdimg/usr/local/bin/etcdutl /usr/local/bin/ && "
                  f"cp /mnt/etcdimg/usr/local/bin/etcdctl /usr/local/bin/ && "
                  f"ctr -n k8s.io images unmount /mnt/etcdimg && echo COPIE_OK"))
    print("== etcdutl version =="); print(root(c, "/usr/local/bin/etcdutl version | head -2"))
    print("== snapshot status (23 juin) =="); print(root(c, f"/usr/local/bin/etcdutl snapshot status {SNAP} -w table"))

elif phase == "phase2":
    ts = str(int(time.time()))
    print("== 1. stop static pods etcd + apiserver ==")
    print(root(c, "mkdir -p /etc/kubernetes/manifests-off && "
                  "mv /etc/kubernetes/manifests/etcd.yaml /etc/kubernetes/manifests/kube-apiserver.yaml "
                  "/etc/kubernetes/manifests-off/ && echo MOVED"))
    print("   attente arret des conteneurs (25s)..."); time.sleep(25)
    print("== 2. backup de la data corrompue ==")
    print(root(c, f"mv /var/lib/etcd /var/lib/etcd.corrupt-{ts} && echo BACKED_UP_{ts}"))
    print("== 3. restore du snapshot 23 juin (etcdctl) ==")
    print(root(c, f"ETCDCTL_API=3 /usr/local/bin/etcdctl snapshot restore {SNAP} "
                  f"--name k8s-master "
                  f"--initial-cluster k8s-master=https://192.168.148.153:2380 "
                  f"--initial-advertise-peer-urls https://192.168.148.153:2380 "
                  f"--data-dir /var/lib/etcd && chown -R root:root /var/lib/etcd && echo RESTORED"))
    print("== 4. verif data dir ==");  print(root(c, "ls -la /var/lib/etcd/member"))
    print("== 5. remettre les static pods ==")
    print(root(c, "mv /etc/kubernetes/manifests-off/etcd.yaml /etc/kubernetes/manifests-off/kube-apiserver.yaml "
                  "/etc/kubernetes/manifests/ && echo MANIFESTS_BACK"))
    print("== 6. attente API stable (5 checks consecutifs) ==")
    cons = 0
    for i in range(35):
        time.sleep(12)
        if api_up():
            cons += 1; print(f"  API UP [{cons}/5] ({12*(i+1)}s)")
        else:
            cons = 0; print(f"  API down ({12*(i+1)}s)")
        if cons >= 5:
            print("=> API STABLE apres restauration etcd."); break

c.close()
