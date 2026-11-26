# -*- coding: utf-8 -*-
"""HELIOS - rotation coordonnee du mot de passe PostgreSQL de l'utilisateur applicatif (CNPG, namespace pfe).
S'execute sur un noeud avec kubectl. N'affiche JAMAIS de mot de passe. Retour arriere automatique. Usage : python3 rotate-postgres-app-password.py [--dry-run]"""
import json, subprocess, base64, hashlib, hmac, os, secrets, sys, time
NS = 'pfe'; DRY = '--dry-run' in sys.argv
def k(*a, inp=None, check=True):
    r = subprocess.run(['kubectl', *a], input=inp, capture_output=True, text=True)
    if check and r.returncode: raise RuntimeError(f"kubectl {' '.join(a[:4])} : {r.stderr.strip()[:200]}")
    return r.stdout
def log(m): print(time.strftime('%H:%M:%S'), m, flush=True)
def scram(pw, it=4096):
    salt = os.urandom(16); sp = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, it)
    ck = hmac.new(sp, b'Client Key', 'sha256').digest(); sk = hmac.new(sp, b'Server Key', 'sha256').digest()
    b = lambda x: base64.b64encode(x).decode()
    return f'SCRAM-SHA-256${it}:{b(salt)}${b(hashlib.sha256(ck).digest())}:{b(sk)}'
def sget(name, key): return base64.b64decode(json.loads(k('-n', NS, 'get', 'secret', name, '-o', 'json'))['data'][key]).decode()
def spatch(name, key, val): k('-n', NS, 'patch', 'secret', name, '--type', 'merge', '-p', json.dumps({'data': {key: base64.b64encode(val.encode()).decode()}}))
cl = json.loads(k('-n', NS, 'get', 'cluster', 'postgres', '-o', 'json')); primary = cl['status']['currentPrimary']; user = sget('postgres-app', 'username')
assert cl['status'].get('readyInstances') == 2, f"CNPG non 2/2 ({cl['status'].get('readyInstances')}) : rotation refusee"
old = sget('postgres-app', 'password'); assert old == sget('backend-secret', 'SPRING_DATASOURCE_PASSWORD'), 'postgres-app et backend-secret divergent : arret'
log(f'pre-controles OK : CNPG 2/2, primaire={primary}, utilisateur={user}, secrets coherents, ancien mot de passe longueur {len(old)}')
def psql_stdin(sql): return k('-n', NS, 'exec', '-i', primary, '-c', 'postgres', '--', 'psql', '-U', 'postgres', '-d', 'pfe_db', '-X', '-q', '-v', 'ON_ERROR_STOP=1', inp=sql)
def login_ok(pw):  # test TCP+SCRAM avec le mot de passe, fourni par stdin (pas dans les arguments)
    r = subprocess.run(['kubectl', '-n', NS, 'exec', '-i', primary, '-c', 'postgres', '--', 'sh', '-c', f'read PGPASSWORD; export PGPASSWORD; psql -h postgres-rw -U {user} -d pfe_db -Atc "select 1"'], input=pw + '\n', capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == '1'
assert login_ok(old), 'connexion avec le mot de passe actuel impossible : arret'
if DRY: log('DRY-RUN : arret avant toute modification'); sys.exit(0)
pre = f'rot-pre-{int(time.time())}'; k('-n', NS, 'create', 'job', pre, '--from=cronjob/postgres-backup'); log(f'sauvegarde de securite : Job {pre}')
for _ in range(60):
    st = json.loads(k('-n', NS, 'get', 'job', pre, '-o', 'json'))['status']
    if st.get('succeeded'): break
    if st.get('failed'): raise SystemExit('sauvegarde de securite en echec : rotation annulee')
    time.sleep(5)
else: raise SystemExit('sauvegarde de securite trop longue : rotation annulee')
log('sauvegarde de securite OK')
bk = json.loads(k('-n', NS, 'get', 'secret', 'postgres-app', '-o', 'json')); bk['metadata'] = {'name': 'postgres-app-avant-rotation-' + time.strftime('%Y%m%d'), 'namespace': NS}; k('-n', NS, 'apply', '-f', '-', inp=json.dumps(bk)); log('copie de retour arriere du Secret postgres-app creee (ancien mot de passe, jamais affiche)')
new = secrets.token_urlsafe(30); done = []
def rollback(why):
    log(f'RETOUR ARRIERE : {why}')
    try: psql_stdin(f"ALTER ROLE {user} PASSWORD '{scram(old)}';")
    except Exception as e: log('ALTER ROLE retour : ' + str(e)[:120])
    spatch('postgres-app', 'password', old); spatch('backend-secret', 'SPRING_DATASOURCE_PASSWORD', old)
    k('-n', NS, 'rollout', 'restart', 'deploy/backend'); log('ancien mot de passe retabli, backend relance')
try:
    spatch('postgres-app', 'password', new); spatch('backend-secret', 'SPRING_DATASOURCE_PASSWORD', new); log('Secrets mis a jour (les pods en cours ne les voient pas)')
    psql_stdin(f"ALTER ROLE {user} PASSWORD '{scram(new)}';"); log('ALTER ROLE applique sur le primaire (verificateur SCRAM, replique via WAL)')
    assert login_ok(new), 'connexion avec le nouveau mot de passe impossible'; assert not login_ok(old), 'ancien mot de passe encore accepte'
    log('verifie : nouveau mot de passe accepte, ancien refuse')
    k('-n', NS, 'rollout', 'restart', 'deploy/backend'); r = subprocess.run(['kubectl', '-n', NS, 'rollout', 'status', 'deploy/backend', '--timeout=420s'], capture_output=True, text=True); log('backend : ' + r.stdout.strip().splitlines()[-1])
    assert r.returncode == 0, 'rollout backend echoue'
    for d in ('ha01-canary', 'ha01-canary-w1', 'ha01-canary-w2'):
        if k('-n', NS, 'get', 'deploy', d, '--ignore-not-found', '-o', 'name', check=False).strip(): k('-n', NS, 'rollout', 'restart', f'deploy/{d}')
except Exception as e:
    rollback(str(e)[:200]); raise SystemExit(1)
log('rotation terminee ; controles de fin faits par le script appelant')