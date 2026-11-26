# -*- coding: utf-8 -*-
"""Tests de infra/helios-sot/tools/passive_observe.py (collecteurs simulés : aucun réseau, aucune VM, aucun cluster).

Lancer : python -I scripts/helios-ops/test_passive_observe.py
"""
import datetime as dt
import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools", "passive_observe.py"))
spec = importlib.util.spec_from_file_location("passive_observe", TOOL)
po = importlib.util.module_from_spec(spec)
spec.loader.exec_module(po)


class Patch:
    """Remplace des attributs du module le temps d'un test."""
    def __init__(self, **kw):
        self.kw, self.old = kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(po, k)
            setattr(po, k, v)

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(po, k, v)


def good_rec(**over):
    rec = {
        "ts": "2026-11-06T22:00:00+01:00", "api": {"ok": True, "latence_ms": 80, "echecs": []},
        "noeuds": {n: {"ready": True, "pressions": [], "cordon": False} for n in po.NODE_NAMES},
        "etcd": {"membres": {"153": True, "155": True, "158": True, "160": True, "162": True}, "leader": "162", "changements_leader_cumules": 12},
        "camunda": {"topologie_http": 200, "partitions": 1, "partitions_saines": 1, "sante": "UP", "sante_http": 200},
        "bridge": {"sante_http": 200},
        "cnpg": {"phase": "Cluster in healthy state", "prets": 2, "instances": 2, "primaire": "postgres-7",
                 "replication": [{"app": "postgres-8", "etat": "streaming", "sync": "async", "retard_octets": 0}], "archiveur": {"echecs": 0}},
        "politiques": {"coherent": True, "code": 0, "ecarts": []},
        "haproxy": {"160": {"http": 200, "proxies": {"k8s_masters": {"serveurs": 3, "up": 3, "down": [], "frontend": "OPEN"}}},
                    "162": {"http": 200, "proxies": {"k8s_masters": {"serveurs": 3, "up": 3, "down": [], "frontend": "OPEN"}}}},
        "sondes_ha01": {"harnais_age_s": 2, "sondes": {"web": {"n": 900, "echecs": 0, "dispo_pct": 100.0, "serie_max_s": 0.0, "p95_ms": 90, "max_ms": 400}}},
        "pods": {"total": 60, "anormaux": [], "redemarrages_delta": [], "restarts_map": {}},
        "evenements_warning": {"fenetre_s": 960, "n": 2, "top": []},
        "incidents": {"par_statut": {}, "nouveaux": [], "resolution_failed": 0, "restart_vm": 0, "doublons": [], "actions": {}},
        "dns": {"helios.orange.tn": {"ip": "192.168.148.160"}}, "erreurs": [], "alertes": [],
    }
    rec.update(over)
    return rec


