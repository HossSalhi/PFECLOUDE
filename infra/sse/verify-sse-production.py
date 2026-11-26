# -*- coding: utf-8 -*-
"""Verification SSE EN PRODUCTION (a lancer sur un noeud du cluster avec kubectl) avec VOS identifiants :
  HELIOS_TEST_USER=... HELIOS_TEST_PASSWORD=... python3 verify-sse-production.py
Prouve : cookie helios_sse HttpOnly/SameSite=Strict/Secure via l ingress, ticket a usage unique, flux 200, et diffusion INTER-PODS (un abonne par pod,
evenement injecte dans l outbox sse_event puis supprime). Une seule tentative de connexion (pas de verrouillage). Aucun secret affiche."""
import json,subprocess,base64,re,time,os,tempfile
def sh(*a,inp=None): return subprocess.run(list(a),input=inp,capture_output=True,text=True)
def kget(ns,n,key): return base64.b64decode(json.loads(sh('kubectl','-n',ns,'get','secret',n,'-o','json').stdout)['data'][key]).decode()
user=os.environ.get('HELIOS_TEST_USER'); pw=os.environ.get('HELIOS_TEST_PASSWORD')
if not user or not pw:
    print('BLOQUE - COMPTE DE TEST AUTORISE MANQUANT : definir HELIOS_TEST_USER et HELIOS_TEST_PASSWORD (et, pour l isolation, HELIOS_TEST_USER2 / HELIOS_TEST_PASSWORD2). Aucune requete envoyee.'); raise SystemExit(2)
ing='https://helios.orange.tn'; res='helios.orange.tn:443:10.97.134.39'
out=[]
def chk(n,ok,x=''): out.append((n,ok)); print(('OK    ' if ok else 'ECART ')+n+(' '+x if x else ''))
# 1) connexion via l'ingress HTTPS (UNE tentative)
r=sh('curl','-sk','-m','15','--resolve',res,'-X','POST','-H','Content-Type: application/json','-d',json.dumps({'username':user,'password':pw}),'-w','\n%{http_code}',ing+'/api/auth/login')
body,code=r.stdout.rsplit('\n',1); print('login via ingress : HTTP',code)
if code!='200': print('ARRET : connexion non aboutie (aucune nouvelle tentative pour éviter un verrouillage)'); raise SystemExit
j=json.loads(body); jwt=next((j.get(k) for k in ('accessToken','token','jwt') if j.get(k)),None)
if not jwt: print('ARRET : pas de jeton dans la réponse (2FA ?), clés =',sorted(j.keys())); raise SystemExit
H=['-H','Authorization: Bearer '+jwt]
# 2) ticket via ingress : attributs du cookie en production
r=sh('curl','-sk','-m','15','--resolve',res,'-i','-X','POST',*H,'-H','Content-Type: application/json','-d','{}',ing+'/api/events/ticket')
hdr=r.stdout.split('\r\n\r\n')[0]; sc=re.search(r'(?im)^set-cookie:\s*(helios_sse=[^\r\n]*)',hdr); sc=sc.group(1) if sc else ''
chk('ticket via ingress HTTPS : 200',' 200' in hdr.split('\r\n')[0])
chk('cookie : HttpOnly',bool(re.search(r'HttpOnly',sc))); chk('cookie : SameSite=Strict','SameSite=Strict' in sc); chk('cookie : Secure (chaîne ingress → nginx → backend)',bool(re.search(r';\s*Secure',sc))); chk('cookie : Path limité au flux','Path=/api/events/stream' in sc)
ticket=re.search(r'helios_sse=([^;]+)',sc).group(1) if sc else ''
chk('le ticket n\'est pas dans le corps de la réponse',ticket not in r.stdout.split('\r\n\r\n',1)[-1])
# 3) flux via ingress avec ce ticket
f=tempfile.mktemp(); p=subprocess.Popen(['curl','-sk','-N','-m','6','--resolve',res,'-H','Cookie: helios_sse='+ticket,ing+'/api/events/stream','-o',f]); p.wait()
chk('flux SSE via ingress avec ticket : événement connected reçu','connected' in open(f).read() if os.path.exists(f) else False)
r2=sh('curl','-sk','-m','8','--resolve',res,'-o','/dev/null','-w','%{http_code}','-H','Cookie: helios_sse='+ticket,ing+'/api/events/stream'); chk('ticket déjà consommé : refusé (401)',r2.stdout=='401',r2.stdout)
# 4) diffusion inter-pods : 2 abonnés, un par pod (adresses des pods), évènement de diagnostic injecté dans l'outbox
pods=[l.split() for l in sh('kubectl','-n','pfe','get','pods','-l','app=backend','-o','jsonpath={range .items[*]}{.metadata.name} {.status.podIP}{"\\n"}{end}').stdout.strip().split('\n')]
print('pods :',[p[0] for p in pods])
procs=[]; files=[]
for name,ip in pods:
    t=sh('curl','-s','-m','10','-i','-X','POST',*H,'-H','Content-Type: application/json','-d','{}',f'http://{ip}:8080/api/events/ticket').stdout
    tk=re.search(r'helios_sse=([^;]+)',t).group(1); fl=tempfile.mktemp(); files.append((name,fl))
    procs.append(subprocess.Popen(['curl','-s','-N','-m','12','-H','Cookie: helios_sse='+tk,f'http://{ip}:8080/api/events/stream','-o',fl]))
