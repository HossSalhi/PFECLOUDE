# -*- coding: utf-8 -*-
"""Tests de infra/helios-sot/tools/classify_observation.py (fichiers synthétiques : aucun réseau, aucune VM, aucun cluster).

Lancer : python -I scripts/helios-ops/test_classify_observation.py
"""
import ast
import argparse
import datetime as dt
import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools", "classify_observation.py"))
spec = importlib.util.spec_from_file_location("classify_observation", TOOL)
co = importlib.util.module_from_spec(spec)
spec.loader.exec_module(co)

BASE = dt.datetime(2026, 11, 7, 10, 0, 0, tzinfo=dt.timezone.utc).timestamp()


def z(t, ms=True):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + (".000Z" if ms else "Z")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def vmlog(start, end, kind):
    lines = ["%s In(05) vmx Log for VMware Workstation pid=1" % z(start)]
    lines += ["%s In(05) vmx filler %d" % (z(start + 1 + i), i) for i in range(5)]
    if kind == "VIX":
        lines += ["%s In(05) vmx Vix: [mainDispatch.c:1171]: VMAutomationPowerOff: Powering off." % z(end - 0.6),
                  "%s In(05) vmx Transitioned vmx/execState/val to poweredOff" % z(end - 0.2),
                  "%s In(05) vmx VMX exit (0)." % z(end)]
    elif kind == "GUEST":
        lines += ["%s In(05) vmx Transitioned vmx/execState/val to poweredOff" % z(end - 0.2), "%s In(05) vmx VMX exit (0)." % z(end)]
    else:
        lines += ["%s In(05) vmx heartbeat" % z(end)]
    return "\n".join(lines) + "\n"


class SessionsTest(unittest.TestCase):
    def test_horodatage_vmware(self):
        self.assertAlmostEqual(co.vm_ts("2026-11-07T10:00:00.500Z In(05) vmx x"), BASE + 0.5, places=3)
        self.assertIsNone(co.vm_ts("pas un horodatage"))

    def test_session_arretee_par_vix(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "vmware-1.log")
            write(p, vmlog(BASE, BASE + 600, "VIX"))
            s = co.read_session(p, "k8s-lb2")
            self.assertEqual(s["end_kind"], "VIX_POWEROFF")
            self.assertAlmostEqual(s["start"], BASE, places=2)
            self.assertAlmostEqual(s["end"], BASE + 600, places=2)

    def test_arret_invite_n_est_pas_vix(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "vmware-1.log")
            write(p, vmlog(BASE, BASE + 600, "GUEST"))
            self.assertEqual(co.read_session(p, "k8s-lb2")["end_kind"], "ARRET_NON_VIX")

    def test_session_en_cours(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "vmware.log")
            write(p, vmlog(BASE, BASE + 600, "RUN"))
            self.assertEqual(co.read_session(p, "k8s-lb2")["end_kind"], "EN_COURS_OU_INTERROMPU")

    def test_arrets_entre_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "k8s-lb2", "vmware-0.log"), vmlog(BASE, BASE + 600, "VIX"))
            write(os.path.join(d, "k8s-lb2", "vmware.log"), vmlog(BASE + 660, BASE + 9000, "RUN"))
            ev = co.stop_events(co.vm_sessions(d, vms=("k8s-lb2", "k8s-absente")))
            self.assertEqual(len(ev), 1)
            self.assertEqual(ev[0]["vm"], "k8s-lb2")
            self.assertAlmostEqual(ev[0]["stop"], BASE + 600, places=2)
            self.assertAlmostEqual(ev[0]["restart"], BASE + 660, places=2)
            self.assertEqual(ev[0]["kind"], "VIX_POWEROFF")


