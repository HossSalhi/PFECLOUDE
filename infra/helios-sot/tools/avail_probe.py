# -*- coding: utf-8 -*-
"""SONDE EXTERNE DE DISPONIBILITÉ de HELIOS (lecture seule, stdlib) — preuve chiffrée que « HELIOS reste disponible » pendant l'arrêt d'une VM.

Toutes les INTERVAL secondes (défaut 2), pour CHAQUE répartiteur HAProxy (.160 et .162, actif/actif, sans VIP) :
  ui@<lb>             GET https://<lb>/                  (Host: helios.orange.tn)   attendu HTTP 200   — l'interface via l'ingress
  backend@<lb>        GET https://<lb>/actuator/health   (Host: helios.orange.tn)   attendu HTTP 200 OU 403 — le backend via l'ingress
                      (200 ou 403 selon le pod qui répond : dans les deux cas l'APPLICATION a répondu ; 502/503/504 ou délai = échec)
  k8s-readyz@<lb>     GET https://<lb>:6443/readyz                                  attendu HTTP 200   — l'API Kubernetes via HAProxy
Un code différent, un délai dépassé ou une erreur de connexion = ÉCHEC de cette mesure. Aucune écriture, aucun jeton : GET anonymes.
« Service HELIOS » = à chaque cycle, AU MOINS UN répartiteur sert à la fois l'interface ET le backend (ce que vit un utilisateur qui
retente) ; la disponibilité de chaque cible est aussi donnée séparément.

  python avail_probe.py --out C:\\...\\probe.csv [--minutes 45] [--interval 2] [--stop-file C:\\...\\stop.txt]
  python avail_probe.py --summarize C:\\...\\probe.csv         (relit un CSV et affiche le résumé)
Fin : délai écoulé OU fichier --stop-file créé. Sortie console : uniquement les CHANGEMENTS d'état d'une cible, puis le résumé.
Charge : 6 requêtes toutes les 2 s (≈ 3/s), délai 4 s : négligeable.
"""
import argparse
import csv
import datetime as dt
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

LBS = ("192.168.148.160", "192.168.148.162")
HOST = "helios.orange.tn"
TIMEOUT_S = 4.0
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE        # certificat interne : on mesure la DISPONIBILITÉ, pas la chaîne de confiance


def targets():
    """[(nom, url, en-tête Host ou None, codes acceptés)] — 3 mesures par répartiteur. Pour le backend, 200 ET 403 comptent comme « servi » :
    /actuator/health répond tantôt 200 tantôt 403 selon le pod (constaté le 17 nov. 2026) ; une erreur de l'ingress (502/503/504) n'en est pas un."""
    out = []
    for lb in LBS:
        short = lb.rsplit(".", 1)[1]
        out += [(f"ui@{short}", f"https://{lb}/", HOST, ("200",)),
                (f"backend@{short}", f"https://{lb}/actuator/health", HOST, ("200", "403")),
                (f"k8s-readyz@{short}", f"https://{lb}:6443/readyz", None, ("200",))]
    return out


def probe(url, host=None, timeout=TIMEOUT_S):
    """Retourne (code, ms) : code = « 200 »… ou « ERR:<type> » (délai, refus, TLS…). Ne lève jamais."""
    req = urllib.request.Request(url, headers={"Host": host} if host else {}, method="GET")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            code = str(r.status)
            r.read(64)
    except urllib.error.HTTPError as e:
        code = str(e.code)
    except Exception as e:  # noqa: BLE001 — une sonde ne doit jamais s'arrêter
        code = "ERR:" + type(e).__name__
    return code, int((time.perf_counter() - t0) * 1000)


