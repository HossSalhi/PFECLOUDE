import paramiko, sys, re
KEY="C:/Users/houss/.ssh/id_ed25519"; AI="192.168.148.161"
SH="C:/Users/houss/AppData/Local/Temp/claude/c--Users-houss-IdeaProjects-PFE-Orange/7a498a6f-f64e-405d-a014-1f262520c572/scratchpad/probe.sh"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(AI,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
sftp=c.open_sftp(); sftp.put(SH,"/tmp/probe.sh"); sftp.close()
_,o,e=c.exec_command("echo 'kube' | sudo -S bash /tmp/probe.sh 2>/dev/null",timeout=60)
resp=(o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
out(resp[:2000])
out("\n[extrait]")
for key in ("incident_type","recommended_action","final_executable_action","incident","severity","confidence"):
    m=re.search(r'"'+key+r'"\s*:\s*("?[^",}]+"?)',resp)
    if m: out(f"  {key} = {m.group(1)}")
c.close()
out("\n=== ATTENDU : incident_type=POD_CRASH_LOOP , final_executable_action=ROLLBACK_DEPLOYMENT ===")
