# -*- coding: utf-8 -*-
"""Tests de la sonde externe de disponibilité (aucun accès à HELIOS : serveur HTTP local éphémère). Lancer : python -I scripts/helios-ops/test_avail_probe.py"""
import http.server
import importlib.util
import os
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools", "avail_probe.py"))
spec = importlib.util.spec_from_file_location("avail_probe", TOOL)
ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        code = {"/ok": 200, "/ko": 503, "/interdit": 403}.get(self.path, 404)
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):   # silence
        pass


class ProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_codes_http_et_erreurs_sont_rendus_sans_lever(self):
        base = f"http://127.0.0.1:{self.port}"
        self.assertEqual(ap.probe(base + "/ok")[0], "200")
        self.assertEqual(ap.probe(base + "/ko")[0], "503")
        self.assertEqual(ap.probe(base + "/interdit")[0], "403")
        self.assertEqual(ap.probe(base + "/absent")[0], "404")
        code, ms = ap.probe("http://127.0.0.1:1/ok", timeout=1.0)      # port fermé
        self.assertTrue(code.startswith("ERR:"), code)
        self.assertIsInstance(ms, int)

    def test_les_cibles_couvrent_les_deux_repartiteurs_trois_mesures_chacun(self):
        t = ap.targets()
        self.assertEqual(len(t), 6)
        self.assertEqual({n.split("@")[1] for n, *_ in t}, {"160", "162"})
        self.assertEqual({n.split("@")[0] for n, *_ in t}, {"ui", "backend", "k8s-readyz"})
        for nom, url, host, attendus in t:
            self.assertIn("200", attendus)
            # le backend répond 200 OU 403 selon le pod (les deux = l'application a répondu) ; jamais 502/503/504
            self.assertEqual(attendus, ("200", "403") if nom.startswith("backend") else ("200",))
            self.assertNotIn("503", attendus)
            self.assertEqual(host, None if nom.startswith("k8s-readyz") else "helios.orange.tn")
            self.assertTrue(url.startswith("https://192.168.148.16"))


class ResumeTest(unittest.TestCase):
    @staticmethod
    def rows(spec, debut=1000.0, pas=2.0):
        """spec = {cible: "1110011"} (1 = ok) -> lignes (epoch, cible, ok, ms)."""
        out = []
        for cible, s in spec.items():
            for i, c in enumerate(s):
                out.append((debut + i * pas, cible, c == "1", 20))
        return out

    def test_disponibilite_et_pannes_par_cible(self):
        r = ap.summarize(self.rows({"ui@160": "1110011110", "ui@162": "1111111111"}))
        c = r["cibles"]["ui@160"]
        self.assertEqual(c["n"], 10)
        self.assertAlmostEqual(c["ok_pct"], 70.0)                # « 1110011110 » : 7 mesures OK sur 10
        self.assertEqual(len(c["pannes"]), 2)                    # deux échecs consécutifs, puis un échec isolé en fin de série
        self.assertAlmostEqual(c["plus_longue_s"], 4.0)          # 2 échecs consécutifs : (1008-1006)+2 s ; l'échec isolé dure 2 s
        self.assertEqual(r["cibles"]["ui@162"]["pannes"], [])
        self.assertEqual(r["cibles"]["ui@162"]["ok_pct"], 100.0)

    def test_une_panne_en_cours_en_fin_de_serie_est_comptee(self):
        r = ap.summarize(self.rows({"ui@160": "1100"}))
        self.assertEqual(len(r["cibles"]["ui@160"]["pannes"]), 1)
        self.assertAlmostEqual(r["cibles"]["ui@160"]["plus_longue_s"], 4.0)

    def test_le_service_est_disponible_si_un_repartiteur_sert_interface_et_backend(self):
        # .160 tombe entièrement pendant 3 cycles : .162 tient le service => 100 %
        r = ap.summarize(self.rows({"ui@160": "1000111", "backend@160": "1000111", "ui@162": "1111111", "backend@162": "1111111"}))
        self.assertEqual(r["service"]["ok_pct"], 100.0)
        self.assertEqual(r["service"]["pannes"], [])
        self.assertLess(r["cibles"]["ui@160"]["ok_pct"], 100.0)

    def test_le_service_tombe_si_aucun_repartiteur_ne_sert_les_deux(self):
        # .160 : interface OK mais backend KO ; .162 : interface KO ; => aucun répartiteur ne sert interface ET backend sur les cycles 1-2
        r = ap.summarize(self.rows({"ui@160": "1111", "backend@160": "1001", "ui@162": "1001", "backend@162": "1111"}))
        s = r["service"]
        self.assertAlmostEqual(s["ok_pct"], 50.0)
        self.assertEqual(len(s["pannes"]), 1)
        self.assertAlmostEqual(s["plus_longue_s"], 4.0)

    def test_serie_vide_ne_leve_pas(self):
        r = ap.summarize([])
        self.assertEqual(r["cibles"], {})
        self.assertEqual(r["service"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
