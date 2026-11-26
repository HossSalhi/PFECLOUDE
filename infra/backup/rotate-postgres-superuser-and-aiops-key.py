# -*- coding: utf-8 -*-
"""HELIOS - rotation du mot de passe du SUPERUTILISATEUR PostgreSQL (CNPG, ns pfe) et suppression controlee d'AIOPS_SERVICE_API_KEY.
Sur un noeud avec kubectl. Aucune valeur affichee. Retour arriere automatique. Usage : python3 rotate-postgres-superuser-and-aiops-key.py [--dry-run]"""
import json, subprocess, base64, hashlib, hmac, os, secrets, sys, time
NS='pfe'; DRY='--dry-run' in sys.argv
def k(*a, inp=None, check=True):
    r=subprocess.run(['kubectl',*a],input=inp,capture_output=True,text=True)
    if check and r.returncode: raise RuntimeError(f"kubectl {' '.join(a[:4])} : {r.stderr.strip()[:200]}")
    return r.stdout
def log(m): print(time.strftime('%H:%M:%S'),m,flush=True)
def scram(pw,it=4096):
    salt=os.urandom(16); sp=hashlib.pbkdf2_hmac('sha256',pw.encode(),salt,it); ck=hmac.new(sp,b'Client Key','sha256').digest(); sk=hmac.new(sp,b'Server Key','sha256').digest(); b=lambda x: base64.b64encode(x).decode()
    return f'SCRAM-SHA-256${it}:{b(salt)}${b(hashlib.sha256(ck).digest())}:{b(sk)}'
def sget(ns,n,key): return base64.b64decode(json.loads(k('-n',ns,'get','secret',n,'-o','json'))['data'][key]).decode()
def spatch(ns,n,key,val): k('-n',ns,'patch','secret',n,'--type','merge','-p',json.dumps({'data':{key:base64.b64encode(val.encode()).decode()}}))
cl=json.loads(k('-n',NS,'get','cluster','postgres','-o','json')); primary=cl['status']['currentPrimary']
assert cl['status'].get('readyInstances')==2, 'CNPG non 2/2 : arret'
# consommateurs du Secret superutilisateur (toutes les ressources de tous les namespaces)
allj=k('get','deploy,sts,ds,cronjob,job,pod','-A','-o','json'); refs=[]
for it in json.loads(allj)['items']:
    if 'postgres-superuser' in json.dumps(it.get('spec',{})) and it['metadata'].get('ownerReferences',[{}])[0].get('kind')!='Cluster' and not it['metadata']['name'].startswith('postgres-'): refs.append(it['kind']+'/'+it['metadata']['name'])
log(f'consommateurs externes de postgres-superuser : {refs or "aucun"}'); assert not refs, 'consommateur(s) detecte(s) : rotation refusee'
su_user=sget(NS,'postgres-superuser','username'); old=sget(NS,'postgres-superuser','password')
def psql(sql): return k('-n',NS,'exec','-i',primary,'-c','postgres','--','psql','-U','postgres','-d','postgres','-X','-q','-v','ON_ERROR_STOP=1',inp=sql)
def login_ok(pw):
    r=subprocess.run(['kubectl','-n',NS,'exec','-i',primary,'-c','postgres','--','sh','-c',f'read PGPASSWORD; export PGPASSWORD; psql -h postgres-rw -U {su_user} -d postgres -Atc "select 1"'],input=pw+'\n',capture_output=True,text=True)
    return r.returncode==0 and r.stdout.strip()=='1'
log(f'pre-controles : primaire={primary}, ancien mot de passe longueur {len(old)}, connexion TCP avec l ancien : {login_ok(old)}')
aiops_present='AIOPS_SERVICE_API_KEY' in json.loads(k('-n',NS,'get','secret','backend-secret','-o','json'))['data']
wl=k('-n',NS,'get','deploy,cronjob,sts','-o','json'); aiops_refs=[it['kind']+'/'+it['metadata']['name'] for it in json.loads(wl)['items'] if 'AIOPS_SERVICE_API_KEY' in json.dumps(it['spec'])]
log(f'AIOPS_SERVICE_API_KEY presente dans backend-secret : {aiops_present} ; reference explicite par une charge : {aiops_refs or "aucune"}')
if DRY: sys.exit(0)
d=time.strftime('%Y%m%d')
for n,src in (('postgres-superuser-avant-rotation-'+d,'postgres-superuser'),('backend-secret-avant-aiops-'+d,'backend-secret')):
    j=json.loads(k('-n',NS,'get','secret',src,'-o','json')); j['metadata']={'name':n,'namespace':NS}; k('-n',NS,'apply','-f','-',inp=json.dumps(j))
log('copies de retour arriere creees (postgres-superuser-avant-rotation-*, backend-secret-avant-aiops-*)')
new=secrets.token_urlsafe(30)
try:
    spatch(NS,'postgres-superuser','password',new); psql(f"ALTER ROLE {su_user} PASSWORD '{scram(new)}';"); log('Secret mis a jour + ALTER ROLE applique')
    time.sleep(20)  # laisse CNPG reconcilier : il doit converger vers le meme mot de passe
    assert login_ok(new), 'nouveau mot de passe refuse'; assert not login_ok(old), 'ancien mot de passe encore accepte'; log('verifie : nouveau accepte, ancien refuse (apres reconciliation CNPG)')
    r=json.loads(k('-n',NS,'get','cluster','postgres','-o','json'))['status']; assert r.get('readyInstances')==2, 'CNPG non 2/2 apres rotation'
except Exception as e:
    log('RETOUR ARRIERE : '+str(e)[:160]); spatch(NS,'postgres-superuser','password',old)
    try: psql(f"ALTER ROLE {su_user} PASSWORD '{scram(old)}';")
    except Exception as e2: log('ALTER retour : '+str(e2)[:100])
    raise SystemExit(1)
if aiops_present and not aiops_refs:
    k('-n',NS,'patch','secret','backend-secret','--type','json','-p','[{"op":"remove","path":"/data/AIOPS_SERVICE_API_KEY"}]'); log('AIOPS_SERVICE_API_KEY supprimee de backend-secret (copie dans backend-secret-avant-aiops-*) ; les pods en cours ne sont pas redemarres')
log('termine')