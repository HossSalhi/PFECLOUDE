#!/usr/bin/env python3
"""Find which node has ghcr.io/cloudnative-pg/postgresql:16 and copy it to the others (air-gap)."""
import paramiko, os
KEY="C:/Users/houss/.ssh/id_ed25519"; USER="kube"; SUDO="kube"
IMG="ghcr.io/cloudnative-pg/postgresql:16"
LOCAL="C:/Users/houss/IdeaProjects/PFE-Orange/pg16.tar"
NODES={"master":"192.168.148.153","worker1":"192.168.148.158","worker2":"192.168.148.155"}

def cli(h):
    k=paramiko.Ed25519Key.from_private_key_file(KEY)
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(h,username=USER,pkey=k,timeout=15,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,t=180):
    _,o,e=c.exec_command(f"echo '{SUDO}' | sudo -S {cmd}",timeout=t)
    out=o.read().decode(errors="replace"); err=e.read().decode(errors="replace")
    return o.channel.recv_exit_status(), out, err

# 1) find a node that HAS the image
src=None
for name,ip in NODES.items():
    c=cli(ip); rc,out,err=run(c,f"ctr -n k8s.io images ls -q | grep -F '{IMG}'"); c.close()
    has = IMG in out
    print(f"{name} ({ip}): {'HAS' if has else 'no'}")
    if has and src is None: src=(name,ip)

if not src:
    print("AUCUN noeud n'a l'image -> il faut un tar externe"); raise SystemExit(1)

print(f"Source = {src[0]}")
c=cli(src[1]); print("export:", run(c,f"ctr -n k8s.io images export /tmp/pg16.tar {IMG}")[0])
s=c.open_sftp(); s.get("/tmp/pg16.tar",LOCAL); s.close(); c.close()
print(f"  -> {LOCAL} ({round(os.path.getsize(LOCAL)/1e6,1)} MB)")

for name,ip in NODES.items():
    if ip==src[1]: continue
    c=cli(ip); s=c.open_sftp(); s.put(LOCAL,"/tmp/pg16.tar"); s.close()
    rc,out,err=run(c,"ctr -n k8s.io images import /tmp/pg16.tar")
    print(f"import {name}: rc={rc} {out.strip()[:120]}{err.strip()[:120]}"); c.close()
print("DONE")
