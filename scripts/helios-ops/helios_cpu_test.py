#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lanceur de test CPU HELIOS sur .158 / .155 / .153 : PRE-VOL (feu vert / orange / rouge) puis, sur demande, lancement
de la charge de laboratoire et SUIVI jusqu'a la resolution.

    python helios_cpu_test.py                      # pre-vol des 3 noeuds, LECTURE SEULE (defaut)
    python helios_cpu_test.py 158 155              # pre-vol de certains noeuds
    python helios_cpu_test.py --go                 # lance la charge sur les noeuds au vert/orange, puis suit le cycle
    python helios_cpu_test.py --go 155 --force     # lance meme si le noeud est au rouge (deconseille)

Le pre-vol repond, AVANT le test, a « pourquoi il ne se passerait rien ? » : plafond CPU temporaire de HELIOS encore pose
(~4 min), incident CPU deja actif sur le noeud (le backend correlerait au lieu de creer), anomalie de rang superieur
(le detecteur n'en elit qu'une par cycle), supervision non fiable ou disque de .160 proche de 90 %, cluster ou chaine
HELIOS degrades. Une charge lancee sans pre-vol vert peut « ne rien faire » sans le dire : ne pas s'en passer.

Seul --go ecrit quelque chose : `systemctl start helios-cpuload.service` (charge de LABORATOIRE, priorite basse, arret
automatique 45 min / 30 min sur .153) et, si le cluster se degrade pendant le suivi, `systemctl stop` de CES charges.
Aucun secret n'est lu ni affiche.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request

NOEUDS = {"158": ("k8s-worker1", "192.168.148.158"),
          "155": ("k8s-worker2", "192.168.148.155"),
          "153": ("k8s-master", "192.168.148.153")}
IP_MONITORING = "192.168.148.160"
IP_BRIDGE = "192.168.148.161"
PROM = "http://%s:9090/api/v1/query?" % IP_MONITORING
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no"]
DELAI_DETECTION_S = 8 * 60          # 3 min de rate + 5 min de fenetre continue au-dessus de 90 %
ANTI_FLAPPING_S = 5 * 60            # backend : un incident de meme type+cible cree il y a moins de 5 min est correle
STATUTS_ACTIFS = ("NEW", "OPEN", "IN_PROGRESS", "WAITING_FOR_APPROVAL")

# les 10 regles qui passent AVANT NODE_HIGH_CPU (rang 11) dans le detecteur
REGLES_AU_DESSUS_DU_CPU = ["ETCD_NO_LEADER", "ETCD_SLOW_FSYNC", "ETCD_LEADER_FLAPPING", "NODE_NOT_READY",
                           "NODE_DISK_PRESSURE", "NODE_PID_PRESSURE", "NODE_NETWORK_UNAVAILABLE", "NODE_CORDONED",
                           "NODE_DISK_FULL", "NODE_HIGH_MEMORY"]
ORDRE_NIVEAU = {"INFO": 0, "ORANGE": 1, "ROUGE": 2}


# ------------------------------------------------------------------------------------------------ parsing (pur)
def parse_show(texte):
    """Sortie `systemctl show` (lignes cle=valeur) -> dict."""
    d = {}
    for ligne in (texte or "").splitlines():
        if "=" in ligne:
            k, v = ligne.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def etat_noeud(sortie):
    """Sortie de la commande de releve d'un noeud -> faits (dict)."""
    d = parse_show(sortie)
    maintenant = int(d["NOW"]) if d.get("NOW", "").isdigit() else None
    expire = int(d["EXPIRE"]) if d.get("EXPIRE", "").isdigit() else None
    try:
        charge = float(d.get("LOAD", ""))
    except ValueError:
        charge = None
    return {
        "joignable": True,
        "load_state": d.get("LoadState", ""),
        "active": d.get("ActiveState", "") == "active",
        "depuis": d.get("ActiveEnterTimestamp", ""),
        "quota": d.get("CPUQuotaPerSecUSec", ""),
        "plafond_temporaire": "temporary-cpu-quota" in d.get("DropInPaths", ""),
        "expire_dans_s": (expire - maintenant) if (expire is not None and maintenant is not None) else None,
        "load": charge,
    }


