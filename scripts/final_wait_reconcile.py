#!/usr/bin/env python3
"""Attend la resolution des incidents par HELIOS (IA -> Camunda -> kubectl -> verif), puis imprime
la table finale. AUCUNE edition de la base : on observe le VRAI resultat (RESOLVED ou RESOLUTION_FAILED)."""
import paramiko, time
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
def run(cmd,t=50):
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode('ascii','replace')+e.read().decode('ascii','replace')).strip()
def sql(q): return run(f"kubectl exec -n pfe postgres-1 -c postgres -- psql -t -A -F'|' postgresql://appuser:postgres@localhost/pfe_db -c \"{q}\"")

print("[wait] resolution par HELIOS en cours (observation seule, sans edition base)...")
for i in range(40):  # ~20 min
    time.sleep(30)
    t=int(sql("SELECT count(DISTINCT incident_type) FROM incident;") or 0)
    active=int(sql("SELECT count(*) FROM incident WHERE status IN ('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL');") or 0)
    failed=int(sql("SELECT count(*) FROM incident WHERE status='RESOLUTION_FAILED';") or 0)
    print(f"   types={t} active={active} failed={failed}")
    if t>=16 and active==0: break

print("\n=== TABLE FINALE (VRAI statut HELIOS, non maquille) ===")
print(sql("SELECT incident_type, status, app FROM incident ORDER BY incident_type, app;"))
print("distinct:", sql("SELECT count(DISTINCT incident_type) FROM incident;"),
      "| total:", sql("SELECT count(*) FROM incident;"),
      "| status:", sql("SELECT status||'x'||count(*) FROM incident GROUP BY status;").replace(chr(10),' '),
      "| GENERAL_DEGRADATION+FAILED:", sql("SELECT count(*) FROM incident WHERE incident_type='GENERAL_DEGRADATION' OR status='RESOLUTION_FAILED';"))
c.close()
print("DONE")
