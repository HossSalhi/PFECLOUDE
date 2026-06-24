import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def cli(h="192.168.148.153"):
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(h,username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,t=140):
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()

c=cli()
out("[1] Exclure les 3 apps demo cassees (label helios.io/monitor=false)")
out(run(c,"kubectl label deployment incident-crashloop incident-imagepull incident-pending helios.io/monitor=false -n pfe --overwrite"))

out("\n[2] Appliquer le configmap (DISCOVERY_ENABLED=true)")
sftp=c.open_sftp()
sftp.put("C:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/infra/k8s/backend-configmap.yaml","/tmp/backend-configmap.yaml")
sftp.close()
out(run(c,"kubectl apply -f /tmp/backend-configmap.yaml -n pfe"))

out("\n[3] Restart backend pour prendre le nouveau flag")
out(run(c,"kubectl rollout restart deployment/backend -n pfe"))
out(run(c,"kubectl rollout status deployment/backend -n pfe --timeout=130s"))
c.close()
out("\nOK - attends ~2 min (leader election) puis la decouverte tournera. podinfo apparaitra dans Deployments.")
