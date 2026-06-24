#!/usr/bin/env python3
"""Affiche l'etat des incidents par app/type/status (PRIMARY postgres-1)."""
import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
def sql(q):
    cmd=f"kubectl exec -n pfe postgres-1 -c postgres -- psql -t -A -F'|' postgresql://appuser:postgres@localhost/pfe_db -c \"{q}\""
    _,o,e=c.exec_command(cmd,timeout=60); return (o.read().decode(errors='replace')+e.read().decode(errors='replace')).strip()
rows = sql("SELECT app, incident_type, status, title FROM incident ORDER BY app, created_at;")
print(rows if rows else "(aucun incident)")
print("---")
print("types distincts:", sql("SELECT count(DISTINCT incident_type) FROM incident;"))
print("par status:", sql("SELECT status, count(*) FROM incident GROUP BY status ORDER BY status;").replace("\n"," ; "))
c.close()
