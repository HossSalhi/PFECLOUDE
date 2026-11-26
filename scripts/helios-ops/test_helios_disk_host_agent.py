# -*- coding: utf-8 -*-
"""Tests unitaires HTTP/HMAC de l'agent DISK Windows. Loopback uniquement, aucun acces HELIOS reel."""
import collections
import hashlib
import hmac
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import HTTPServer
from unittest import mock

import helios_disk_host_agent as A


class TestProtocoleHmac(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key_path = os.path.join(self.tmp.name, "key")
        self.key = bytes(range(32))
        with open(self.key_path, "wb") as f:
            f.write(self.key.hex().encode("ascii"))

    def _serve(self, collecteur):
        handler = A.creer_handler(self.key_path, collecteur)
        server = HTTPServer(("127.0.0.1", 0), handler)
        server.nonces = collections.deque(maxlen=A.MAX_NONCES)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        # unittest execute les cleanups en LIFO : shutdown doit preceder join.
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def _post(self, server, nonce=None, valid_signature=True, payload=None):
        ts = int(time.time())
        nonce = nonce or ("a" * 31 + "1")
        req_obj = payload or {"contract": A.CONTRAT, "target": A.CIBLE, "scenario": A.SCENARIO,
                              "image_backend": A.IMAGE_BACKEND, "timestamp": ts, "nonce": nonce}
        body = json.dumps(req_obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        sig = A.signature(self.key, "REQ", ts, nonce, body)
        if not valid_signature:
            sig = "0" * 64
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        conn.request("POST", A.CHEMIN, body=body, headers={
            "Content-Type": "application/json", "Content-Length": str(len(body)),
            "X-Helios-Time": str(ts), "X-Helios-Nonce": nonce, "X-Helios-Signature": sig})
        response = conn.getresponse()
        raw = response.read()
        headers = dict(response.getheaders())
        status = response.status
        conn.close()
        return status, raw, headers, ts, nonce

    def test_attestation_verte_est_signee_et_lisible(self):
        result = {"verdict": "VERT", "go": True, "reasons": [{"level": "INFO", "code": "OK"}],
                  "host": {"ram_available_mo": 3000}}
        server = self._serve(lambda: result)
        with mock.patch.object(A, "APPELANT_AUTORISE", "127.0.0.1"):
            status, raw, headers, ts, nonce = self._post(server)
        self.assertEqual(status, 200)
        obj = json.loads(raw.decode("ascii"))
        self.assertEqual(obj["verdict"], "VERT")
        self.assertIs(obj["go"], True)
        self.assertEqual(obj["request_timestamp"], ts)
        self.assertEqual(obj["request_nonce"], nonce)
        attendu = A.signature(self.key, "RESP", ts, nonce, raw)
        self.assertTrue(hmac.compare_digest(headers["X-Helios-Signature"], attendu))

    def test_orange_est_retourne_non_autorisant_meme_si_collecteur_mal_forme_go_true(self):
        result = {"verdict": "VERT", "go": True, "reasons": [{"level": "ORANGE", "code": "GEL_HOTE_48H"}]}
        server = self._serve(lambda: result)
        with mock.patch.object(A, "APPELANT_AUTORISE", "127.0.0.1"):
            status, raw, headers, ts, nonce = self._post(server)
        obj = json.loads(raw.decode("ascii"))
        self.assertEqual(status, 200)
        self.assertEqual(obj["verdict"], "ROUGE")
        self.assertIs(obj["go"], False)
        self.assertTrue(hmac.compare_digest(headers["X-Helios-Signature"], A.signature(self.key, "RESP", ts, nonce, raw)))

    def test_orange_autorise_est_retourne_signe_et_autorisant(self):
        result = {"verdict": "ORANGE", "go": True,
                  "reasons": [{"level": "ORANGE", "code": "GEL_HOTE_48H"}],
                  "host": {"ram_available_mo": 3000}}
        server = self._serve(lambda: result)
        with mock.patch.object(A, "APPELANT_AUTORISE", "127.0.0.1"):
            status, raw, headers, ts, nonce = self._post(server)
        obj = json.loads(raw.decode("ascii"))
        self.assertEqual(status, 200)
        self.assertEqual(obj["verdict"], "ORANGE")
        self.assertIs(obj["go"], True)
        self.assertTrue(hmac.compare_digest(headers["X-Helios-Signature"], A.signature(self.key, "RESP", ts, nonce, raw)))

    def test_orange_non_autorise_est_converti_en_rouge(self):
        result = {"verdict": "ORANGE", "go": True,
                  "reasons": [{"level": "ORANGE", "code": "NOTREADY_2H"}]}
        server = self._serve(lambda: result)
        with mock.patch.object(A, "APPELANT_AUTORISE", "127.0.0.1"):
            status, raw, _headers, _ts, _nonce = self._post(server)
        obj = json.loads(raw.decode("ascii"))
        self.assertEqual(status, 200)
        self.assertEqual(obj["verdict"], "ROUGE")
        self.assertIs(obj["go"], False)

    def test_signature_invalide_est_rejetee(self):
        server = self._serve(lambda: {"verdict": "VERT", "go": True, "reasons": []})
        with mock.patch.object(A, "APPELANT_AUTORISE", "127.0.0.1"):
            status, raw, _headers, _ts, _nonce = self._post(server, valid_signature=False)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(raw.decode("ascii"))["status"], "AUTH_FAILED")

    def test_nonce_rejoue_est_rejete(self):
        server = self._serve(lambda: {"verdict": "VERT", "go": True, "reasons": [{"level": "INFO"}]})
        nonce = "b" * 32
        with mock.patch.object(A, "APPELANT_AUTORISE", "127.0.0.1"):
            self.assertEqual(self._post(server, nonce=nonce)[0], 200)
            status, raw, _headers, _ts, _nonce = self._post(server, nonce=nonce)
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(raw.decode("ascii"))["status"], "REPLAY_REJECTED")

    def test_un_appelant_non_autorise_est_rejete(self):
        server = self._serve(lambda: self.fail("le collecteur ne doit pas etre appele"))
        with mock.patch.object(A, "APPELANT_AUTORISE", "192.0.2.99"):
            status, raw, _headers, _ts, _nonce = self._post(server)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(raw.decode("ascii"))["status"], "CALLER_NOT_ALLOWED")

    def test_une_route_get_est_interdite(self):
        server = self._serve(lambda: self.fail("aucun collecteur pour GET"))
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        conn.request("GET", "/health")
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        self.assertEqual(response.status, 405)
        self.assertEqual(json.loads(raw.decode("ascii"))["status"], "METHOD_NOT_ALLOWED")