def parse_detecteur(sortie):
    """Sortie de la commande de releve du detecteur -> faits (dict) ou None."""
    morceaux = (sortie or "").rsplit("---", 1)
    try:
        d = json.loads(morceaux[0].strip())
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    lignes = [l.strip() for l in (morceaux[1] if len(morceaux) > 1 else "").splitlines() if l.strip()]
    v3 = bool(lignes and lignes[-1].isdigit() and int(lignes[-1]) > 0)     # le fichier deploye contient la persistance
    return {"type": d.get("type"), "cible": d.get("cible"), "matches": d.get("matches") or [],
            "fiable": bool(d.get("monitoringFiable", True)), "v3": v3}


# ------------------------------------------------------------------------------------------------ evaluation (pure)
def duree(s):
    s = int(s)
    return "%d s" % s if s < 120 else "%d min" % (s // 60)


def evaluer(f, selection):
    """Faits -> rapport. Pure : aucune E/S. Chaque raison = (niveau, constat, remede)."""
    glob = []
    c = f.get("cluster")
    if c is None:
        glob.append(("ORANGE", "etat du cluster inconnu (kubectl muet)", "verifier `kubectl get nodes`"))
    elif c[0] < c[1]:
        glob.append(("ROUGE", "cluster degrade : %d/%d noeuds Ready" % (c[0], c[1]),
                     "attendre le retour a %d/%d avant de lancer une charge" % (c[1], c[1])))

    d = f.get("detecteur")
    v3 = bool(d and d.get("v3"))
    if d is None:
        glob.append(("ROUGE", "detecteur injoignable (ssh vers .160)", "verifier la VM .160 et le service ssh"))
    else:
        if not d.get("fiable", True):
            glob.append(("ROUGE", "supervision non fiable (Prometheus/Grafana) : le detecteur repondrait NO_INCIDENT",
                         "reparer la supervision de .160 avant tout test"))
        hauts = [m for m in d.get("matches", []) if m in REGLES_AU_DESSUS_DU_CPU]
        et_si_connu = bool(f.get("et_si")) and all(f["et_si"].get(n) for n in selection)
        if hauts and et_si_connu:
            glob.append(("INFO", "anomalie(s) de rang superieur active(s) mais sans effet sur le test : %s" % ", ".join(hauts),
                         "le detecteur elirait quand meme le CPU (voir le « et si » de chaque noeud)"))
        elif hauts:
            glob.append(("ORANGE", "anomalie(s) de rang superieur active(s) : %s" % ", ".join(hauts),
                         ("elles passent d'abord tant qu'elles ont moins de 10 min ; au-dela (detecteur PRIORITY-03) elles ne "
                          "bloquent plus. Voir : ssh kube@%s \"python3 /opt/helios/detecter.py --diag\"" % IP_MONITORING)
                         if v3 else
                         "le detecteur actuel ne laisse passer le CPU qu'une fois ces anomalies terminees (sauf disque de .160 < 90 %)"))

    disque = f.get("disque_160")
    if disque is not None:
        if disque >= 90:
            glob.append(("ORANGE" if v3 else "ROUGE", "disque de .160 a %.1f %% (>= 90 %% : urgence disque)" % disque,
                         "en fond apres 10 min (PRIORITY-03)" if v3 else "libere de l'espace sur .160 : l'urgence disque passe devant le CPU"))
        elif disque >= 85:
            glob.append(("ORANGE", "disque de .160 a %.1f %% : approche du seuil d'urgence de 90 %%" % disque,
                         "surveiller ; cause probable si en hausse : WAL de Prometheus (voir ci-dessous)"))
    wal_mo, echecs = f.get("wal_mo"), f.get("wal_echecs")
    if (wal_mo is not None and wal_mo > 800) or (echecs or 0) > 0:
        glob.append(("ORANGE", "Prometheus de .160 : WAL %s Mo, %s compaction(s) echouee(s) depuis son dernier redemarrage" % (
            "?" if wal_mo is None else int(wal_mo), "?" if echecs is None else int(echecs)),
            "le WAL ne se purge pas et remplit le disque : voir scripts/helios-ops/repair_prometheus_wal_160.py (--verify, simulation par defaut)"))

    p = f.get("pipeline") or {}
    if p.get("bridge") not in (None, "active"):
        glob.append(("ROUGE", "Bridge HELIOS inactif sur .161 (%s)" % p.get("bridge"), "sans Bridge, aucune remediation"))
    pret = p.get("backend_pret")
    if pret is not None and pret == 0:
        glob.append(("ROUGE", "aucun pod backend pret", "sans backend, aucun incident n'est cree"))
    elif pret is not None and pret < 2:
        glob.append(("ORANGE", "un seul pod backend pret", "fonctionne mais sans redondance"))

    ram = f.get("hote_ram_mo")
    if ram is not None and ram < 1000:
        glob.append(("ORANGE", "RAM de l'hote basse (%d Mo disponibles)" % ram, "fermer des applications : risque de gel des VM"))

    noeuds = {}
    for n in selection:
        nom = NOEUDS[n][0]
        nf = (f.get("noeuds") or {}).get(n)
        r = []
        es = (f.get("et_si") or {}).get(n)
        if es and tuple(es) != ("NODE_HIGH_CPU", nom):
            r.append(("ROUGE", "un CPU sur .%s serait MASQUE : le detecteur elirait %s (cible %s)" % (n, es[0], es[1]),
                      ("cette anomalie est recente (moins de 10 min) ou unique de son rang ; attendre qu'elle se termine ou qu'elle passe "
                       "« de fond » apres 10 min, voir --diag") if v3 else
                      "le detecteur actuel donne la priorite a cette anomalie tant qu'elle dure"))
        if not nf or not nf.get("joignable"):
            r.append(("ROUGE", "noeud .%s injoignable en SSH" % n, "verifier la VM"))
        else:
            if nf.get("load_state") != "loaded":
                r.append(("ROUGE", "unite helios-cpuload.service absente sur .%s" % n, "l'unite de laboratoire doit etre installee"))
            if nf.get("plafond_temporaire"):
                reste = nf.get("expire_dans_s")
                quand = "pendant encore ~%s" % duree(reste) if reste is not None and reste > 0 else "(expiration imminente ou inconnue)"
                r.append(("ROUGE", "plafond CPU temporaire de HELIOS (25 %%) encore pose %s : une charge lancee maintenant serait "
                                   "bridee et ne declencherait RIEN" % quand, "attendre l'expiration automatique (environ 4 min apres son application)"))
            elif nf.get("quota") not in ("infinity", "", None):
                r.append(("ROUGE", "quota CPU anormal (%s) sans plafond temporaire connu" % nf.get("quota"),
                          "systemctl show helios-cpuload.service -p DropInPaths ; retirer le drop-in avec l'autorisation de l'exploitant"))
            if nf.get("active"):
                r.append(("INFO", "charge deja active depuis %s" % nf.get("depuis"),
                          "l'incident est attendu ~%d min apres son demarrage" % (DELAI_DETECTION_S // 60)))
            if nf.get("load") is not None and nf["load"] > 3.0 and not nf.get("active"):
                r.append(("ORANGE", "charge de base elevee (load average %.1f) sans charge de test" % nf["load"], "verifier ce qui consomme"))
        actifs = [i for i in (f.get("incidents_cpu") or {}).get(nom, []) if i.get("statut") in STATUTS_ACTIFS]
        for i in actifs:
            r.append(("ROUGE", "incident CPU #%s encore ACTIF (%s) sur %s : un nouveau test serait CORRELE a lui, aucun nouvel "
                               "incident ni remediation" % (i["id"], i["statut"], nom),
                      "attendre sa fin ; s'il est reste bloque, le clore avec l'autorisation de l'exploitant"))
        recents = [i for i in (f.get("incidents_cpu") or {}).get(nom, [])
                   if i.get("statut") not in STATUTS_ACTIFS and i.get("age_s") is not None and i["age_s"] < ANTI_FLAPPING_S]
        for i in recents:
            # SANS EFFET PRATIQUE : le backend ne correle (anti-flapping 5 min) que si le nouvel incident est detecte moins de 5 min
            # apres le precedent ; une charge lancee maintenant n'est detectee qu'~8 min plus tard.
            r.append(("INFO", "incident CPU #%s cree il y a %s sur %s : l'anti-flapping du backend (5 min) est sans effet sur ce test" % (
                i["id"], duree(i["age_s"]), nom), "la detection a lieu ~%d min apres le demarrage de la charge" % (DELAI_DETECTION_S // 60)))
        noeuds[n] = r

    def pire(rs):
        return max((ORDRE_NIVEAU[x[0]] for x in rs), default=0)
    inv = {v: k for k, v in ORDRE_NIVEAU.items()}
    pg = pire(glob)
    verdicts = {n: inv[max(pg, pire(rs))] for n, rs in noeuds.items()}
    # INFO seul = vert (information, pas un frein)
    verdicts = {n: ("VERT" if v == "INFO" else v) for n, v in verdicts.items()}
    return {"global": glob, "noeuds": noeuds, "verdicts": verdicts,
            "verdict": max(verdicts.values(), key=lambda v: {"VERT": 0, "ORANGE": 1, "ROUGE": 2}[v], default="VERT")}


def afficher(rapport, selection, sortie=sys.stdout):
    p = lambda s="": print(s, file=sortie)
    p("PRE-VOL DU TEST CPU  (%s)" % time.strftime("%Y-%m-%d %H:%M:%S"))
    p("=" * 78)
    if rapport["global"]:
        p("Etat general :")
        for niv, msg, remede in rapport["global"]:
            p("  [%s] %s" % (niv, msg))
            p("          -> %s" % remede)
    else:
        p("Etat general : RAS (cluster, detecteur, supervision, chaine HELIOS)")
    for n in selection:
        p("")
        p(".%s (%s) : %s" % (n, NOEUDS[n][0], rapport["verdicts"][n]))
        for niv, msg, remede in rapport["noeuds"][n]:
            p("  [%s] %s" % (niv, msg))
            p("          -> %s" % remede)
        if not rapport["noeuds"][n]:
            p("  [OK] rien ne s'oppose au test")
    p("")
    p("VERDICT GLOBAL : %s" % rapport["verdict"])
    p("Delai normal : l'incident apparait ~%d min apres le demarrage de la charge (regle CPU > 90 %% en continu), "
      "resolu ~1 min plus tard." % (DELAI_DETECTION_S // 60))


# ------------------------------------------------------------------------------------------------ collecte (E/S)
def ssh(ip, commande, delai=45):
    try:
        p = subprocess.run(SSH + ["kube@%s" % ip, commande], capture_output=True, text=True, timeout=delai)
        return p.returncode, p.stdout
    except Exception:
        return 255, ""


def prom(expr):
    try:
        with urllib.request.urlopen(PROM + urllib.parse.urlencode({"query": expr}), timeout=25) as r:
            return [float(x["value"][1]) for x in json.load(r)["data"]["result"]]
    except Exception:
        return []


def sql(requete):
    try:
        p = subprocess.run(["kubectl", "exec", "-i", "-n", "pfe", "postgres-8", "-c", "postgres", "--",
                            "psql", "-U", "postgres", "-d", "pfe_db", "-At", "-F", "|"],
                           input=requete, capture_output=True, text=True, timeout=60)
        return [l.split("|") for l in p.stdout.splitlines() if l.strip()]
    except Exception:
        return []


CMD_NOEUD = ("systemctl show helios-cpuload.service -p LoadState -p ActiveState -p ActiveEnterTimestamp "
             "-p CPUQuotaPerSecUSec -p DropInPaths --no-pager; echo LOAD=$(cut -d' ' -f1 /proc/loadavg); echo NOW=$(date -u +%s); "
             "E=$(systemctl list-timers 'helios-quota-expire-*' --all --no-legend --no-pager 2>/dev/null | head -1 | awk '{print $1\" \"$2\" \"$3\" \"$4}'); "
             "[ -n \"$E\" ] && echo EXPIRE=$(date -d \"$E\" +%s 2>/dev/null); true")
CMD_DETECTEUR = "python3 /opt/helios/detecter.py; echo; echo ---; grep -c DUREE_PERSISTANTE_S /opt/helios/detecter.py; true"


# « Et si une anomalie CPU apparaissait MAINTENANT sur ce noeud ? » : le detecteur DEPLOYE sur .160 est execute avec
# une serie CPU simulee ajoutee aux vraies donnees ; AUCUNE ecriture (l'etat de persistance n'est pas sauvegarde).
ET_SI = """
import contextlib, importlib.util, io, json, sys
spec = importlib.util.spec_from_file_location("det", "/opt/helios/detecter.py")
det = importlib.util.module_from_spec(spec)
spec.loader.exec_module(det)
if hasattr(det, "ecrire_etat"):
    det.ecrire_etat = lambda etat: None
requete = {r[0]: r[1] for r in det.REGLES}["NODE_HIGH_CPU"]
reel = det.prom
def faux(q):
    res = list(reel(q))
    if q == requete:
        res.append({"metric": {"instance": sys.argv[1] + ":9100"}, "value": [0, "96.5"]})
    return res
det.prom = faux
tampon = io.StringIO()
with contextlib.redirect_stdout(tampon):
    det.main()
d = json.loads(tampon.getvalue())
print("ETSI|%s|%s" % (d.get("type"), d.get("cible")))
"""


def parse_et_si(sortie):
    """Sortie du script « et si » -> (type, cible) ou None."""
    for ligne in (sortie or "").splitlines():
        if ligne.startswith("ETSI|"):
            morceaux = ligne.strip().split("|")
            if len(morceaux) == 3:
                return morceaux[1], morceaux[2]
    return None


def et_si(ip):
    try:
        # entree en OCTETS avec des fins de ligne LF : en mode texte, Windows enverrait du CRLF (bash le rejetterait)
        p = subprocess.run(SSH + ["kube@%s" % IP_MONITORING, "python3 - %s" % ip], input=ET_SI.replace("\r\n", "\n").encode("utf-8"),
                           capture_output=True, timeout=90)
        return parse_et_si(p.stdout.decode("utf-8", "replace"))
    except Exception:
        return None


def memoire_hote_mo():
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("u", ctypes.c_ulong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong),
                        ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
        m = MS()
        m.l = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return int(m.ap / 1048576)
    except Exception:
        return None


def collecter(selection):
    f = {"noeuds": {}, "incidents_cpu": {}}
    for n in selection:
        rc, out = ssh(NOEUDS[n][1], CMD_NOEUD)
        f["noeuds"][n] = etat_noeud(out) if "LoadState" in out else {"joignable": False}
    try:
        p = subprocess.run(["kubectl", "get", "nodes", "--no-headers"], capture_output=True, text=True, timeout=30)
        lignes = [l.split() for l in p.stdout.splitlines() if l.strip()]
        f["cluster"] = (sum(1 for l in lignes if len(l) > 1 and l[1] == "Ready"), len(lignes)) if lignes else None
    except Exception:
        f["cluster"] = None
    rc, out = ssh(IP_MONITORING, CMD_DETECTEUR)
    f["detecteur"] = parse_detecteur(out)
    d = prom('(1-(node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs",instance=~".*160.*"}/'
             'node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs",instance=~".*160.*"}))*100')
    f["disque_160"] = d[0] if d else None
    w = prom("prometheus_tsdb_wal_storage_size_bytes")
    f["wal_mo"] = w[0] / 1048576 if w else None
    e = prom("prometheus_tsdb_compactions_failed_total")          # repart de 0 a chaque redemarrage de Prometheus
    f["wal_echecs"] = e[0] if e else None
    rc, out = ssh(IP_BRIDGE, "systemctl is-active helios-ssh-bridge.service", 30)
    pret = None
    try:
        p = subprocess.run(["kubectl", "get", "pods", "-n", "pfe", "--no-headers"], capture_output=True, text=True, timeout=30)
        pret = sum(1 for l in p.stdout.splitlines() if l.startswith("backend-") and l.split()[1].split("/")[0] == l.split()[1].split("/")[1]
                   and l.split()[2] == "Running")
    except Exception:
        pass
    f["pipeline"] = {"bridge": out.strip() or None, "backend_pret": pret}
    noms = ",".join("'%s'" % NOEUDS[n][0] for n in selection)
    lignes = sql("select app, id, status, extract(epoch from ((now() at time zone 'Africa/Tunis') - created_at))::bigint "
                 "from incident where incident_type='NODE_HIGH_CPU' and app in (%s) and (status in "
                 "('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL') or created_at > (now() at time zone 'Africa/Tunis') - interval '1 hour') "
                 "order by id desc;" % noms)
    for l in lignes:
        if len(l) >= 4:
            f["incidents_cpu"].setdefault(l[0], []).append({"id": l[1], "statut": l[2], "age_s": int(l[3]) if l[3].lstrip("-").isdigit() else None})
    f["et_si"] = {n: et_si(NOEUDS[n][1]) for n in selection}
    f["hote_ram_mo"] = memoire_hote_mo()
    return f


# ------------------------------------------------------------------------------------------------ lancement + suivi
def demarrer(n):
    rc, out = ssh(NOEUDS[n][1], "sudo -n systemctl start helios-cpuload.service; echo \"$(systemctl is-active helios-cpuload.service) $(date -u +%s)\"")
    mots = out.split()
    return rc == 0 and mots and mots[0] == "active", int(mots[1]) if len(mots) > 1 and mots[1].isdigit() else int(time.time())


def arreter(n):
    ssh(NOEUDS[n][1], "sudo -n systemctl stop helios-cpuload.service")


def suivre(demarres, duree_max):
    """Suit les noeuds demarres jusqu'a la resolution de leur incident CPU (ou duree_max)."""
    base = sql("select coalesce(max(id),0) from incident;")
    id0 = int(base[0][0]) if base and base[0][0].isdigit() else 0
    debut = time.time()
    attendus = {NOEUDS[n][0]: n for n in demarres}
    fin = {}
    vus = set()
    muet = 0
    print("\nSUIVI (toutes les ~20 s ; Ctrl+C pour arreter le suivi sans arreter la charge)")
    while time.time() - debut < duree_max and len(fin) < len(attendus):
        cpu = {n: prom('100-(avg by(instance)(rate(node_cpu_seconds_total{mode="idle",instance=~".*%s.*"}[3m]))*100)' % n) for n in demarres}
        regle = {n: prom('min_over_time((100-(avg by(instance)(rate(node_cpu_seconds_total{mode="idle",instance=~".*%s.*"}[3m]))*100))[5m:30s])' % n) for n in demarres}
        c = None
        try:
            p = subprocess.run(["kubectl", "get", "nodes", "--no-headers"], capture_output=True, text=True, timeout=30)
            ls = [l.split() for l in p.stdout.splitlines() if l.strip()]
            c = (sum(1 for l in ls if len(l) > 1 and l[1] == "Ready"), len(ls))
        except Exception:
            pass
        print("%s  %s | regle(>90 sur 5 min) %s | noeuds Ready=%s" % (
            time.strftime("%H:%M:%S"),
            " ".join(".%s CPU=%s" % (n, ["%.0f" % x for x in cpu[n]] or "?") for n in demarres),
            " ".join(".%s=%s" % (n, ["%.0f" % x for x in regle[n]] or "?") for n in demarres),
            "%s/%s" % c if c else "?"), flush=True)
        if c is None or c[0] < c[1]:
            muet += 1
            if muet >= 2:
                print("!!! cluster degrade : ARRET DE SECURITE de la charge lancee par ce script")
                for n in demarres:
                    arreter(n)
                return fin
        else:
            muet = 0
        for l in sql("select id, to_char(created_at,'HH24:MI:SS'), app, status, to_char(updated_at,'HH24:MI:SS') from incident "
                     "where id > %d and incident_type='NODE_HIGH_CPU' order by id;" % id0):
            if tuple(l) not in vus:
                vus.add(tuple(l))
                print("   >>> incident #%s  %s  %s  %s" % (l[0], l[1], l[2], l[3]), flush=True)
            if len(l) >= 5 and l[2] in attendus and l[3] in ("RESOLVED", "RESOLUTION_FAILED"):
                fin[l[2]] = (l[0], l[1], l[4], l[3])
        time.sleep(20)
    return fin


def principal(argv=None):
    ap = argparse.ArgumentParser(description="Pre-vol et lancement du test CPU HELIOS (.158 .155 .153)")
    ap.add_argument("noeuds", nargs="*", default=[], help="158 155 153 (defaut : les trois)")
    ap.add_argument("--go", action="store_true", help="lancer la charge sur les noeuds au vert/orange, puis suivre")
    ap.add_argument("--force", action="store_true", help="lancer meme au rouge (deconseille)")
    ap.add_argument("--duree", type=int, default=14 * 60, help="duree max du suivi en secondes (defaut 840)")
    a = ap.parse_args(argv)
    selection = [n for n in (a.noeuds or list(NOEUDS))]
    inconnus = [n for n in selection if n not in NOEUDS]
    if inconnus:
        print("noeud inconnu : %s (valeurs : 158 155 153)" % ", ".join(inconnus))
        return 2
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    faits = collecter(selection)
    rapport = evaluer(faits, selection)
    afficher(rapport, selection)
    if faits.get("detecteur") and faits["detecteur"].get("v3"):
        rc, out = ssh(IP_MONITORING, "python3 /opt/helios/detecter.py --diag")
        if rc == 0 and out.strip():
            print("\nDETAIL DU DETECTEUR (--diag) :\n" + out.strip())
    if not a.go:
        print("\nPre-vol termine : rien n'a ete lance. Pour lancer : python scripts/helios-ops/helios_cpu_test.py --go [158 155 153]")
        return 0 if rapport["verdict"] != "ROUGE" else 1
    glob_rouge = any(n == "ROUGE" for n, _, _ in rapport["global"])
    if glob_rouge and not a.force:
        print("\nREFUS : un frein GLOBAL au rouge empeche le test (voir ci-dessus). --force pour passer outre (deconseille).")
        return 1
    demarres = {}
    for n in selection:
        v = rapport["verdicts"][n]
        if v == "ROUGE" and not a.force:
            print("\n.%s : au ROUGE, non lance." % n)
            continue
        if faits["noeuds"].get(n, {}).get("active"):
            print("\n.%s : charge deja active, suivie sans relance." % n)
            demarres[n] = int(time.time())
            continue
        ok, t0 = demarrer(n)
        print("\n.%s : charge %s a %s (incident attendu vers %s)" % (
            n, "DEMARREE" if ok else "ECHEC DU DEMARRAGE", time.strftime("%H:%M:%S", time.localtime(t0)),
            time.strftime("%H:%M:%S", time.localtime(t0 + DELAI_DETECTION_S))))
        if ok:
            demarres[n] = t0
    if not demarres:
        return 1
    fin = suivre(demarres, a.duree)
    print("\nBILAN")
    for n in demarres:
        nom = NOEUDS[n][0]
        if nom in fin:
            i = fin[nom]
            print("  .%s : incident #%s cree %s, %s a %s" % (n, i[0], i[1], i[3], i[2]))
        else:
            print("  .%s : AUCUN incident termine dans le delai du suivi" % n)
    return 0


if __name__ == "__main__":
    sys.exit(principal())
