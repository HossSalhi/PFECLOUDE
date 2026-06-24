#!/usr/bin/env python3
"""Copy the CNPG operator image from master (where it's cached) to the workers (air-gap).
Fixes postgres init container ErrImagePull after a full VM reboot."""
import paramiko, os

KEY = "C:/Users/houss/.ssh/id_ed25519"
USER = "kube"; SUDO = "kube"
MASTER = "192.168.148.153"
WORKERS = ["192.168.148.158", "192.168.148.155"]
IMG = "ghcr.io/cloudnative-pg/postgresql:16"
LOCAL = "C:/Users/houss/IdeaProjects/PFE-Orange/cnpg-postgresql16.tar"

def cli(host):
    k = paramiko.Ed25519Key.from_private_key_file(KEY)
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=USER, pkey=k, timeout=15, allow_agent=False, look_for_keys=False)
    return c

def run(c, cmd, t=180):
    cmd = f"echo '{SUDO}' | sudo -S {cmd}"
    _, o, e = c.exec_command(cmd, timeout=t)
    out = o.read().decode(errors="replace").strip(); err = e.read().decode(errors="replace").strip()
    rc = o.channel.recv_exit_status()
    if out: print("   ", out[:300])
    if err and "[sudo]" not in err: print("    ERR:", err[:300])
    return rc

print("[1] export image sur master")
c = cli(MASTER)
print("  rc=", run(c, f"ctr -n k8s.io images export /tmp/cnpg.tar {IMG}"))
sftp = c.open_sftp(); sftp.get("/tmp/cnpg.tar", LOCAL); sftp.close(); c.close()
print(f"  telecharge -> {LOCAL} ({round(os.path.getsize(LOCAL)/1e6,1)} MB)")

for w in WORKERS:
    print(f"[2] import sur worker {w}")
    c = cli(w)
    s = c.open_sftp(); s.put(LOCAL, "/tmp/cnpg.tar"); s.close()
    print("  rc=", run(c, "ctr -n k8s.io images import /tmp/cnpg.tar"))
    c.close()
print("DONE")
