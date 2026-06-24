#!/usr/bin/env python3
import paramiko
k=paramiko.Ed25519Key.from_private_key_file('C:/Users/houss/.ssh/id_ed25519')
TAR="/home/kube/cnpg-postgresql-16.tar"
for name,ip in [('master','192.168.148.153'),('worker1','192.168.148.158'),('worker2','192.168.148.155')]:
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip,username='kube',pkey=k,timeout=15,allow_agent=False,look_for_keys=False)
    _,o,e=c.exec_command(f"echo kube | sudo -S ctr -n k8s.io images import {TAR}",timeout=240)
    out=o.read().decode(errors='replace').strip(); err=e.read().decode(errors='replace').strip()
    print(f"=== {name} ({ip}) rc={o.channel.recv_exit_status()} ===")
    print(out[:300]);
    if err and '[sudo]' not in err: print("ERR:",err[:200])
    c.close()
print("DONE")
