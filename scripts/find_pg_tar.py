#!/usr/bin/env python3
import paramiko
k=paramiko.Ed25519Key.from_private_key_file('C:/Users/houss/.ssh/id_ed25519')
for name,ip in [('master','192.168.148.153'),('worker1','192.168.148.158'),('worker2','192.168.148.155')]:
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip,username='kube',pkey=k,timeout=15,allow_agent=False,look_for_keys=False)
    cmd=("echo kube | sudo -S sh -c 'find /opt /root /home /tmp /var/tmp -maxdepth 3 -name \"*.tar\" 2>/dev/null | "
         "xargs -r ls -la 2>/dev/null' | grep -iE 'postgr|pg|cnpg|image|16' ")
    _,o,_=c.exec_command(cmd,timeout=90)
    print(f"=== {name} ({ip}) ===")
    print(o.read().decode(errors='replace').strip() or "(aucun tar postgres)")
    # also: can this node reach ghcr.io ?
    _,o2,_=c.exec_command("timeout 5 getent hosts ghcr.io 2>/dev/null; echo rc=$?",timeout=15)
    print("ghcr.io DNS:", o2.read().decode(errors='replace').strip())
    c.close()
