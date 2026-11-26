# -*- coding: utf-8 -*-
"""HELIOS - copie HORS CLUSTER chiffree des sauvegardes PostgreSQL + preuve de restauration automatique.
Execute chaque jour sur le poste d'administration (tache planifiee). Aucune suppression : les copies sont conservees.
Etapes : 1) lire .last-success sur les nœuds (A=.158, B=.155) 2) telecharger le dump (ssh + sudo cat, flux binaire) 3) verifier SHA-256 du serveur, gzip,
marqueur de fin de dump 4) chiffrer AES-256-GCM (cle locale, ACL utilisateur seul) 5) ecrire manifeste + SHA-256 du fichier chiffre 6) DECHIFFRER et
RESTAURER dans un PostgreSQL jetable (127.0.0.1) : Flyway, tables, contraintes, cles etrangeres sans orphelin 7) rapport JSON. Code retour != 0 si echec."""
import os, sys, json, gzip, hashlib, subprocess, tempfile, datetime, struct, shutil, time, re
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT = os.environ.get('HELIOS_OFFSITE_DIR', r'C:\Users\houss\helios-state\offsite')
PGBIN = os.environ.get('PGBIN', r'C:\Program Files\PostgreSQL\16\bin'); PGPORT = os.environ.get('HELIOS_OFFSITE_PGPORT', '55440')
SOURCES = [('A', 'helios158', '/opt/local-path-provisioner/pvc-036608b9-0842-4301-80bb-8f91d0eee350_pfe_postgres-backups'),
           ('B', 'helios155', '/opt/local-path-provisioner/pvc-c3506dc8-b545-44fc-ac71-5463c947de65_pfe_postgres-backups-b')]
MAGIC = b'HELIOSBK1'
# Destination INDEPENDANTE (NAS : \\\\nas\\partage\\helios, disque externe : E:\\helios, dossier synchronise...) : variable HELIOS_OFFSITE_DEST ou fichier ROOT\\dest.txt (1 ligne)
def get_dest():
    d = os.environ.get('HELIOS_OFFSITE_DEST', '').strip()
    f = os.path.join(ROOT, 'dest.txt')
    if not d and os.path.exists(f): d = open(f, encoding='utf-8').read().strip()
    return d or None
def replicate(enc_path, dest):
    if not dest: return None
    # Garde d'identite : la destination doit exister ET porter le marqueur cree a la configuration (disque debranche ou lettre reattribuee => refus)
    mk = os.path.join(dest, '.helios-dest-marker'); ref = os.path.join(ROOT, 'dest-marker.txt')
    if os.path.exists(ref):
        if not os.path.isdir(dest) or not os.path.exists(mk) or open(mk, encoding='utf-8').read().strip() != open(ref, encoding='utf-8').read().strip():
            raise RuntimeError('destination absente ou non reconnue (marqueur manquant/different) : copie hors site REFUSEE')
    os.makedirs(dest, exist_ok=True); out = None
    for suf in ('', '.sha256', '.manifest.json'):
        shutil.copy2(enc_path + suf, os.path.join(dest, os.path.basename(enc_path) + suf))
    out = os.path.join(dest, os.path.basename(enc_path))
    if hashlib.sha256(open(out, 'rb').read()).hexdigest() != hashlib.sha256(open(enc_path, 'rb').read()).hexdigest(): raise RuntimeError('copie vers la destination alteree (SHA-256 different)')
    return out
def ssh(host, cmd, binary=False):
    r = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host, cmd], capture_output=True)
    if r.returncode != 0: raise RuntimeError(f'ssh {host} : {r.stderr.decode(errors="replace")[:200]}')
    return r.stdout if binary else r.stdout.decode().strip()
def get_key():
    p = os.path.join(ROOT, 'offsite.key')
    if not os.path.exists(p):
        os.makedirs(ROOT, exist_ok=True)
        with open(p, 'wb') as f: f.write(os.urandom(32))
        subprocess.run(['icacls', p, '/inheritance:r', '/grant:r', f'{os.environ.get("USERNAME")}:(R,W)'], capture_output=True)
        print('cle AES-256 creee (ACL : utilisateur seul). SAUVEGARDEZ-LA HORS DE CE POSTE : sans elle les copies sont illisibles.')
    k = open(p, 'rb').read(); assert len(k) == 32; return k