time.sleep(4)
marker='DIAG-SSE-PROD-'+str(int(time.time()))
sql="insert into sse_event(event_name,namespace,payload) values ('helios_diagnostic',null,'{\"marker\":\""+marker+"\"}')"
ins=sh('kubectl','-n','pfe','exec','postgres-7','-c','postgres','--','psql','-U','postgres','-d','pfe_db','-Atc',sql); print('évènement de diagnostic inséré :',ins.stdout.strip() or ins.stderr.strip()[:80])
for p in procs: p.wait()
for name,fl in files:
    c=open(fl).read() if os.path.exists(fl) else ''; chk(f'abonné sur {name} reçoit l\'évènement injecté (outbox → poller du pod)',marker in c and c.count(marker)==1,f'(reçu {c.count(marker)} fois)')
# 5) ISOLATION utilisateur / namespace (optionnelle) : second compte RESTREINT fourni par HELIOS_TEST_USER2 / HELIOS_TEST_PASSWORD2 (une seule tentative)
#    Controle : un evenement destine a un namespace que le compte restreint ne possede pas ne doit PAS lui parvenir, alors que le compte principal (temoin) le recoit.
iso_marker='DIAG-NS-'+str(int(time.time())); u2=os.environ.get('HELIOS_TEST_USER2'); p2=os.environ.get('HELIOS_TEST_PASSWORD2')
if u2 and p2:
    r=sh('curl','-sk','-m','15','--resolve',res,'-X','POST','-H','Content-Type: application/json','-d',json.dumps({'username':u2,'password':p2}),'-w','\n%{http_code}',ing+'/api/auth/login')
    b2,c2=r.stdout.rsplit('\n',1); print('login compte restreint : HTTP',c2)
    jw2=None
    if c2=='200':
        j2=json.loads(b2); jw2=next((j2.get(k) for k in ('accessToken','token','jwt') if j2.get(k)),None)
    if jw2:
        name,ip=pods[0]; fl_r=tempfile.mktemp(); fl_a=tempfile.mktemp(); pr=[]
        for tok,fl in ((jw2,fl_r),(jwt,fl_a)):
            t=sh('curl','-s','-m','10','-i','-X','POST','-H','Authorization: Bearer '+tok,'-H','Content-Type: application/json','-d','{}',f'http://{ip}:8080/api/events/ticket').stdout
            m=re.search(r'helios_sse=([^;]+)',t)
            if m: pr.append(subprocess.Popen(['curl','-s','-N','-m','12','-H','Cookie: helios_sse='+m.group(1),f'http://{ip}:8080/api/events/stream','-o',fl]))
        time.sleep(4)
        sql2="insert into sse_event(event_name,namespace,payload) values ('helios_diagnostic','ns-diag-interdit','{\"marker\":\""+iso_marker+"\"}')"
        sh('kubectl','-n','pfe','exec','postgres-7','-c','postgres','--','psql','-U','postgres','-d','pfe_db','-Atc',sql2)
        for p in pr: p.wait()
        ca=open(fl_a).read() if os.path.exists(fl_a) else ''; cr=open(fl_r).read() if os.path.exists(fl_r) else ''
        chk('isolation : le compte principal (temoin) recoit l\'evenement du namespace de diagnostic',iso_marker in ca)
        chk('isolation : le compte RESTREINT ne recoit PAS l\'evenement d\'un namespace qui n\'est pas le sien',iso_marker not in cr and 'connected' in cr,'(flux ouvert: %s)'%('connected' in cr))
        sh('kubectl','-n','pfe','exec','postgres-7','-c','postgres','--','psql','-U','postgres','-d','pfe_db','-Atc',"delete from sse_event where payload like '%"+iso_marker+"%'")
        for fl in (fl_r,fl_a):
            try: os.unlink(fl)
            except: pass
    else: print('isolation : NON EXECUTEE (connexion du compte restreint non aboutie, aucune nouvelle tentative)')
else: print('isolation : NON EXECUTEE (HELIOS_TEST_USER2 / HELIOS_TEST_PASSWORD2 absents)')
d=sh('kubectl','-n','pfe','exec','postgres-7','-c','postgres','--','psql','-U','postgres','-d','pfe_db','-Atc',"delete from sse_event where event_name='helios_diagnostic' and payload like '%"+marker+"%'"); print('nettoyage de la ligne de diagnostic :',d.stdout.strip())
for _,fl in files+[('',f)]:
    try: os.unlink(fl)
    except: pass
print('BILAN :',sum(1 for _,o in out if o),'/',len(out),'conformes')