class WatchdogTest(unittest.TestCase):
    def test_seules_les_decisions_STARTED_comptent(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "w.log")
            rows = [
                {"event": "READY", "ts": "2026-11-07T09:00:00.000Z"},
                {"caller": "192.168.148.1", "decision": "CALLER_NOT_ALLOWED", "http": 403, "ts": "2026-11-07T09:01:00.000Z"},
                {"event": "VMRUN_START", "rc": 0, "ts": "2026-11-07T10:10:00.000Z", "vm": "k8s-lb2"},
                {"caller": "192.168.148.155", "decision": "STARTED", "http": 200, "incident": 4936, "request_id": "r1",
                 "state_before": "POWERED_OFF", "ts": "2026-11-07T10:10:01.000Z", "vm": "k8s-lb2"},
            ]
            write(p, "ligne non JSON\n" + "\n".join(json.dumps(r) for r in rows) + "\n")
            out = co.watchdog_starts(p)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["vm"], "k8s-lb2")
            self.assertEqual(out[0]["incident"], 4936)
            self.assertAlmostEqual(out[0]["ts"], BASE + 601, places=2)
        self.assertEqual(co.watchdog_starts(os.path.join(tempfile.gettempdir(), "absent-xyz.log")), [])


class EventsTest(unittest.TestCase):
    def start(self, vm, t, inc, before="POWERED_OFF"):
        return {"vm": vm, "ts": t, "incident": inc, "state_before": before, "caller": "192.168.148.155"}

    def test_appariement_avec_arret_vix(self):
        stops = [{"vm": "k8s-lb2", "stop": BASE + 100, "restart": BASE + 160, "kind": "VIX_POWEROFF", "source": "vmware-1.log"}]
        ev, extra = co.provoked_events(stops, [self.start("k8s-lb2", BASE + 166, 1)])
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]["stop_known"])
        self.assertEqual(ev[0]["stop"], BASE + 100)
        self.assertEqual(extra, [])

    def test_journal_roule_utilise_la_date_de_l_incident(self):
        ev, _ = co.provoked_events([], [self.start("k8s-lb2", BASE + 1000, 7)], incident_created={7: BASE + 400})
        self.assertFalse(ev[0]["stop_known"])
        self.assertEqual(ev[0]["stop"], BASE + 400 - co.INCIDENT_LEAD_S)

    def test_journal_roule_sans_incident(self):
        ev, _ = co.provoked_events([], [self.start("k8s-lb2", BASE + 1000, 7)])
        self.assertEqual(ev[0]["stop"], BASE + 1000 - co.UNKNOWN_STOP_S)

    def test_demarrage_sans_arret_prouve_est_signale(self):
        ev, extra = co.provoked_events([], [self.start("k8s-lb2", BASE + 1000, 7, before="RUNNING")])
        self.assertEqual(ev, [])
        self.assertEqual(len(extra), 1)

    def test_arret_vix_redemarre_hors_watchdog(self):
        stops = [{"vm": "k8s-master", "stop": BASE + 10, "restart": BASE + 70, "kind": "VIX_POWEROFF", "source": "vmware-0.log"}]
        ev, _ = co.provoked_events(stops, [])
        self.assertEqual(len(ev), 1)
        self.assertIsNone(ev[0]["incident"])
        self.assertIn("hors watchdog", ev[0]["source"])

    def test_arret_non_vix_n_est_pas_provoque(self):
        stops = [{"vm": "k8s-master", "stop": BASE + 10, "restart": BASE + 70, "kind": "ARRET_NON_VIX", "source": "vmware-0.log"}]
        ev, _ = co.provoked_events(stops, [])
        self.assertEqual(ev, [])

    def test_fenetres(self):
        events = [{"vm": "a", "stop": BASE + 100, "start": BASE + 200}, {"vm": "b", "stop": BASE + 250, "start": BASE + 300}]
        w = co.impact_windows(events, BASE, BASE + 5000)
        self.assertEqual(len(w), 1)                       # fusionnées
        self.assertEqual(w[0][0], BASE + 100 - co.BEFORE_S)
        self.assertEqual(w[0][1], BASE + 300 + co.AFTER_S)
        m = co.manipulation_window(events, BASE, BASE + 5000)
        self.assertEqual(m, (BASE + 100 - co.BEFORE_S, BASE + 300 + co.MANIP_MARGIN_S))
        self.assertIsNone(co.manipulation_window([], BASE, BASE + 5000))
        self.assertEqual(co.impact_windows([{"vm": "a", "stop": BASE - 9000, "start": BASE - 8000}], BASE, BASE + 5000), [])


