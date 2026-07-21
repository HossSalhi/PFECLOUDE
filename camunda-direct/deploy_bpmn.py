# -*- coding: utf-8 -*-
"""Deploie la v9 (garde-fou namespaces systeme) apres verification de la portee RBAC."""
import paramiko, sys, os
def out(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8","replace")); sys.stdout.flush()
def sec(t): out("\n" + "="*74 + "\n  " + t + "\n" + "="*74)
SP = os.path.dirname(__file__)

k = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
m = paramiko.SSHClient(); m.set_missing_host_key_policy(paramiko.AutoAddPolicy())
m.connect("192.168.148.153", username="kube", pkey=k, timeout=20, allow_agent=False, look_for_keys=False)
def rm(c,t=90):
    _,o,e=m.exec_command(c,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
a = paramiko.SSHClient(); a.set_missing_host_key_policy(paramiko.AutoAddPolicy())
a.connect("192.168.148.161", username="kube", password="kube", timeout=20, allow_agent=False, look_for_keys=False)
def ra(c,t=120):
    _,o,e=a.exec_command(c,timeout=t)
    return "\n".join(l for l in (o.read().decode(errors="replace")+e.read().decode(errors="replace")).splitlines() if "password for" not in l).strip()

sec("PORTEE RBAC de la ServiceAccount camunda-direct")
out("   ClusterRoleBinding : " + (rm("kubectl get clusterrolebinding -o json | grep -c camunda-direct") ))
out("   bindings existants :")
out(rm("kubectl get clusterrolebinding,rolebinding -A -o json | python3 -c \"import sys,json;d=json.load(sys.stdin);"
       "print('\\n'.join('     %s %s -> %s (ns=%s)'%(i['kind'],i['metadata']['name'],i['roleRef']['name'],i['metadata'].get('namespace','-')) "
       "for i in d['items'] if any('camunda' in str(s.get('name','')) for s in i.get('subjects',[]) or [])))\" 2>/dev/null") or "     (aucun)")
out("\n   peut-il patcher un deploiement HORS pfe ? (namespace default)")
out("   " + rm("kubectl auth can-i patch deployments --namespace=default --as=system:serviceaccount:pfe:camunda-direct"))
out("   peut-il patcher dans kube-system ?")
out("   " + rm("kubectl auth can-i patch deployments --namespace=kube-system --as=system:serviceaccount:pfe:camunda-direct"))

sec("DEPLOIEMENT v9")
sftp = a.open_sftp(); sftp.put(os.path.join(SP, "camunda-full-cycle.bpmn"), "/tmp/v9.bpmn"); sftp.close()
res = ra("curl -s --max-time 40 -X POST http://localhost:8080/v2/deployments -F 'resources=@/tmp/v9.bpmn;type=text/xml'")
if '"processDefinitionVersion"' in res:
    out("   >>> v9 DEPLOYEE (version %s)" % res.split('"processDefinitionVersion":')[1].split(',')[0])
else:
    out("   >>> ECHEC :"); out("   " + res[:600])
m.close(); a.close()
