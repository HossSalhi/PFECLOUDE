import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace"))
for host,label in [("192.168.148.153","master"),("192.168.148.158","w1"),("192.168.148.155","w2")]:
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(host,username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
        _,o,_=c.exec_command("echo 'kube' | sudo -S ctr -n k8s.io images list -q 2>/dev/null | grep -iE 'metrics-server|metrics_server'",timeout=30)
        r=o.read().decode(errors="replace").strip()
        out(f"[{label}] {r if r else '(aucune image metrics-server)'}")
        c.close()
    except Exception as e:
        out(f"[{label}] ERR {e}")