# Tests du client Linux : le contrat, le HMAC et la fraicheur doivent etre verifies avant acceptation.
import pathlib
import stat
import sys
from types import SimpleNamespace

REPO = pathlib.Path(__file__).resolve().parents[2]
DISKFILL_DIR = REPO / "infra" / "helios-sot" / "diskfill"
if str(DISKFILL_DIR) not in sys.path:
    sys.path.insert(0, str(DISKFILL_DIR))
import diskfill as D  # noqa: E402


class TestContratIdentique(unittest.TestCase):
    def test_contrat_agent_et_client_linux_identique(self):
        self.assertEqual(A.CONTRAT, D.AGENT_HOTE_CONTRAT)
        self.assertEqual(A.CIBLE, D.AGENT_HOTE_CIBLE)
        self.assertEqual(A.SCENARIO, D.AGENT_HOTE_SCENARIO)
        self.assertEqual(A.IMAGE_BACKEND, D.AGENT_HOTE_IMAGE)
        self.assertEqual(A.BIND, "192.168.148.1")
        self.assertEqual(A.PORT, 19158)
        self.assertEqual(A.APPELANT_AUTORISE, "192.168.148.158")
        self.assertEqual(D.URL_AGENT_HOTE, "http://192.168.148.1:19158/v1/preflight")


class _FausseReponse(object):
    def __init__(self, brut, headers):
        self.status = 200
        self.headers = headers
        self._brut = brut

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, taille=-1):
        return self._brut if taille < 0 else self._brut[:taille]


class TestClientLinuxAttestation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key = bytes(range(32))
        self.key_path = os.path.join(self.tmp.name, "host-agent.key")
        with open(self.key_path, "wb") as f:
            f.write(self.key.hex().encode("ascii"))
        self.old_key_path = D.F_CLE_AGENT_HOTE
        D.F_CLE_AGENT_HOTE = self.key_path
        self.addCleanup(setattr, D, "F_CLE_AGENT_HOTE", self.old_key_path)
        self.now = time.time()

    def _opener(self, verdict="VERT", go=True, reasons=None, age=0, bad_signature=False, wrong_target=False):
        test = self

        class Opener(object):
            def open(self, req, timeout):
                ts = int(req.get_header("X-helios-time"))
                nonce = req.get_header("X-helios-nonce")
                obj = {
                    "contract": D.AGENT_HOTE_CONTRAT,
                    "target": "155" if wrong_target else D.AGENT_HOTE_CIBLE,
                    "scenario": D.AGENT_HOTE_SCENARIO,
                    "image_backend": D.AGENT_HOTE_IMAGE,
                    "request_timestamp": ts,
                    "request_nonce": nonce,
                    "generated_epoch": test.now - age,
                    "verdict": verdict,
                    "go": go,
                    "reasons": reasons if reasons is not None else [{"level": "INFO", "code": "OK"}],
                    "host": {"ram_available_mo": 3000},
                }
                brut = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
                sig = A.signature(test.key, "RESP", ts, nonce, brut)
                if bad_signature:
                    sig = "0" * 64
                return _FausseReponse(brut, {"X-Helios-Signature": sig})

        return Opener()

    def _fake_file_metadata(self):
        p1 = mock.patch.object(D.os, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0))
        p2 = mock.patch.object(D.os, "fstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1))
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _call(self, **kwargs):
        self._fake_file_metadata()
        opener = self._opener(**kwargs)
        patcher = mock.patch.object(D.urllib.request, "build_opener", return_value=opener)
        patcher.start()
        self.addCleanup(patcher.stop)
        clock = mock.patch.object(D.time, "time", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        return D.MondeReel().preflight_hote()

    def test_le_client_linux_accepte_uniquement_une_attestation_verte_fraiche_signee(self):
        obj = self._call()
        self.assertEqual(obj["verdict"], "VERT")
        self.assertEqual(obj["target"], "158")

    def test_le_client_linux_refuse_orange_et_rouge(self):
        with self.assertRaises(D.RefusSecurite):
            self._call(verdict="ORANGE", go=False, reasons=[{"level": "ORANGE", "code": "GEL_HOTE_48H"}])

    def test_le_client_linux_refuse_signature_invalide(self):
        with self.assertRaises(D.ErreurNoeud):
            self._call(bad_signature=True)

    def test_le_client_linux_refuse_attestation_perimee_ou_mauvaise_cible(self):
        with self.assertRaises(D.ErreurNoeud):
            self._call(age=D.AGENT_HOTE_FRAICHEUR_S + 1)
        with self.assertRaises(D.ErreurNoeud):
            self._call(wrong_target=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