class ProbesTest(unittest.TestCase):
    def make_file(self, d, fails):
        p = os.path.join(d, "probe.jsonl")
        lines = []
        for step in range(2160):                          # 3 h, une mesure toutes les 5 s, 2 sondes
            t = BASE + 5 * step
            for name in ("web", "kube_api"):
                ok = not (name in fails and step in fails[name])
                lines.append(json.dumps({"ts": z(t), "probe": name, "ok": ok, "status": 200 if ok else None, "ms": 40, "ip": "x", "err": None if ok else "E"}))
        write(p, "\n".join(lines) + "\n")
        return p

    def test_series_et_dichotomie(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.make_file(d, {"web": set(range(100, 103)) | set(range(1500, 1506)) | set(range(2158, 2160)), "kube_api": {1700}})
            t0 = BASE + 7200                               # début de la fenêtre : step 1440
            s = co.probe_series(p, t0, BASE + 10800)
            web = s["web"]
            self.assertFalse(any(x["start"] < BASE + 5 * 1200 for x in web))          # la série du step 100 est hors lecture
            ser = [x for x in web if x["n"] == 6][0]
            self.assertEqual(ser["dur_s"], 26.0)                                        # (6-1) x 5 s + 1 s
            self.assertEqual(ser["start"], BASE + 5 * 1500)
            self.assertTrue(any(x["n"] == 2 for x in web))                              # série ouverte en fin de fichier, fermée
            self.assertEqual(s["kube_api"][0]["n"], 1)

    def test_un_succes_ferme_la_serie(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.make_file(d, {"web": {1500, 1501, 1503, 1504}})
            s = co.probe_series(p, BASE + 7200, BASE + 10800)["web"]
            self.assertEqual(sorted(x["n"] for x in s), [2, 2])


class ClassementTest(unittest.TestCase):
    def ctx(self, **over):
        c = {"impact": [(BASE + 100, BASE + 400)], "manip": (BASE + 100, BASE + 9000), "stalls": [(BASE + 20000, BASE + 20060)],
             "series": {}, "events": [{"vm": "k8s-lb2", "stop": BASE + 100, "start": BASE + 200}], "t0": BASE}
        c.update(over)
        return c

    def test_precedence_A_B_C_D(self):
        c = self.ctx()
        self.assertEqual(co.categorize(BASE + 150, BASE + 160, c)[0], co.CAT_A)
        self.assertEqual(co.categorize(BASE + 5000, BASE + 5010, c)[0], co.CAT_B)
        self.assertEqual(co.categorize(BASE + 20010, BASE + 20020, c)[0], co.CAT_C)
        self.assertEqual(co.categorize(BASE + 50, BASE + 60, c)[0], co.CAT_D)
        self.assertEqual(co.categorize(BASE + 20010, BASE + 20020, self.ctx(stalls=None))[0], co.CAT_D)   # Prometheus muet : pas d'exemption

    def test_le_gel_est_signale_meme_si_la_categorie_est_A(self):
        c = self.ctx(stalls=[(BASE + 120, BASE + 130)])
        self.assertEqual(co.categorize(BASE + 150, BASE + 160, c), (co.CAT_A, True))

    def test_item_serie_retrouvee_par_duree(self):
        ts = BASE + 1000
        series = {"kube_api": [{"start": BASE + 700, "end": BASE + 731.4, "n": 7, "dur_s": 32.4}, {"start": BASE + 900, "end": BASE + 905, "n": 2, "dur_s": 6.0}]}
        a, b, kind = co.item_interval("sonde kube_api : série d'échecs de 32.4 s", ts, series)
        self.assertEqual(kind, "serie")
        self.assertAlmostEqual(a, BASE + 695, places=1)
        a, b, kind = co.item_interval("sonde kube_api : série d'échecs de 90.0 s", ts, series)
        self.assertEqual(kind, "serie_non_retrouvee")
        self.assertEqual((a, b), (ts - co.RECORD_WINDOW_S, ts))

    def test_compteur_de_redemarrages(self):
        ctx = self.ctx()
        base_rec = {"ts": z(BASE + 500, ms=False).replace("Z", "+00:00"), "verdict": "FAIL"}
        ok = co.classify_record(dict(base_rec, fails=["1 action(s) RESTART_VM depuis le début de la fenêtre (aucun arrêt provoqué : analyse requise)"]), ctx)
        self.assertEqual(ok["items"][0]["cat"], co.CAT_A)
        excess = co.classify_record(dict(base_rec, fails=["2 action(s) RESTART_VM depuis le début de la fenêtre (aucun arrêt provoqué : analyse requise)"]), ctx)
        self.assertEqual(excess["items"][0]["cat"], co.CAT_D)          # un redémarrage de plus que d'arrêts prouvés = non expliqué
        wd = co.classify_record(dict(base_rec, fails=["3 `vmrun start` exécuté(s) par le watchdog depuis le début de la fenêtre"]), ctx)
        self.assertEqual(wd["items"][0]["cat"], co.CAT_D)

    def test_synthese_decroissante(self):
        ctx = self.ctx()
        def rec(dt_s, fails):
            return {"ts": z(BASE + dt_s, ms=False).replace("Z", "+00:00"), "verdict": "FAIL", "fails": fails}
        recs = [rec(150, ["nœud k8s-worker1 NotReady"]),            # A
                rec(5000, ["nœud k8s-worker1 NotReady"]),           # B
                rec(20040, ["CNPG : aucune réplique connectée"]),   # C
                rec(30000, ["CNPG : aucune réplique connectée"]),   # D
                {"ts": z(BASE + 40, ms=False).replace("Z", "+00:00"), "verdict": "OK", "fails": []}]
        s = co.summarize([co.classify_record(r, ctx) for r in recs])
        self.assertEqual(s["FAIL brut"], 4)
        self.assertEqual((s["FAIL hors A"], s["FAIL hors A+B"], s["FAIL hors A+B+C (non expliqués)"]), (3, 2, 1))
        self.assertGreaterEqual(s["FAIL brut"], s["FAIL hors A"])
        self.assertGreaterEqual(s["FAIL hors A"], s["FAIL hors A+B"])
        self.assertGreaterEqual(s["FAIL hors A+B"], s["FAIL hors A+B+C (non expliqués)"])

    def test_fenetres_de_gel(self):
        calls = []
        def fetch(q, a, b, step):
            calls.append(q)
            return [{"values": [[BASE + 100, "3"], [BASE + 105, "4"], [BASE + 110, "2"], [BASE + 500, "3"], [BASE + 700, "5"]]}]
        w = co.stall_windows(fetch, "EXPR", BASE, BASE + 3600)
        self.assertEqual([(x["start"], x["end"], x["members"]) for x in w], [(BASE + 100 - co.LOOKBACK_S, BASE + 105, 4), (BASE + 500 - co.LOOKBACK_S, BASE + 500, 3), (BASE + 700 - co.LOOKBACK_S, BASE + 700, 5)])
        self.assertTrue(calls[0].startswith("count(EXPR > 1.0)"))
        def boom(*a):
            raise OSError("Prometheus injoignable")
        self.assertIsNone(co.stall_windows(boom, "EXPR", BASE, BASE + 3600))
        self.assertEqual(co.host_stalls(BASE, BASE + 3600, fetch=boom), (None, None, None))

    def test_verdict_jamais_valide(self):
        s_ok = {"FAIL hors A+B+C (non expliqués)": 0}
        t = co.verdict_text(s_ok, 8, True)
        self.assertTrue(t.startswith("FENÊTRE CONTAMINÉE"))
        self.assertIn("NON VALIDÉE", t)
        t = co.verdict_text({"FAIL hors A+B+C (non expliqués)": 2}, 0, True)
        self.assertTrue(t.startswith("NON VALIDÉ"))
        t = co.verdict_text(s_ok, 0, False)
        self.assertIn("aucun verdict VALIDÉ", t)
        self.assertIn("NON ÉVALUÉS", t)


class BoutEnBoutTest(unittest.TestCase):
    def test_run_et_rendu(self):
        with tempfile.TemporaryDirectory() as d:
            t0 = BASE
            vm_dir = os.path.join(d, "vms")
            write(os.path.join(vm_dir, "k8s-lb2", "vmware-0.log"), vmlog(BASE - 1000, BASE + 600, "VIX"))
            write(os.path.join(vm_dir, "k8s-lb2", "vmware.log"), vmlog(BASE + 660, BASE + 90000, "RUN"))
            wd = os.path.join(d, "wd.log")
            write(wd, json.dumps({"caller": "192.168.148.155", "decision": "STARTED", "http": 200, "incident": 11, "request_id": "r", "state_before": "POWERED_OFF",
                                  "ts": z(BASE + 666), "vm": "k8s-lb2"}) + "\n")
            stab = os.path.join(d, "stab.jsonl")
            recs = [{"ts": dt.datetime.fromtimestamp(BASE + 700, dt.timezone.utc).isoformat(), "verdict": "FAIL", "fails": ["nœud k8s-worker1 NotReady"]},
                    {"ts": dt.datetime.fromtimestamp(BASE + 3600, dt.timezone.utc).isoformat(), "verdict": "WARN", "fails": []}]
            write(stab, "\n".join(json.dumps(r) for r in recs) + "\n")
            armed = os.path.join(d, "armed.jsonl")
            write(armed, json.dumps({"ts": dt.datetime.fromtimestamp(BASE + 300, dt.timezone.utc).isoformat(), "verdict": "WARN", "fails": [], "hote": {"ram_dispo_mo": 4200}}) + "\n")
            since = dt.datetime.fromtimestamp(t0).strftime("%Y-%m-%d %H:%M:%S")
            until = dt.datetime.fromtimestamp(t0 + 7200).strftime("%Y-%m-%d %H:%M:%S")
            out_md, out_js = os.path.join(d, "r.md"), os.path.join(d, "r.json")
            args = argparse.Namespace(since=since, until=until, stab=stab, armed=armed, watchdog_log=wd, vm_dir=vm_dir, probes=os.path.join(d, "absent.jsonl"),
                                      incidents_json=None, no_prometheus=True, out=out_md, json_out=out_js)
            res = co.run(args)
            self.assertEqual(len(res["evenements_provoques"]), 1)
            self.assertEqual(res["stabilite"]["synthese"]["FAIL brut"], 1)
            self.assertEqual(res["stabilite"]["synthese"]["FAIL hors A"], 0)
            self.assertIsNone(res["gels_hote"])
            self.assertEqual(res["ram_hote_mo"]["min_mo"], 4200)
            text = co.render(res)
            self.assertIn("FENÊTRE CONTAMINÉE", text)
            self.assertIn("NON ÉVALUÉS", text)
            self.assertIn("SANS arrêt prouvé (superflus) : **0**", text)
            self.assertEqual(co.main(["--since", since, "--until", until, "--stab", stab, "--armed", armed, "--watchdog-log", wd, "--vm-dir", vm_dir,
                                      "--probes", os.path.join(d, "absent.jsonl"), "--no-prometheus", "--out", out_md, "--json-out", out_js]), 0)
            self.assertIn("FENÊTRE CONTAMINÉE", open(out_md, encoding="utf-8").read())
            self.assertEqual(json.load(open(out_js, encoding="utf-8"))["stabilite"]["synthese"]["FAIL brut"], 1)


class PassiviteTest(unittest.TestCase):
    """L'outil est passif PAR CONSTRUCTION : pas de sous-processus, pas de socket brute, écriture seulement via write_text, GET seulement."""
    @classmethod
    def setUpClass(cls):
        with open(TOOL, "r", encoding="utf-8") as fh:
            cls.src = fh.read()
        cls.tree = ast.parse(cls.src)

    def test_aucun_sous_processus_ni_commande(self):
        imported = set()
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                imported.add((n.module or "").split(".")[0])
        for bad in ("subprocess", "socket", "ctypes", "shutil", "requests", "http"):
            self.assertNotIn(bad, imported)
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id == "os":
                self.assertNotIn(n.func.attr, ("system", "popen", "remove", "unlink", "rmdir", "rename", "replace", "kill", "startfile", "makedirs"))

    def test_ecriture_seulement_dans_write_text(self):
        for fn in [n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]:
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open":
                    mode = ""
                    if len(n.args) > 1 and isinstance(n.args[1], ast.Constant):
                        mode = str(n.args[1].value)
                    for kw in n.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = str(kw.value.value)
                    if any(c in mode for c in "wax+"):
                        self.assertEqual(fn.name, "write_text")

    def test_reseau_get_seulement(self):
        calls = [n for n in ast.walk(self.tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "urlopen"]
        self.assertEqual(len(calls), 1)                                             # un seul site : prom_range
        self.assertFalse({k.arg for k in calls[0].keywords} & {"data", "method"})   # pas de corps de requête => GET
        self.assertNotIn("Request(", self.src)
        self.assertNotIn("POST", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