def encrypt(key, data, aad):
    nonce = os.urandom(12); return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, aad)
def decrypt(key, blob, aad):
    assert blob[:len(MAGIC)] == MAGIC; n = blob[len(MAGIC):len(MAGIC)+12]; return AESGCM(key).decrypt(n, blob[len(MAGIC)+12:], aad)
def pull(label, host, path, key):
    try:
        ls = ssh(host, f'sudo -n cat {path}/.last-success'); ts, name, size = ls.split()[:3]
    except RuntimeError:  # dump anterieur au durcissement : pas de .last-success, repli sur le dernier dump muni de son .sha256
        name = ssh(host, f'sudo -n ls -1t {path} | grep -E "^pfe_db-[0-9]{{8}}-[0-9]{{6}}\\.sql\\.gz$" | head -n 1')
        if not name: raise RuntimeError(f'{label}: aucun dump trouve')
        ts = 'sans-.last-success'; size = ssh(host, f'sudo -n stat -c %s {path}/{name}')
    data = ssh(host, f'sudo -n cat {path}/{name}', binary=True)
    exp = ssh(host, f'sudo -n cat {path}/{name}.sha256').split()[0].lower()
    act = hashlib.sha256(data).hexdigest()
    if len(data) != int(size) or act != exp: raise RuntimeError(f'{label}: integrite serveur KO (taille {len(data)} vs {size}, sha {act[:12]} vs {exp[:12]})')
    raw = gzip.decompress(data)
    if b'PostgreSQL database dump complete' not in raw[-400:]: raise RuntimeError(f'{label}: marqueur de fin de dump absent')
    day = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')
    out_dir = os.path.join(ROOT, 'copies'); os.makedirs(out_dir, exist_ok=True)
    enc_path = os.path.join(out_dir, f'{name}.enc')
    if not os.path.exists(enc_path):
        enc = encrypt(key, data, name.encode()); open(enc_path, 'wb').write(enc)
    enc = open(enc_path, 'rb').read(); esha = hashlib.sha256(enc).hexdigest()
    open(enc_path + '.sha256', 'w').write(f'{esha}  {name}.enc\n')
    man = {'source': label, 'host': host, 'dump': name, 'dump_sha256': act, 'dump_bytes': len(data), 'encrypted_sha256': esha, 'encrypted_bytes': len(enc), 'server_timestamp': ts, 'copied_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), 'cipher': 'AES-256-GCM', 'aad': name}
    open(enc_path + '.manifest.json', 'w').write(json.dumps(man, indent=1)); return enc_path, man
def restore_test(key, enc_path, man):
    name = man['dump']; blob = open(enc_path, 'rb').read()
    if hashlib.sha256(blob).hexdigest() != man['encrypted_sha256']: raise RuntimeError('SHA-256 de la copie chiffree different du manifeste')
    sql = gzip.decompress(decrypt(key, blob, name.encode()))
    if hashlib.sha256(gzip.compress(sql, mtime=0)).hexdigest() is None: pass
    work = os.path.join(ROOT, 'pgtest'); data = os.path.join(work, 'data'); os.makedirs(work, exist_ok=True)
    env = dict(os.environ); env['PGUSER'] = 'postgres'
    if not os.path.exists(os.path.join(data, 'PG_VERSION')):
        subprocess.run([os.path.join(PGBIN, 'initdb.exe'), '-D', data, '-U', 'postgres', '-A', 'trust', '--encoding=UTF8', '--locale=C'], capture_output=True, check=True)
    opts = f'-p {PGPORT} -c listen_addresses=127.0.0.1 -c fsync=off -c shared_buffers=64MB'
    subprocess.Popen([os.path.join(PGBIN, 'pg_ctl.exe'), '-D', data, '-l', os.path.join(work, 'pg.log'), '-o', opts, '-W', 'start'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    def psql(db, *a, inp=None): return subprocess.run([os.path.join(PGBIN, 'psql.exe'), '-h', '127.0.0.1', '-p', PGPORT, '-U', 'postgres', '-d', db, '-X', *a], input=inp, capture_output=True, env=env)
    for _ in range(40):
        time.sleep(1)
        if psql('postgres', '-Atc', 'select 1').stdout.strip() == b'1': break
    else: raise RuntimeError('PostgreSQL jetable injoignable')
    try:
        db = 'restore_' + name.split('-')[1]; psql('postgres', '-Atc', f'drop database if exists {db}'); psql('postgres', '-Atc', f'create database {db}')
        r = psql(db, '-v', 'ON_ERROR_STOP=1', '-q', '-f', '-', inp=sql)
        res = {'restore_returncode': r.returncode, 'restore_errors': r.stderr.decode(errors='replace')[:300] if r.returncode else ''}
        q = lambda s: psql(db, '-Atc', s).stdout.decode().strip()
        res['flyway_max'] = q("select max(version::int) from flyway_schema_history where version is not null"); res['flyway_failed'] = q('select count(*) from flyway_schema_history where not success')
        res['tables'] = q("select count(*) from pg_tables where schemaname='public'"); res['constraints_not_validated'] = q('select count(*) from pg_constraint where not convalidated')
        res['incidents'] = q('select count(*) from incident')
        orph = q("""do $$ declare r record; n bigint; t bigint:=0; begin for r in select c.conrelid::regclass t, c.confrelid::regclass rt, c.conkey, c.confkey, c.conrelid ro, c.confrelid rid from pg_constraint c join pg_namespace ns on ns.oid=c.connamespace where c.contype='f' and ns.nspname='public' loop execute (select 'select count(*) from '||r.t||' x where '||string_agg('x.'||quote_ident(a.attname)||' is not null',' and ')||' and not exists (select 1 from '||r.rt||' y where '||string_agg('y.'||quote_ident(b.attname)||'=x.'||quote_ident(a.attname),' and ')||')' from unnest(r.conkey,r.confkey) k(ck,fk) join pg_attribute a on a.attrelid=r.ro and a.attnum=k.ck join pg_attribute b on b.attrelid=r.rid and b.attnum=k.fk) into n; t:=t+n; end loop; create temp table if not exists orph_total(n bigint); delete from orph_total; insert into orph_total values (t); end $$; select n from orph_total""")
        res['fk_orphans'] = orph.splitlines()[-1] if orph else 'n/a'
        res['ok'] = (r.returncode == 0 and res['flyway_failed'] == '0' and res['constraints_not_validated'] == '0' and res['fk_orphans'] == '0' and int(res['tables'] or 0) >= 16)
        return res
    finally:
        subprocess.run([os.path.join(PGBIN, 'pg_ctl.exe'), '-D', data, '-m', 'fast', '-w', 'stop'], capture_output=True)
def main():
    os.makedirs(os.path.join(ROOT, 'rapports'), exist_ok=True); key = get_key(); rapport = {'date_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), 'resultats': [], 'ok': True}
    for label, host, path in SOURCES:
        try:
            enc, man = pull(label, host, path, key); dest = get_dest(); denc = replicate(enc, dest); man['destination'] = dest or 'AUCUNE (copie locale seulement : pas independante du PC)'; rest = restore_test(key, denc or enc, man); man['restauration'] = rest; man['restauration_depuis'] = 'destination' if denc else 'local'
            rapport['resultats'].append(man); rapport['ok'] &= bool(rest.get('ok'))
            print(f"[{label}] {man['dump']} -> {os.path.basename(enc)} ({man['encrypted_bytes']} o) ; restauration {'OK' if rest.get('ok') else 'ECHEC'} : Flyway V{rest.get('flyway_max')} tables={rest.get('tables')} incidents={rest.get('incidents')} orphelins={rest.get('fk_orphans')}")
        except Exception as e:
            rapport['ok'] = False; rapport['resultats'].append({'source': label, 'erreur': str(e)[:300]}); print(f'[{label}] ECHEC : {e}')
    p = os.path.join(ROOT, 'rapports', 'offsite-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + ('.json' if rapport['ok'] else '.ECHEC.json')); open(p, 'w').write(json.dumps(rapport, indent=1)); print('rapport :', p)
    open(os.path.join(ROOT, 'dernier-statut.txt'), 'w').write(('OK ' if rapport['ok'] else 'ECHEC ') + rapport['date_utc'] + (' [destination: ' + str(get_dest()) + ']' if get_dest() else ' [AUCUNE destination independante]') + '\n')
    sys.exit(0 if rapport['ok'] else 1)
if __name__ == '__main__': main()