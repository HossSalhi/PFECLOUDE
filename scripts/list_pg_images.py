#!/usr/bin/env python3
import paramiko
k=paramiko.Ed25519Key.from_private_key_file('C:/Users/houss/.ssh/id_ed25519')
for name,ip in [('master','192.168.148.153'),('worker1','192.168.148.158'),('worker2','192.168.148.155')]:
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip,username='kube',pkey=k,timeout=15,allow_agent=False,look_for_keys=False)
    cmd="echo kube | sudo -S ctr -n k8s.io images ls -q 2>/dev/null | grep -iE 'postgres|cloudnative'"
    _,o,_=c.exec_command(cmd,timeout=60)
    print(f"=== {name} ({ip}) ===")
    print(o.read().decode(errors='replace').strip() or "(aucune)")
    c.close()
