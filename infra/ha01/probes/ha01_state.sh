#!/usr/bin/env bash
# HA-01 — instantané d'état LECTURE SEULE (pour les GATES et avant/après chaque test).
# Usage : ./ha01_state.sh <étiquette> > results/<date>/state-<étiquette>.txt
set -uo pipefail
LABEL="${1:-snap}"
echo "##### HA-01 état '$LABEL' — $(date -u +%FT%TZ)"
echo; echo "##### Hôte Windows"
powershell.exe -NoProfile -Command "\$m=Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory; 'RAM disponible: ' + \$m.AvailableMBytes + ' Mo'; & 'C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe' list" | tr -d '\r'
VIA=""
for ip in 155 158 153; do ssh -o BatchMode=yes -o ConnectTimeout=6 kube@192.168.148.$ip 'kubectl get --raw=/readyz --request-timeout=6s' >/dev/null 2>&1 && { VIA=$ip; break; }; done
echo; echo "##### kubectl via .$VIA"
[ -n "$VIA" ] || { echo "AUCUN apiserver joignable"; exit 2; }
ssh -o BatchMode=yes -o ConnectTimeout=10 kube@192.168.148.$VIA 'bash -s' <<'EOF'
export KUBECONFIG=$HOME/.kube/config
R="--request-timeout=30s"
echo "--- nodes"; kubectl get nodes -o wide $R
echo "--- taints"; kubectl get nodes -o custom-columns=NAME:.metadata.name,UNSCHED:.spec.unschedulable,TAINTS:.spec.taints $R
echo "--- pods non sains (tous ns)"; kubectl get pods -A --no-headers $R | awk '$4!="Running" && $4!="Completed"{print}' | head -30
echo "--- pods pfe / ingress / cnpg / traefik (placement)"; kubectl get pods -A -o wide $R | awk 'NR==1 || /^(pfe|ingress-nginx|cnpg-system|traefik) /' | cut -c1-210
echo "--- CNPG"; kubectl -n pfe get cluster.postgresql.cnpg.io postgres $R -o custom-columns=NAME:.metadata.name,PHASE:.status.phase,PRIMARY:.status.currentPrimary,READY:.status.readyInstances; kubectl -n pfe get pods -l cnpg.io/cluster=postgres -L cnpg.io/instanceRole -o wide $R | cut -c1-200
echo "--- PDB / HPA"; kubectl get pdb -A $R; kubectl -n pfe get hpa $R
echo "--- ressources reservees (requests) par noeud"; kubectl describe nodes $R | grep -E "^Name:|Allocated resources|  memory  |  cpu  " | paste - - - - | sed 's/  */ /g' | cut -c1-200
echo "--- kubectl top nodes"; kubectl top nodes $R 2>&1 | head -5
echo "--- etcd"
sudo -n env ETCDCTL_API=3 etcdctl --endpoints=https://192.168.148.153:2379,https://192.168.148.155:2379,https://192.168.148.158:2379,https://192.168.148.160:2379,https://192.168.148.162:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key --command-timeout=8s endpoint status -w table 2>&1 | head -12
sudo -n env ETCDCTL_API=3 etcdctl --endpoints=https://192.168.148.153:2379,https://192.168.148.155:2379,https://192.168.148.158:2379,https://192.168.148.160:2379,https://192.168.148.162:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key --command-timeout=8s endpoint health -w table 2>&1 | head -10
EOF
echo; echo "##### VMs : mémoire / charge / pression CPU"
for ip in 153 155 158 160 161 162; do printf ".%s  " $ip; ssh -o BatchMode=yes -o ConnectTimeout=8 kube@192.168.148.$ip 'printf "load=%s  MemAvailable=%s Mo  " "$(cut -d" " -f1-3 /proc/loadavg)" "$(awk "/MemAvailable/{print int(\$2/1024)}" /proc/meminfo)"; head -1 /proc/pressure/cpu | cut -d" " -f1-3' 2>&1; done
echo; echo "##### HAProxy (serveurs)"
for ip in 160 162; do echo "-- .$ip"; curl -s -m 6 "http://192.168.148.$ip:8404/;csv" | python -I -c '
import sys,csv
rd=csv.reader(sys.stdin); hdr=next(rd); hdr[0]=hdr[0].lstrip("# "); ix={h:i for i,h in enumerate(hdr)}
for r in rd:
    if len(r)>=len(hdr) and r[ix["svname"]] not in ("FRONTEND","BACKEND"):
        print("   %-14s %-9s %-4s lastchg=%ss chkfail=%s"%(r[ix["pxname"]],r[ix["svname"]],r[ix["status"]],r[ix["lastchg"]],r[ix["chkfail"]]))
' 2>&1; done
echo; echo "##### DNS (dnsmasq .160 / .162) et Windows"
for ip in 160 162; do for n in helios.orange.tn api.helios.orange.tn; do printf ".%s %s -> " $ip $n; ssh -o BatchMode=yes kube@192.168.148.$ip "dig +short +time=2 +tries=1 @192.168.148.$ip $n | sort | tr '\n' ' '"; echo; done; done
powershell.exe -NoProfile -Command "foreach(\$n in 'helios.orange.tn','api.helios.orange.tn'){ \$n + ' -> ' + (([System.Net.Dns]::GetHostAddresses(\$n) | % { \$_.IPAddressToString }) -join ' ') }" | tr -d '\r'
echo; echo "##### Bridge HELIOS (.161)"
ssh -o BatchMode=yes kube@192.168.148.161 'curl -s -m 5 http://127.0.0.1:8099/sante; echo' 2>&1
