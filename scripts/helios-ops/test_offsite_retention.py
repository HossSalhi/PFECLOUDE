# -*- coding: utf-8 -*-
"""Tests de la rétention SÛRE du script de sauvegarde hors cluster (infra/ha01/backup/helios-offsite-backup.sh) : bash local, aucun réseau.

Lancer : python -I scripts/helios-ops/test_offsite_retention.py
"""
import gzip
import hashlib
import os
import re
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "ha01", "backup", "helios-offsite-backup.sh"))
BASH = next((p for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe", "/bin/bash", "/usr/bin/bash") if os.path.exists(p)), None)

with open(SCRIPT, encoding="utf-8") as fh:
    SRC = fh.read()
START = SRC.index("# --- 5. retention SURE")
END = SRC.index('log "terminé rc=$RC"')
BLOCK = SRC[START:END]


def posix(path):
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    return "/%s/%s" % (m.group(1).lower(), m.group(2).replace("\\", "/")) if m else path


def age(p, seconds):
    t = time.time() - seconds
    os.utime(p, (t, t))


def write_unix(path, text):
    """Écrit avec des fins de ligne Unix (sha256sum -c et grep -x refusent un retour chariot)."""
    with open(path, "w", newline="\n") as fh:
        fh.write(text)


def make_pg(base, n, bad_newest=0):
    os.makedirs(os.path.join(base, "pg"), exist_ok=True)
    names = []
    for i in range(n):
        name = "pfe_db-20261001T%06dZ.sql.gz" % i
        p = os.path.join(base, "pg", name)
        rank = n - 1 - i
        if rank >= bad_newest:
            with gzip.open(p, "wb") as fh:
                fh.write(b"dump\n" * 100)
        else:
            with open(p, "wb") as fh:
                fh.write(b"\x1f\x8b tronque")
        age(p, (n - i) * 3600)
        names.append(name)
    return names


def make_etcd(base, n, bad_newest=0):
    os.makedirs(os.path.join(base, "etcd"), exist_ok=True)
    names = []
    for i in range(n):
        name = "etcd-20261001T%06dZ-158.db" % i
        p = os.path.join(base, "etcd", name)
        data = (b"snap%d" % i) * 100
        with open(p, "wb") as fh:
            fh.write(data)
        digest = hashlib.sha256(data).hexdigest()
        if n - 1 - i < bad_newest:
            digest = "0" * 64
        write_unix(p + ".sha256", "%s  %s\n" % (digest, name))
        age(p, (n - i) * 3600)
        age(p + ".sha256", (n - i) * 3600)
        names.append(name)
    return names


@unittest.skipIf(BASH is None, "bash introuvable")
class RetentionSureTest(unittest.TestCase):
    def run_block(self, base, keep=None):
        env = dict(os.environ, BASE=posix(base))
        if keep:
            env["HELIOS_OFFSITE_KEEP"] = str(keep)
        script = "set -uo pipefail\nlog() { echo \"LOG $*\"; }\n" + BLOCK
        p = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=env, timeout=60, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout + p.stderr

    def test_pg_supprime_seulement_au_dela_de_30_et_respecte_protected(self):
        with tempfile.TemporaryDirectory() as d:
            names = make_pg(d, 40)
            write_unix(os.path.join(d, "pg", ".protected"), names[1] + "\n")
            rc, out = self.run_block(d)
            self.assertEqual(0, rc, out)
            reste = sorted(f for f in os.listdir(os.path.join(d, "pg")) if f.endswith(".sql.gz"))
            self.assertIn(names[1], reste)                       # protégé : conservé
            self.assertNotIn(names[0], reste)                    # le plus ancien, non protégé : supprimé
            for n in names[10:]:
                self.assertIn(n, reste)                          # les 30 plus récents sont intacts
            self.assertEqual(31, len(reste))                     # 30 récents + 1 protégé
            self.assertIn("protege", out)

    def test_pg_annulee_si_un_des_5_plus_recents_est_corrompu(self):
        with tempfile.TemporaryDirectory() as d:
            make_pg(d, 40, bad_newest=1)
            rc, out = self.run_block(d)
            self.assertEqual(0, rc, out)
            self.assertEqual(40, len(os.listdir(os.path.join(d, "pg"))))
            self.assertIn("ANNULEE", out)

    def test_etcd_supprime_les_anciens_avec_leur_empreinte_mais_pas_si_la_verification_echoue(self):
        with tempfile.TemporaryDirectory() as d:
            names = make_etcd(d, 40)
            rc, out = self.run_block(d)
            self.assertEqual(0, rc, out)
            reste = os.listdir(os.path.join(d, "etcd"))
            self.assertNotIn(names[0], reste)
            self.assertNotIn(names[0] + ".sha256", reste)
            self.assertIn(names[10], reste)
            self.assertIn(names[10] + ".sha256", reste)
            self.assertEqual(60, len(reste))                     # 30 snapshots + 30 empreintes
        with tempfile.TemporaryDirectory() as d:
            make_etcd(d, 40, bad_newest=1)
            rc, out = self.run_block(d)
            self.assertEqual(40 * 2, len(os.listdir(os.path.join(d, "etcd"))))
            self.assertIn("etcd ANNULEE", out)

    def test_jusqu_a_30_fichiers_rien_n_est_supprime(self):
        with tempfile.TemporaryDirectory() as d:
            make_pg(d, 30)
            make_etcd(d, 30)
            rc, out = self.run_block(d)
            self.assertEqual(0, rc, out)
            self.assertEqual(30, len(os.listdir(os.path.join(d, "pg"))))
            self.assertEqual(60, len(os.listdir(os.path.join(d, "etcd"))))

    def test_dossiers_vides(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "pg"))
            os.makedirs(os.path.join(d, "etcd"))
            rc, out = self.run_block(d)
            self.assertEqual(0, rc, out)


class StructureTest(unittest.TestCase):
    def test_plus_de_suppression_aveugle(self):
        self.assertNotIn("xargs -r rm", SRC)
        self.assertNotIn("tail -n +31", SRC)
        self.assertEqual(2, len(re.findall(r"\brm -f -- ", SRC)))
        # chaque suppression est précédée du contrôle d'intégrité et de la liste .protected
        for fn in ("retention_pg", "retention_etcd"):
            body = SRC[SRC.index(fn + "() {"):]
            body = body[:body.index("\n}\n")]
            self.assertLess(body.index("ANNULEE"), body.index("rm -f --"))
            self.assertLess(body.index(".protected"), body.index("rm -f --"))

    def test_le_script_complet_reste_syntaxiquement_valide_et_lance_la_retention_en_dernier(self):
        self.assertLess(SRC.index("retention_etcd\n"), SRC.index('log "terminé rc=$RC"'))
        if BASH:
            p = subprocess.run([BASH, "-n", SCRIPT], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, p.returncode, p.stderr)


if __name__ == "__main__":
    unittest.main()
