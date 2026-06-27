"""Configure keepalived (VIP control-plane) sur les 3 noeuds. VIP 192.168.148.150,
priorites master=150 > w1=100 > w2=90, health-check apiserver local. Non destructif :
la VIP va sur le noeud de plus haute priorite dont l'apiserver repond (= master au depart).
NOPREEMPT : les 3 sont en BACKUP (nopreempt n'est valide qu'en BACKUP) -> celui qui tient la
VIP la garde ; un noeud de plus haute priorite qui REVIENT ne la vole pas (pas de trou API au
retour du master). Au demarrage a froid, le plus haute priorite vivant (master 150) prend la VIP."""
import paramiko, shlex, socket, time

KEY="C:/Users/houss/.ssh/id_ed25519"
VIP="192.168.148.150"; IFACE="ens33"; VRID="51"; AUTH="helios42"
NODES=[("192.168.148.153","BACKUP","150"),("192.168.148.158","BACKUP","100"),("192.168.148.155","BACKUP","90")]

CHECK = "#!/bin/sh\ncurl -sfk --max-time 2 https://127.0.0.1:6443/healthz -o /dev/null\n"
def conf(state, prio):
    return f"""vrrp_script chk_apiserver {{
    script "/etc/keepalived/check_apiserver.sh"
    interval 3
    weight -2
    fall 3
    rise 2
}}
vrrp_instance VI_1 {{
    state {state}
    interface {IFACE}
    virtual_router_id {VRID}
    priority {prio}
    nopreempt
    authentication {{ auth_type PASS; auth_pass {AUTH} }}
    virtual_ipaddress {{ {VIP}/24 }}
    track_script {{ chk_apiserver }}
}}
"""

def conn(ip):
    k=paramiko.Ed25519Key.from_private_key_file(KEY)
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip,username="kube",pkey=k,timeout=15,allow_agent=False,look_for_keys=False); return c
def root(c,chain,t=60):
    i,o,e=c.exec_command("sudo -S -p '' bash -c "+shlex.quote(chain),timeout=t); i.write("kube\n"); i.flush()
    return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
def put_root(c,content,path):
    # ecrit via cat heredoc en root
    return root(c, "cat > "+path+" <<'EOFKA'\n"+content+"\nEOFKA")

for ip,state,prio in NODES:
    print(f"=== {ip} ({state} prio={prio}) ===")
    try:
        c=conn(ip)
        put_root(c, CHECK, "/etc/keepalived/check_apiserver.sh")
        root(c, "chmod +x /etc/keepalived/check_apiserver.sh")
        put_root(c, conf(state,prio), "/etc/keepalived/keepalived.conf")
        # reload (SIGHUP) = non-disruptif (ne lache pas la VIP) ; fallback restart si pas lance
        print(" enable+reload:", root(c, "systemctl enable keepalived >/dev/null 2>&1; "
              "(systemctl reload keepalived 2>/dev/null && echo RELOADED) || "
              "(systemctl restart keepalived && echo RESTARTED)")[:60])
        c.close()
    except Exception as e:
        print("  ERREUR:", str(e)[:60])

time.sleep(5)
print("=== VIP active sur le master ? ===")
try:
    with socket.create_connection((VIP,6443),timeout=5): print("VIP %s:6443 REPOND (via apiserver master)"%VIP)
except Exception as e:
    print("VIP pas encore joignable:", str(e)[:50])