class CollecteursTest(unittest.TestCase):
    def test_readyz(self):
        with Patch(kube=lambda *a, **k: (0, "[+]ping ok\n[+]etcd ok\nreadyz check passed\n", "")):
            e = []
            r = po.collect_api(e)
            self.assertTrue(r["ok"])
            self.assertEqual([], r["echecs"])
        with Patch(kube=lambda *a, **k: (1, "[+]ping ok\n[-]etcd failed: reason withheld\nreadyz check failed\n", "x")):
            e = []
            r = po.collect_api(e)
            self.assertFalse(r["ok"])
            self.assertEqual(["etcd"], r["echecs"])

    def test_noeuds(self):
        js = {"items": [{"metadata": {"name": "k8s-master"}, "spec": {}, "status": {"conditions": [{"type": "Ready", "status": "True"}, {"type": "DiskPressure", "status": "False"}]}},
                        {"metadata": {"name": "k8s-worker2"}, "spec": {"unschedulable": True}, "status": {"conditions": [{"type": "Ready", "status": "Unknown"}, {"type": "MemoryPressure", "status": "True"}]}}]}
        with Patch(kube=lambda *a, **k: (0, json.dumps(js), "")):
            r = po.collect_nodes([])
        self.assertTrue(r["k8s-master"]["ready"])
        self.assertFalse(r["k8s-worker2"]["ready"])
        self.assertEqual(["MemoryPressure"], r["k8s-worker2"]["pressions"])
        self.assertTrue(r["k8s-worker2"]["cordon"])

    def test_etcd_sante_leader_et_changements(self):
        def fake(url, timeout=6):
            ip = url.split("//")[1].split(":")[0]
            if url.endswith("/health"):
                return 200, '{"health":"true","reason":""}' if not ip.endswith(".158") else '{"health":"false"}', 5
            lead = 1 if ip.endswith(".162") else 0
            return 200, "etcd_server_is_leader %d\netcd_server_leader_changes_seen_total 14\n" % lead, 5
        with Patch(http_get=fake):
            r = po.collect_etcd([])
        self.assertEqual("162", r["leader"])
        self.assertFalse(r["membres"]["158"])
        self.assertTrue(r["membres"]["155"])
        self.assertEqual(14, r["changements_leader_cumules"])

    def test_camunda(self):
        topo = json.dumps({"brokers": [{"partitions": [{"partitionId": 1, "health": "healthy"}]}]})
        with Patch(http_get=lambda url, timeout=6: (200, topo if "topology" in url else '{"status":"UP"}', 3)):
            r = po.collect_camunda([])
        self.assertEqual((1, 1, "UP"), (r["partitions"], r["partitions_saines"], r["sante"]))

    def test_haproxy_csv(self):
        csv_text = ("# pxname,svname,qcur,status\nk8s_masters,FRONTEND,0,OPEN\nk8s_masters,m153,0,UP\nk8s_masters,m155,0,DOWN\n"
                    "k8s_masters,m158,0,UP 1/3\nk8s_masters,BACKEND,0,UP\n")
        r = po.parse_haproxy_csv(csv_text)["k8s_masters"]
        self.assertEqual((3, 2), (r["serveurs"], r["up"]))
        self.assertEqual(["m155:DOWN"], r["down"])
        self.assertEqual("OPEN", r["frontend"])

    def test_sondes_ha01_fenetre_series_et_percentiles(self):
        base = dt.datetime(2026, 11, 6, 20, 0, 0, tzinfo=dt.timezone.utc).timestamp()
        def line(off, ok, ms=50):
            t = dt.datetime.fromtimestamp(base + off, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            return json.dumps({"ts": t, "probe": "web", "ok": ok, "ms": ms})
        lines = [line(-5000, False)]                                   # hors fenêtre : ignoré
        lines += [line(i, True, 40 + i) for i in range(0, 100)]
        lines += [line(i, False) for i in range(100, 108)]             # série de 8 s
        lines += [line(i, True) for i in range(108, 120)]
        out, last = po.analyse_probes(lines, base + 130, 960)
        w = out["web"]
        self.assertEqual(120, w["n"])
        self.assertEqual(8, w["echecs"])
        self.assertEqual(8.0, w["serie_max_s"])
        self.assertAlmostEqual(100 * 112 / 120, w["dispo_pct"], places=2)
        self.assertEqual(base + 119, last)

    def test_pods_anormaux_et_redemarrages(self):
        js = {"items": [
            {"metadata": {"namespace": "pfe", "name": "backend-a"}, "status": {"phase": "Running", "containerStatuses": [{"ready": True, "restartCount": 3}]}},
            {"metadata": {"namespace": "pfe", "name": "postgres-9"}, "status": {"phase": "Running", "containerStatuses": [{"ready": False, "restartCount": 0}]}},
            {"metadata": {"namespace": "pfe", "name": "job-x"}, "status": {"phase": "Succeeded", "containerStatuses": [{"ready": False, "restartCount": 0}]}}]}
        prev = {"pods": {"restarts_map": {"pfe/backend-a": 2, "pfe/postgres-9": 0}}}
        with Patch(kube=lambda *a, **k: (0, json.dumps(js), "")):
            r = po.collect_pods([], prev)
        self.assertEqual([{"pod": "pfe/backend-a", "delta": 1}], r["redemarrages_delta"])
        self.assertEqual(["pfe/postgres-9:Running:non prêt"], r["anormaux"])

    def test_cnpg_replication_archiveur_et_config(self):
        cluster = {"spec": {"instances": 2}, "status": {"phase": "Cluster in healthy state", "readyInstances": 2, "currentPrimary": "postgres-7"}}
        psql = ("repl|postgres-8|streaming|async|0\narch|35|2|120|000000760000005700000090\ncfg|on|logical|/controller/manager wal-archive %p|512MB\ntaille|13500000\nwal|78|620000000\n")
        calls = []
        def fake(*a, **k):
            calls.append(a)
            return (0, json.dumps(cluster), "") if a[0] == "get" else (0, psql, "")
        with Patch(kube=fake):
            r = po.collect_cnpg([])
        self.assertEqual("postgres-7", r["primaire"])
        self.assertEqual([{"app": "postgres-8", "etat": "streaming", "sync": "async", "retard_octets": 0}], r["replication"])
        self.assertEqual(2, r["archiveur"]["echecs"])
        self.assertEqual("on", r["config"]["archive_mode"])
        self.assertEqual({"fichiers": 78, "octets": 620000000}, r["pg_wal"])

    def test_incidents(self):
        out = ("inc_statut|RESOLVED|2\ninc_type|NODE_HIGH_CPU|2\nnouveau|4931:NODE_HIGH_CPU:k8s-nodes/k8s-worker1:RESOLVED\nechec|0\nvm_scope|0\n"
               "action|RESTART_POD:COMPLETED|1\nrestart_vm|0\ndoublon|4931:RESTART_POD|2\nouvert|4821:NODE_TIME_DRIFT:k8s-nodes/monitoring:OPEN\n")
        def fake(*a, **k):
            return (0, "backend-x", "") if a[0] == "get" else (0, out, "")
        with Patch(kube=fake):
            r = po.collect_incidents([], "2026-11-06 22:00:00")
        self.assertEqual({"RESOLVED": 2}, r["par_statut"])
        self.assertEqual(["4931:RESTART_POD x2"], r["doublons"])
        self.assertEqual(1, len(r["ouverts"]))

    def test_politiques(self):
        with Patch(run=lambda *a, **k: (0, "...\nCOHÉRENT : toutes les couches appliquent la politique définitive\n", "")):
            self.assertTrue(po.collect_policies([])["coherent"])
        with Patch(run=lambda *a, **k: (1, "...\nÉCARTS (2) :\n - k8s-monitoring backend AUTO\n", "")):
            r = po.collect_policies([])
            self.assertFalse(r["coherent"])
            self.assertTrue(r["ecarts"])


class VerdictTest(unittest.TestCase):
    def test_nominal_ok(self):
        v, f, w = po.verdict(good_rec())
        self.assertEqual(("OK", [], []), (v, f, w))

    def test_fails(self):
        cases = {
            "api": good_rec(api={"ok": False, "echecs": ["etcd"]}),
            "noeud": good_rec(noeuds={"k8s-worker2": {"ready": False, "pressions": [], "cordon": False}}),
            "etcd": good_rec(etcd={"membres": {"155": False}, "leader": "162"}),
            "etcd leader": good_rec(etcd={"membres": {"155": True}, "leader": None}),
            "camunda": good_rec(camunda={"topologie_http": 200, "partitions": 1, "partitions_saines": 0, "sante": "UP"}),
            "cnpg 1/2": good_rec(cnpg={"phase": "Failing over", "prets": 1, "instances": 2, "replication": []}),
            "retard": good_rec(cnpg={"phase": "Cluster in healthy state", "prets": 2, "instances": 2, "replication": [{"app": "p", "etat": "streaming", "retard_octets": 2000000}], "archiveur": {}}),
            "catchup": good_rec(cnpg={"phase": "Cluster in healthy state", "prets": 2, "instances": 2, "replication": [{"app": "p", "etat": "catchup", "retard_octets": 0}], "archiveur": {}}),
            "politiques": good_rec(politiques={"coherent": False, "ecarts": ["x"]}),
            "haproxy": good_rec(haproxy={"160": {"http": 200, "proxies": {"k8s": {"down": ["m155:DOWN"]}}}, "162": {"http": 200, "proxies": {}}}),
            "série": good_rec(sondes_ha01={"harnais_age_s": 1, "sondes": {"web": {"n": 9, "echecs": 9, "serie_max_s": 45.0}}}),
            "doublon": good_rec(incidents={"doublons": ["4931:RESTART_POD x2"], "restart_vm": 0}),
            "restart_vm": good_rec(incidents={"doublons": [], "restart_vm": 1}),
        }
        for name, rec in cases.items():
            v, f, w = po.verdict(rec)
            self.assertEqual("FAIL", v, name)
            self.assertTrue(f, name)

    def test_warns(self):
        cases = {
            "pods": good_rec(pods={"anormaux": ["pfe/x:Pending:non prêt"], "redemarrages_delta": []}),
            "redémarrage": good_rec(pods={"anormaux": [], "redemarrages_delta": [{"pod": "pfe/backend-a", "delta": 1}]}),
            "sonde": good_rec(sondes_ha01={"harnais_age_s": 1, "sondes": {"web": {"n": 900, "echecs": 3, "serie_max_s": 3.0}}}),
            "harnais muet": good_rec(sondes_ha01={"harnais_age_s": 500, "sondes": {}}),
            "événements": good_rec(evenements_warning={"fenetre_s": 960, "n": 40}),
            "nouvel incident": good_rec(incidents={"nouveaux": ["4931:X"], "doublons": [], "restart_vm": 0}),
            "dns": good_rec(dns={"api.helios.orange.tn": {"ip": None, "erreur": "x"}}),
            "pg_wal gros": good_rec(cnpg={"phase": "Cluster in healthy state", "prets": 2, "instances": 2, "replication": [{"app": "p", "etat": "streaming", "retard_octets": 0}], "archiveur": {"echecs": 0}, "pg_wal": {"fichiers": 400, "octets": 3 * 1024 ** 3}}),
            "archivage": good_rec(cnpg={"phase": "Cluster in healthy state", "prets": 2, "instances": 2, "replication": [{"app": "p", "etat": "streaming", "retard_octets": 0}], "archiveur": {"echecs": 4}}),
            "alerte": good_rec(alertes=["etcd : 1 changement(s) de leader depuis le relevé précédent"]),
            "relevé incomplet": good_rec(erreurs=["cnpg: x"]),
        }
        for name, rec in cases.items():
            v, f, w = po.verdict(rec)
            self.assertEqual("WARN", v, (name, f, w))


class RapportEtJournalTest(unittest.TestCase):
    def recs(self, n, start="2026-11-06T22:00:00+01:00", step=900):
        t0 = dt.datetime.fromisoformat(start)
        out = []
        for i in range(n):
            r = good_rec()
            r["ts"] = (t0 + dt.timedelta(seconds=step * i)).isoformat(timespec="seconds")
            r["verdict"], r["fails"], r["warns"] = po.verdict(r)
            out.append(r)
        return out

    def test_en_cours_jamais_termine_avant_l_heure(self):
        now = dt.datetime.fromisoformat("2026-11-07T01:00:00+01:00")
        txt = po.build_report(self.recs(13), "2026-11-06 22:00:00", "2026-11-07 22:00:00", now=now)
        self.assertIn("Statut : EN COURS", txt)
        self.assertIn("aucun verdict final", txt)
        self.assertIn("NON RENDU", txt)
        self.assertNotIn("TERMINÉE", txt)

    def test_termine_seulement_apres_la_fin_avec_un_releve_final(self):
        now = dt.datetime.fromisoformat("2026-11-07T22:10:00+01:00")
        recs = self.recs(97)                      # 22:00 -> 22:00 J+1
        txt = po.build_report(recs, "2026-11-06 22:00:00", "2026-11-07 22:00:00", now=now)
        self.assertIn("Statut : TERMINÉE", txt)
        self.assertIn("Verdict final : OK", txt)
        # la fin est passée mais plus aucun relevé ne couvre la fin : pas « terminée »
        txt2 = po.build_report(recs[:40], "2026-11-06 22:00:00", "2026-11-07 22:00:00", now=now)
        self.assertIn("Statut : EN COURS", txt2)

    def test_trous_et_anomalies_dedupliquees(self):
        recs = self.recs(3)
        recs[2]["ts"] = "2026-11-06T23:30:00+01:00"          # trou de 1 h
        for r in recs[1:]:
            r["fails"], r["verdict"] = ["ANOMALIE-TEST unique"], "FAIL"
        txt = po.build_report(recs, "2026-11-06 22:00:00", "2026-11-07 22:00:00", now=dt.datetime.fromisoformat("2026-11-07T00:00:00+01:00"))
        self.assertIn("Trous", txt)
        self.assertNotIn("Trous de plus de 22 min entre deux relevés : aucun", txt)
        self.assertEqual(1, txt.count("ANOMALIE-TEST unique"))
        self.assertIn("2 relevé(s)", txt)

    def test_journal_annotations_et_rapport_ecrits_ou_demandes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x", "a.jsonl")
            po.annotate("patch CronJob sauvegardes", path=p)
            po.append_record({"ts": "2026-11-06T22:00:00+01:00", "verdict": "OK"}, path=os.path.join(d, "x", "r.jsonl"))
            po.write_report("# t\n", path=os.path.join(d, "x", "rapport.md"))
            self.assertEqual("patch CronJob sauvegardes", po.load_all(p)[0]["texte"])
            self.assertEqual(1, len(po.load_all(os.path.join(d, "x", "r.jsonl"))))
            with open(os.path.join(d, "x", "rapport.md"), encoding="utf-8") as fh:
                self.assertEqual("# t\n", fh.read())

    def test_un_collecteur_qui_leve_n_interrompt_pas_le_releve(self):
        def boom(*a, **k):
            raise RuntimeError("panne simulée")
        names = ["collect_api", "collect_nodes", "collect_etcd", "collect_camunda", "collect_bridge", "collect_cnpg", "collect_policies",
                 "collect_haproxy", "collect_probes", "collect_pods", "collect_events", "collect_incidents", "collect_dns",
                 "collect_hote", "collect_arrets_vix"]
        with Patch(**{n: boom for n in names}):
            rec = po.snapshot(None, "2026-11-06 22:00:00")
        self.assertEqual("WARN", rec["verdict"])
        self.assertEqual(len(names), len(rec["erreurs"]))


class HoteEtArretsVixTest(unittest.TestCase):
    """Relevés ajoutés le 11 nov. 2026 : arrêts de VM par demande VIX (contamination) et mémoire disponible de l'hôte."""
    BASE = dt.datetime(2026, 11, 7, 10, 0, 0, tzinfo=dt.timezone.utc).timestamp()

    @staticmethod
    def z(t):
        return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def log(self, path, start, end, kind):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = ["%s In(05) vmx Log for VMware Workstation" % self.z(start)]
        if kind == "vix":
            lines += ["%s In(05) vmx Vix: [mainDispatch.c:1171]: VMAutomationPowerOff: Powering off." % self.z(end - 1),
                      "%s In(05) vmx Transitioned vmx/execState/val to poweredOff" % self.z(end - 0.5),
                      "%s In(05) vmx VMX exit (0)." % self.z(end)]
        else:
            lines += ["%s In(05) vmx heartbeat" % self.z(end)]
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")

    def test_arrets_vix_comptes_depuis_le_debut_de_la_fenetre(self):
        with tempfile.TemporaryDirectory() as d:
            b = self.BASE
            self.log(os.path.join(d, "k8s-lb2", "vmware-0.log"), b - 3000, b + 600, "vix")        # arrêté APRÈS le début de la fenêtre
            self.log(os.path.join(d, "k8s-lb2", "vmware.log"), b + 660, b + 9000, "run")
            self.log(os.path.join(d, "k8s-worker1", "vmware-0.log"), b - 9000, b - 5000, "vix")  # arrêté AVANT : ignoré
            self.log(os.path.join(d, "k8s-worker1", "vmware.log"), b - 4900, b + 9000, "run")
            since = dt.datetime.fromtimestamp(b).strftime("%Y-%m-%d %H:%M:%S")
            errors = []
            with Patch(VM_DIR=d, VM_NAMES=("k8s-lb2", "k8s-worker1", "k8s-absente")):
                r = po.collect_arrets_vix(errors, since)
            self.assertEqual(errors, [])
            self.assertEqual(r["n"], 1)
            self.assertEqual(r["evenements"][0]["vm"], "k8s-lb2")
            self.assertIsNotNone(r["evenements"][0]["redemarrage"])

    def test_session_en_cours_n_est_pas_un_arret(self):
        with tempfile.TemporaryDirectory() as d:
            self.log(os.path.join(d, "k8s-lb2", "vmware.log"), self.BASE, self.BASE + 600, "run")
            with Patch(VM_DIR=d, VM_NAMES=("k8s-lb2",)):
                r = po.collect_arrets_vix([], dt.datetime.fromtimestamp(self.BASE).strftime("%Y-%m-%d %H:%M:%S"))
            self.assertEqual(r["n"], 0)

    def test_contamination_et_ram_donnent_des_warn(self):
        ev = [{"vm": "k8s-lb2", "arret": "2026-11-07T14:50:38+01:00", "redemarrage": None}]
        v, f, w = po.verdict(good_rec(arrets_vix={"depuis": "x", "n": 1, "evenements": ev}))
        self.assertEqual("WARN", v)
        self.assertTrue(any("CONTAMINATION" in x for x in w), w)
        v, f, w = po.verdict(good_rec(hote={"ram_disponible_mo": 400}))
        self.assertEqual("WARN", v)
        self.assertTrue(any("RAM disponible" in x for x in w), w)
        self.assertEqual("OK", po.verdict(good_rec(hote={"ram_disponible_mo": 5000}, arrets_vix={"depuis": "x", "n": 0, "evenements": []}))[0])

    def test_un_redemarrage_couvert_par_un_arret_vix_n_est_plus_un_fail(self):
        ev = [{"vm": "k8s-lb2", "arret": "2026-11-07T14:50:38+01:00", "redemarrage": "2026-11-07T14:51:40+01:00"}]
        inc = {"doublons": [], "restart_vm": 1}
        v, f, w = po.verdict(good_rec(incidents=inc, arrets_vix={"depuis": "x", "n": 1, "evenements": ev}))
        self.assertEqual("WARN", v)
        self.assertTrue(any("couverte par un arrêt VIX" in x for x in w), w)
        v, f, w = po.verdict(good_rec(incidents={"doublons": [], "restart_vm": 2}, arrets_vix={"depuis": "x", "n": 1, "evenements": ev}))
        self.assertEqual("FAIL", v)                                                   # un redémarrage de plus que d'arrêts constatés
        self.assertTrue(any(x.startswith("2 action(s) RESTART_VM") for x in f), f)
        v, f, w = po.verdict(good_rec(incidents=inc))
        self.assertEqual("FAIL", v)                                                   # aucun arrêt constaté : toujours un FAIL

    @unittest.skipUnless(sys.platform == "win32", "GlobalMemoryStatusEx : Windows seulement")
    def test_memoire_reelle_de_l_hote(self):
        errors = []
        h = po.collect_hote(errors)
        self.assertEqual(errors, [])
        self.assertGreater(h["ram_disponible_mo"], 0)
        self.assertGreaterEqual(h["ram_total_mo"], h["ram_disponible_mo"])

    def test_le_rapport_signale_la_contamination(self):
        t0 = dt.datetime.fromisoformat("2026-11-06T22:00:00+01:00")
        recs = []
        for i in range(97):
            r = good_rec()
            r["ts"] = (t0 + dt.timedelta(seconds=900 * i)).isoformat(timespec="seconds")
            r["hote"] = {"ram_disponible_mo": 3000 + i}
            if i >= 40:
                r["arrets_vix"] = {"depuis": "2026-11-06 22:00:00", "n": 1, "evenements": [{"vm": "k8s-lb2", "arret": "2026-11-07T08:00:00+01:00", "redemarrage": None}]}
            r["verdict"], r["fails"], r["warns"] = po.verdict(r)
            recs.append(r)
        now = dt.datetime.fromisoformat("2026-11-07T22:10:00+01:00")
        txt = po.build_report(recs, "2026-11-06 22:00:00", "2026-11-07 22:00:00", now=now)
        self.assertIn("Fenêtre CONTAMINÉE", txt)
        self.assertEqual(1, txt.count("Arrêts de VM par demande VIX pendant la fenêtre : k8s-lb2 à 11-07T08:00:00."))   # dédupliqué sur 57 relevés
        self.assertIn("RAM disponible de l'hôte : min 3000 Mo", txt)
        clean = po.build_report([dict(r, arrets_vix={"depuis": "x", "n": 0, "evenements": []}) for r in recs], "2026-11-06 22:00:00", "2026-11-07 22:00:00", now=now)
        self.assertNotIn("Fenêtre CONTAMINÉE", clean)
        self.assertIn("Arrêts de VM par demande VIX pendant la fenêtre : aucun", clean)


if __name__ == "__main__":
    unittest.main()