def summarize(rows, interval=2.0):
    """Pure. `rows` = itérable de (epoch_s, cible, ok(bool), ms). Retourne
    {"cibles": {nom: {n, ok_pct, pannes:[(debut, fin, duree_s)], plus_longue_s, ms_p50, ms_max}}, "service": {n, ok_pct, pannes, plus_longue_s}}.
    Une panne = suite CONSÉCUTIVE d'échecs ; durée = dernier échec - premier échec + interval (un seul échec dure donc « interval »)."""
    par_cible, cycles = {}, {}
    for ts, cible, ok, ms in sorted(rows, key=lambda r: r[0]):
        par_cible.setdefault(cible, []).append((ts, ok, ms))
        cycles.setdefault(int(ts // interval), {})[cible] = ok

    def pannes(serie):
        res, debut, dernier = [], None, None
        for ts, ok in serie:
            if not ok:
                debut = ts if debut is None else debut
                dernier = ts
            elif debut is not None:
                res.append((debut, dernier, dernier - debut + interval))
                debut = None
        if debut is not None:
            res.append((debut, dernier, dernier - debut + interval))
        return res

    out = {"cibles": {}, "service": {}}
    for nom, serie in par_cible.items():
        ps = pannes([(ts, ok) for ts, ok, _ in serie])
        lat = sorted(ms for _, ok, ms in serie if ok)
        out["cibles"][nom] = {
            "n": len(serie), "ok_pct": round(100.0 * sum(1 for _, ok, _ in serie if ok) / len(serie), 2), "pannes": ps,
            "plus_longue_s": max((p[2] for p in ps), default=0.0), "ms_p50": lat[len(lat) // 2] if lat else None, "ms_max": lat[-1] if lat else None}
    serie_service = []
    for k in sorted(cycles):
        c = cycles[k]
        lbs = {n.split("@", 1)[1] for n in c if "@" in n}
        ok = any(c.get(f"ui@{lb}") and c.get(f"backend@{lb}") for lb in lbs)
        serie_service.append((k * interval, ok))
    ps = pannes(serie_service)
    out["service"] = {"n": len(serie_service), "ok_pct": round(100.0 * sum(1 for _, ok in serie_service if ok) / max(1, len(serie_service)), 2),
                      "pannes": ps, "plus_longue_s": max((p[2] for p in ps), default=0.0)}
    return out


def print_summary(res):
    print("\n==== RÉSUMÉ DE DISPONIBILITÉ ====")
    s = res["service"]
    print(f"  SERVICE HELIOS (≥ 1 répartiteur sert interface ET backend) : {s['ok_pct']} % sur {s['n']} cycles ; "
          f"{len(s['pannes'])} panne(s) ; plus longue {s['plus_longue_s']:.0f} s")
    for nom in sorted(res["cibles"]):
        c = res["cibles"][nom]
        print(f"  {nom:<18} {c['ok_pct']:>6} % sur {c['n']:>4} mesures ; {len(c['pannes'])} panne(s) ; plus longue {c['plus_longue_s']:.0f} s ; "
              f"latence p50 {c['ms_p50']} ms, max {c['ms_max']} ms")


def read_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((float(r["epoch"]), r["cible"], r["ok"] == "1", int(r["ms"])))
    return rows


def run(out_path, minutes, interval, stop_file):
    tg = targets()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fin = time.time() + minutes * 60
    etat = {}
    print(f"sonde démarrée {dt.datetime.now():%H:%M:%S} : {len(tg)} cibles toutes les {interval:g} s pendant {minutes:g} min ; "
          f"stop : {stop_file or 'délai seulement'}", flush=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=len(tg)) as pool:
        w = csv.writer(fh)
        w.writerow(["epoch", "iso", "cible", "code", "ok", "ms"])
        while time.time() < fin and not (stop_file and os.path.exists(stop_file)):
            t0 = time.time()
            res = list(pool.map(lambda t: (t[0],) + probe(t[1], t[2]) + (t[3],), tg))
            for nom, code, ms, attendus in res:
                ok = code in attendus
                w.writerow([f"{t0:.3f}", dt.datetime.fromtimestamp(t0).strftime("%H:%M:%S"), nom, code, 1 if ok else 0, ms])
                if etat.get(nom) is not ok:
                    if nom in etat or not ok:
                        print(f"{dt.datetime.fromtimestamp(t0):%H:%M:%S}  {nom:<18} {'OK' if ok else 'ÉCHEC (' + code + ')'}", flush=True)
                    etat[nom] = ok
            fh.flush()
            time.sleep(max(0.0, interval - (time.time() - t0)))
    print_summary(summarize(read_csv(out_path), interval))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out")
    ap.add_argument("--minutes", type=float, default=45)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--stop-file")
    ap.add_argument("--summarize", metavar="CSV")
    a = ap.parse_args(argv)
    if a.summarize:
        print_summary(summarize(read_csv(a.summarize), a.interval))
        return 0
    if not a.out:
        ap.error("--out requis (ou --summarize)")
    return run(a.out, a.minutes, a.interval, a.stop_file)


if __name__ == "__main__":
    sys.exit(main())
