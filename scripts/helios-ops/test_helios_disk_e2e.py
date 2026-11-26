# -*- coding: utf-8 -*-
"""DISK-E2E-158-01 - tests locaux du scenario DISK_E2E_158 (helios_disk_e2e.py).

Lancer : python -B -m unittest test_helios_disk_e2e -v          (depuis scripts/helios-ops)

AUCUN acces reel : tous les tests tournent sous une garde qui interdit tout sous-processus (donc tout ssh / kubectl) et toute connexion
reseau hors de la boucle locale (le Bridge est joint sur 127.0.0.1, port ephemere). Le VRAI code du Bridge (infra/helios-sot/bridge/bridge.py)
s'execute derriere son vrai gestionnaire HTTP ; seuls le noeud (.158) et le Backend Java sont simules.

Les sept preuves exigees :
  1. nominal -> RESOLVED                                  NominalTests
  2. detection -> bon type d'action                        DetectionVersActionTests
  3. le Bridge ne nettoie QUE les fichiers du scenario     NettoyageExclusifE2ETests
  4. verification <= 77 %, jamais un echec en RESOLVED     VerificationE2ETests
  5. deuxieme execution sans double suppression            IdempotenceE2ETests
  6. expiration/garde liberent si Python disparait         ExpirationSansPythonTests
  7. incidents DISK etrangers non touches                  IncidentsEtrangersE2ETests
"""
import ast
import contextlib
import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_helios_disk_test as T  # noqa: E402  emulateur (noeud, kubectl, Prometheus, monde E2E) ; enregistre le module outil UNIQUE
import helios_disk_e2e as E  # noqa: E402
import helios_disk_test as H  # noqa: E402

MIO = H.MIO


def muet(*a, **k):
    return None


class GardeSansAccesReel(object):
    """Interdit tout sous-processus (ssh, kubectl, vmrun...) et toute connexion hors 127.0.0.1 pendant un test."""

    def __enter__(self):
        original = socket.socket.connect

        def connecter(sock, adresse, *a, **k):
            assert isinstance(adresse, tuple) and adresse[0] == "127.0.0.1", "connexion reseau hors boucle locale : %r" % (adresse,)
            return original(sock, adresse, *a, **k)

        self.patches = [mock.patch("subprocess.Popen", side_effect=AssertionError("sous-processus interdit (ssh/kubectl)")),
                        mock.patch("os.system", side_effect=AssertionError("os.system interdit")),
                        mock.patch.object(socket.socket, "connect", connecter)]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in reversed(self.patches):
            p.stop()


class Base(unittest.TestCase):
    def setUp(self):
        garde = GardeSansAccesReel()
        garde.__enter__()
        self.addCleanup(garde.__exit__)

    @staticmethod
    def ctx(**kw):
        return E.monde_nominal(**kw)

    @staticmethod
    def lancer(ctx, **kw):
        return E.lancer_scenario(ctx, sortie=muet, **kw)


def a_linstant(monde, t_cible, action):
    """Appelle `action(monde)` UNE FOIS, des que l'horloge simulee atteint t_cible (evenement externe pendant le scenario)."""
    original = monde.avancer
    etat = {"fait": False}

    def avancer(secondes):
        fin = monde.t + secondes
        if not etat["fait"] and monde.t < t_cible <= fin:
            original(t_cible - monde.t)
            etat["fait"] = True
            action(monde)
            if fin > monde.t:
                original(fin - monde.t)
            return
        original(secondes)

    monde.avancer = avancer


def usage_force(ctx, pct):
    """Fixe l'occupation de .158 (noeud ET Prometheus) a `pct` dans les faits ET dans le monde."""
    taille = ctx.fs.taille
    dispo = int(round(taille * (1.0 - pct / 100.0)))
    ctx.faits["noeud"]["fs"] = H.Fs(taille, dispo)
    ctx.faits["prom"]["usage"] = H.Fs(taille, dispo).usage
    if isinstance(ctx.fe.get("bridge_sonde"), dict):
        ctx.fe["bridge_sonde"]["usage"] = round(H.Fs(taille, dispo).usage, 2)          # le Bridge lit la meme occupation de /
    ctx.monde.base = dispo


# ====================================================================================================== parametres figes
class ParametresFigesTests(Base):
    def test_parametres_du_scenario(self):
        p = E.parametres()
        self.assertEqual((81.0, 82.0, 83.0, 77.0), (p.cible, p.plafond, p.urgence, p.recuperation))
        self.assertEqual(512 * MIO, p.bloc)
        self.assertEqual((720, 1020), (p.observe, p.expiration))
        self.assertEqual(8350, H.seuil_garde_x100(p), "garde systeme a 83,5 %")
        self.assertEqual(80.0, E.DETECTION_PCT)
        self.assertEqual(6 * 1024 ** 3, E.MAX_TOTAL_OCTETS)
        self.assertEqual("/var/lib/helios-diskfill", E.DOSSIER)
        self.assertEqual(("DETECTION", "INCIDENT", "HOST_RECLAIM_DISK", "BRIDGE", "VERIFY", "RESOLVED"), E.CHEMIN_NOMINAL)
        self.assertNotIn("ESCALATE", E.CHEMIN_NOMINAL, "l'escalade n'est jamais un resultat nominal")

    def test_le_plan_theorique_respecte_les_limites(self):
        ctx = self.ctx()
        blocs = H.planifier(ctx.fs, E.parametres())
        self.assertLessEqual(len(blocs), E.MAX_FICHIERS)
        self.assertLessEqual(sum(blocs), E.MAX_TOTAL_OCTETS)
        self.assertLessEqual(H.Fs(ctx.fs.taille, ctx.fs.dispo - sum(blocs)).usage, E.PLAFOND_PCT)

    def test_aucun_parametre_ne_peut_etre_surcharge_en_ligne_de_commande(self):
        for option in ("--target-pct", "--cap-pct", "--emergency-pct", "--recovery-pct", "--chunk-mb", "--observe-s", "--target"):
            with self.subTest(option=option), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                E.principal([option, "50"], sortie=muet)

    def test_le_module_ne_lance_rien_et_renvoie_vers_le_lanceur_reel(self):
        lignes = []
        self.assertEqual(2, E.principal(["--go"], sortie=lignes.append))
        self.assertIn("ce module ne lance rien", " ".join(lignes))
        self.assertIn("helios_disk_test.py --target 158 --go", " ".join(lignes))

    def test_le_module_n_importe_ni_ssh_ni_sous_processus_ni_socket(self):
        arbre = ast.parse(open(os.path.join(HERE, "helios_disk_e2e.py"), encoding="utf-8").read())
        modules = set()
        for n in ast.walk(arbre):
            if isinstance(n, ast.Import):
                modules.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                modules.add(n.module)
        autorises = {"argparse", "hashlib", "importlib.util", "json", "os", "re", "secrets", "shlex", "sys", "threading", "urllib.error",
                     "urllib.request", "collections", "http.server", "types", "helios_disk_test"}
        self.assertEqual(set(), modules - autorises)

    def test_le_programme_de_la_garde_est_valide_statiquement(self):
        self.assertTrue(E.autotester_garde())
        self.assertFalse(E.autotester_garde(H.valider_parametres(cible=81.0, plafond=82.0, urgence=82.5, recuperation=77.0)),
                         "une garde construite avec une autre urgence n'est pas celle du scenario (8325 != 8350)")


# ============================================================================================================ coherence
class CoherenceTests(Base):
    def test_coherence_reelle(self):
        res = E.verifier_coherence()
        ko = [(n, d) for n, ok, d in res if not ok]
        self.assertEqual([], ko)
        self.assertGreaterEqual(len(res), 30)

    @staticmethod
    def _dans_verify_disk(texte, ancien, nouveau):
        """Remplace `ancien` UNIQUEMENT dans le corps de verifyDiskRecovery (les verifications CPU/RAM portent les memes expressions)."""
        debut = texte.index("RecoveryVerificationResult verifyDiskRecovery(")
        fin = texte.index("RecoveryVerificationResult verifyMemoryRecovery(")
        corps = texte[debut:fin]
        assert corps.count(ancien) == 1, "ancre introuvable ou ambigue dans verifyDiskRecovery"
        return texte[:debut] + corps.replace(ancien, nouveau) + texte[fin:]

    def test_une_derive_est_detectee(self):
        s = E.lire_sources()
        s["java_worker"] = s["java_worker"]       # lecture seule : les derives sont appliquees sur des copies
        cas = [("java_verification", "recovery-threshold-percent:85.0}", "recovery-threshold-percent:77.0}",
                "Java verification : seuil d'origine INCHANGE hors scenario (defaut 85.0)"),
               ("bridge", "DISQUE_RECUPERATION_PCT = 77.0", "DISQUE_RECUPERATION_PCT = 85.0", "Bridge : recuperation <= 77 %"),
               ("bridge", 'DISQUE_SCENARIO = "DISK_E2E_158"', 'DISQUE_SCENARIO = "DISK_E2E_155"', "Bridge : scenario"),
               ("bridge", 'DISQUE_DOSSIER = "/var/lib/helios-diskfill"', 'DISQUE_DOSSIER = "/var/lib"', "Bridge : repertoire exclusif"),
               ("bridge", "DISQUE_MAX_FICHIERS = 16", "DISQUE_MAX_FICHIERS = 64", "Bridge : 16 fichiers maximum"),
               ("java_executeur", 'DISK_E2E_TARGET = "k8s-worker1"', 'DISK_E2E_TARGET = "k8s-worker2"', "Java executeur : cible"),
               ("java_client", 'DISK_SCENARIO = "DISK_E2E_158"', 'DISK_SCENARIO = "AUTRE"', "Java client : scenario"),
               ("java_verification", "INODES_RECOVERY_THRESHOLD_PERCENT = 85.0;", "INODES_RECOVERY_THRESHOLD_PERCENT = 77.0;",
                "Java verification : seuil des inodes inchange"),
               ("java_worker", "@VERIFY_DISK:consecutiveHealthy >= 2", "consecutiveHealthy >= 1",
                "Java verification Camunda : tentatives bornees 2..8, pas <= 30 s, 2 echantillons sains consecutifs"),
               ("java_worker", "@VERIFY_DISK:Math.min(8, configuredAttempts)", "Math.min(80, configuredAttempts)",
                "Java verification Camunda : tentatives bornees 2..8, pas <= 30 s, 2 echantillons sains consecutifs"),
               ("java_proprietes", "private int cooldownMinutes = 30;", "private int cooldownMinutes = 5;",
                "Java proprietes : cooldown 30 min, 10 tentatives, pas 30 s, listes blanches '*'"),
               ("detecteur", "@DETECTEUR_DISK", "85", "detecteur : NODE_DISK_FULL > 80"),
               ("bridge", r'DISQUE_RE_FICHIER = re.compile(r"helios-diskfill-([0-9a-f]{32})-([0-9]{3})\.bin")',
                r'DISQUE_RE_FICHIER = re.compile(r"helios-diskfill-.*")', "noms de fichiers : outil et Bridge reconnaissent exactement le meme motif"),
               # DISK-E2E-158-02 : drapeau dedie, seuil de 77 % limite au scenario, .155 jamais, aucun repli vers le pod hote prive
               ("java_scenario", "RECOVERY_THRESHOLD_PERCENT = 77.0;", "RECOVERY_THRESHOLD_PERCENT = 85.0;", "Java scenario : recuperation <= 77 %"),
               ("java_scenario", 'String TARGET = "k8s-worker1";', 'String TARGET = "k8s-worker2";', "Java scenario : cible unique"),
               ("java_verification", "scenarioEnabled && HostDiskScenario.matchesTargetAndType(target, type)",
                "HostDiskScenario.matchesTargetAndType(target, type)",
                "Java verification : 77 % lu dans le scenario, applique SEULEMENT si le drapeau du scenario est actif"),
               ("java_proprietes", "private boolean hostDiskE2e158Enabled = false;", "private boolean hostDiskE2e158Enabled = true;",
                "Java proprietes : drapeau du scenario DESACTIVE par defaut, ancien drapeau inchange (desactive)"),
               ("application_properties", "${HOST_DISK_E2E_158_ENABLED:false}", "${HOST_DISK_REMEDIATION_ENABLED:false}",
                "application.properties : HOST_DISK_E2E_158_ENABLED (defaut false) distinct de HOST_DISK_REMEDIATION_ENABLED (defaut false)"),
               ("application_properties", "${HOST_DISK_E2E_158_ENABLED:false}", "${HOST_DISK_E2E_158_ENABLED:true}",
                "application.properties : HOST_DISK_E2E_158_ENABLED (defaut false) distinct de HOST_DISK_REMEDIATION_ENABLED (defaut false)"),
               ("java_politique", "return properties.isHostDiskE2e158Enabled();",
                "return properties.isHostDiskE2e158Enabled() || properties.isHostDiskRemediationEnabled();", "Java politique : le scenario n'est eligible QUE par son drapeau dedie ; NODE_DISK_FULL hors scenario (.155) jamais ; PRESSURE/INODES STRICTEMENT inchanges (ancien drapeau, memes cibles)"),
               ("java_politique", 'if ("NODE_DISK_FULL".equals(normalizedType)) {', "if (false) {", "Java politique : le scenario n'est eligible QUE par son drapeau dedie ; NODE_DISK_FULL hors scenario (.155) jamais ; PRESSURE/INODES STRICTEMENT inchanges (ancien drapeau, memes cibles)"),
               ("java_politique", 'return properties.isHostDiskRemediationEnabled()\n                && Set.of("k8s-worker1", "k8s-worker2").contains(normalizedTarget)',
                'return properties.isHostDiskRemediationEnabled()\n                && "k8s-worker1".equals(normalizedTarget)', "Java politique : le scenario n'est eligible QUE par son drapeau dedie ; NODE_DISK_FULL hors scenario (.155) jamais ; PRESSURE/INODES STRICTEMENT inchanges (ancien drapeau, memes cibles)"),
               ("java_orchestrateur", 'Set.of("k8s-worker1", "k8s-worker2");', 'Set.of("k8s-worker1");', "Java orchestrateur : allowlist d'origine INCHANGEE ; NODE_DISK_FULL hors scenario (.155) refuse ; drapeau du scenario verifie avant toute action ; ancien drapeau pour PRESSURE/INODES"),
               ("java_orchestrateur", 'if (!scenario && "NODE_DISK_FULL".equals(type)) {', "if (false) {", "Java orchestrateur : allowlist d'origine INCHANGEE ; NODE_DISK_FULL hors scenario (.155) refuse ; drapeau du scenario verifie avant toute action ; ancien drapeau pour PRESSURE/INODES"),
               ("java_client", "if (!properties.isHostDiskE2e158Enabled()) {", "if (!properties.isHostDiskRemediationEnabled()) {",
                "Java client : l'envoi au Bridge est gouverne par le drapeau dedie du scenario (jamais l'ancien)"),
               ("java_executeur", "if (!properties.isHostDiskE2e158Enabled()) {", "if (false) {",
                "Java executeur : le scenario passe par le Bridge, sous son drapeau dedie, SANS repli vers le pod hote prive"),
               ("java_executeur", "            return executeDiskReclaimThroughBridge(plan, canonicalTarget, dryRun);\n        }\n",
                "            RemediationExecutionResult viaBridge = executeDiskReclaimThroughBridge(plan, canonicalTarget, dryRun);\n"
                "            return viaBridge.success() ? viaBridge : executeLegacyHostReclaim(canonicalTarget, dryRun);\n        }\n",
                "Java executeur : le scenario passe par le Bridge, sous son drapeau dedie, SANS repli vers le pod hote prive"),
               ("java_executeur", "if (DISK_E2E_TYPE.equals(type)) {", "if (false) {", "Java executeur : NODE_DISK_FULL hors scenario refuse (.155 jamais) ; PRESSURE / INODES gardent la precedence d'origine (ancien drapeau en tete, memes cibles)"),
               ("java_executeur", "(!scenario && !properties.isHostDiskRemediationEnabled())", "(false)", "Java executeur : NODE_DISK_FULL hors scenario refuse (.155 jamais) ; PRESSURE / INODES gardent la precedence d'origine (ancien drapeau en tete, memes cibles)")]
        for cle, ancien, nouveau, nom in cas:
            with self.subTest(nom=nom, derive=nouveau):
                mod = dict(s)
                if ancien.startswith("@VERIFY_DISK:"):
                    mod[cle] = self._dans_verify_disk(s[cle], ancien[len("@VERIFY_DISK:"):], nouveau)
                elif ancien == "@DETECTEUR_DISK":
                    motif = re.compile(r"(\(\"NODE_DISK_FULL\", '\(1-\(node_filesystem_avail_bytes[^']*\)\)\*100 > )80")
                    self.assertEqual(1, len(motif.findall(s[cle])))
                    mod[cle] = motif.sub(r"\g<1>" + nouveau, s[cle])
                else:
                    self.assertEqual(1, s[cle].count(ancien), "ancre introuvable ou ambigue")
                    mod[cle] = s[cle].replace(ancien, nouveau)
                self.assertNotEqual(s[cle], mod[cle], "la derive n'a rien change")
                ko = [n for n, ok, _ in E.verifier_coherence(mod) if not ok]
                self.assertIn(nom, ko)
                self.assertFalse(E.coherence_ok(mod))

    def test_cli_coherence(self):
        self.assertEqual(0, E.principal(["--coherence"], sortie=muet))


# =================================================================================================== garde des prerequis
def _fs(ctx, pct):
    usage_force(ctx, pct)


CAS_REFUS = [
    # (nom, mutation(ctx), code E2E attendu | None si seule la base refuse, kwargs de evaluer_e2e)
    ("nom d'hote different", lambda c: c.faits["noeud"].update(hostname="k8s-worker2"), None, {}),
    ("InternalIP d'une autre machine", lambda c: c.faits["noeud_k8s"].update(internal_ips=["192.168.148.155"]), None, {}),
    ("nom du noeud Kubernetes different", lambda c: c.faits["noeud_k8s"].update(nom_k8s="k8s-worker2"), None, {}),
    ("occupation illisible", lambda c: c.faits["noeud"].update(fs=None), None, {}),
    ("systeme de fichiers btrfs", lambda c: c.faits["noeud"].update(fstype="btrfs"), None, {}),
    ("fallocate absent", lambda c: c.faits["noeud"].update(fallocate=False), None, {}),
    ("repertoire exclusif non vide", lambda c: c.faits["noeud"].update(dossier=["residu.bin"]), None, {}),
    ("repertoire exclusif illisible", lambda c: c.faits["noeud"].update(dossier="inconnu"), None, {}),
    ("occupation deja a 82 %", lambda c: _fs(c, 82.0), None, {}),
    ("occupation de base 78 % > recuperation", lambda c: _fs(c, 78.0), None, {}),
    ("remplissage requis > 6 Gio (occupation 50 %)", lambda c: _fs(c, 50.0), None, {}),
    ("Prometheus illisible", lambda c: c.faits["prom"].update(illisibles=["q1"]), None, {}),
    ("Prometheus : deux series", lambda c: c.faits["prom"].update(series=2), None, {}),
    ("helios_incident deja actif", lambda c: c.faits["prom"].update(incident_actif=1), None, {}),
    ("cluster degrade", lambda c: c.faits.update(cluster=(2, 3)), None, {}),
    ("noeud non Ready", lambda c: c.faits["noeud_k8s"].update(ready=False), None, {}),
    ("DiskPressure actif", lambda c: c.faits["noeud_k8s"].update(disk_pressure=True), None, {}),
    ("la cible heberge le primaire CNPG (jamais presume accepte)", lambda c: c.faits["cnpg"].update(primaire_sur_cible=True), None, {}),
    ("CNPG non sain", lambda c: c.faits["cnpg"].update(sain=False), None, {}),
    ("incident disque recent sur la cible (base)", lambda c: c.faits["incidents"].update(disque_actifs=[12]), None, {}),
    ("incidents illisibles (base)", lambda c: c.faits.update(incidents=None), None, {}),
    ("NODE_NOT_READY il y a 10 min", lambda c: c.faits["incidents"].update(notready_ages_s=[600]), None, {}),
    ("gel de l'hote il y a 20 min", lambda c: c.faits["hote"].update(gels=[(1200, 90, "04:02")]), None, {}),
    ("journaux VMware illisibles", lambda c: c.faits["hote"].update(gels=None), None, {}),
    ("RAM de l'hote basse", lambda c: c.faits["hote"].update(ram_mo=900), None, {}),
    ("RAM de l'hote illisible", lambda c: c.faits["hote"].update(ram_mo=None), None, {}),
    ("C: presque plein", lambda c: c.faits["hote"].update(c_libre_go=10.0), None, {}),
    ("VMDK avec snapshot", lambda c: c.faits["hote"]["vmdk"].update(snapshots=1), None, {}),
    ("drapeau du scenario false", lambda c: c.fe["drapeaux"].update(HOST_DISK_E2E_158_ENABLED="false"), "E2E_DRAPEAU_DISQUE", {}),
    ("drapeau du scenario absent", lambda c: c.fe["drapeaux"].update(HOST_DISK_E2E_158_ENABLED="absent"), "E2E_DRAPEAU_DISQUE", {}),
    ("drapeau du scenario illisible", lambda c: c.fe["drapeaux"].update(HOST_DISK_E2E_158_ENABLED="illisible"), "E2E_DRAPEAU_DISQUE", {}),
    ("ancien drapeau HOST_DISK_REMEDIATION_ENABLED actif (ouvrirait le pod hote prive)",
     lambda c: c.fe["drapeaux"].update(HOST_DISK_REMEDIATION_ENABLED="true"), "E2E_DRAPEAU_LEGACY", {}),
    ("ancien drapeau illisible", lambda c: c.fe["drapeaux"].update(HOST_DISK_REMEDIATION_ENABLED="illisible"), "E2E_DRAPEAU_LEGACY", {}),
    ("RAM de l'hote 2400 Mo < 2500", lambda c: c.faits["hote"].update(ram_mo=2400), "E2E_HOTE_RAM", {}),
    ("pages lues/s de l'hote >= 5000", lambda c: c.fe.update(hote={"pages_s": 5000.0}), "E2E_HOTE_PAGES", {}),
    ("pages lues/s illisibles", lambda c: c.fe.update(hote={"pages_s": None}), "E2E_HOTE_PAGES_ILLISIBLES", {}),
    ("pagination absente des faits", lambda c: c.fe.pop("hote"), "E2E_HOTE_PAGES_ILLISIBLES", {}),
    ("un pod Backend a l'ancienne image",
     lambda c: c.fe["backend"].update(images_pods=["pfe-backend:sim-disk158", "pfe-backend:0.2.164-vm3auto"]), "E2E_BACKEND_IMAGE", {}),
    ("images des pods Backend illisibles", lambda c: c.fe["backend"].update(images_pods=None), "E2E_BACKEND_PODS_ILLISIBLES", {}),
    ("environnement des pods Backend perime", lambda c: c.fe["backend"].update(env_a_jour=False), "E2E_BACKEND_ENV_PERIME", {}),
    ("environnement des pods Backend illisible", lambda c: c.fe["backend"].update(env_a_jour=None), "E2E_BACKEND_ENV_PERIME", {}),
    ("REMEDIATION_COOLDOWN_MINUTES illisible", lambda c: c.fe.update(cooldown_minutes=None), "E2E_COOLDOWN_ILLISIBLE", {}),
    ("HELIOS_BRIDGE_ENABLED false", lambda c: c.fe["drapeaux"].update(HELIOS_BRIDGE_ENABLED="false"), "E2E_DRAPEAU_BRIDGE", {}),
    ("drapeaux illisibles", lambda c: c.fe.update(drapeaux=None), "E2E_DRAPEAU_BRIDGE", {}),
    ("Bridge injoignable", lambda c: c.fe["bridge"].update(joignable=False), "E2E_BRIDGE_INJOIGNABLE", {}),
    ("Bridge illisible", lambda c: c.fe.update(bridge=None), "E2E_BRIDGE_INJOIGNABLE", {}),
    ("Bridge sans la capacite HOST_RECLAIM_DISK", lambda c: c.fe["bridge"].update(capacites=["HOST_IMAGE_GC"]), "E2E_BRIDGE_CAPACITE_ABSENTE", {}),
    ("Bridge : liste des capacites illisible", lambda c: c.fe["bridge"].update(capacites=None), "E2E_BRIDGE_CAPACITE_ABSENTE", {}),
    ("Bridge deploye != source de verite", lambda c: c.fe["bridge"].update(sha256="0" * 64), "E2E_BRIDGE_VERSION", {}),
    ("Bridge : empreinte illisible", lambda c: c.fe["bridge"].update(sha256=None), "E2E_BRIDGE_VERSION_ILLISIBLE", {}),
    ("sonde du Bridge absente des faits", lambda c: c.fe.pop("bridge_sonde"), "E2E_BRIDGE_SONDE", {}),
    ("sonde du Bridge non tentee alors que le Bridge est conforme", lambda c: c.fe.update(bridge_sonde={"tentee": False}), "E2E_BRIDGE_SONDE", {}),
    ("sonde du Bridge : erreur de collecte", lambda c: c.fe.update(bridge_sonde={"tentee": True, "ok": None, "erreur": "aucune reponse (rc=255)"}),
     "E2E_BRIDGE_SONDE", {}),
    ("sonde du Bridge : validation refusee", lambda c: c.fe["bridge_sonde"].update(ok=False, rejet="DIRECTORY_UNREADABLE"), "E2E_BRIDGE_SONDE", {}),
    ("sonde du Bridge : ok indetermine", lambda c: c.fe["bridge_sonde"].update(ok=None), "E2E_BRIDGE_SONDE", {}),
    ("sonde du Bridge : le Bridge a EXECUTE (execute=True)", lambda c: c.fe["bridge_sonde"].update(execute=True), "E2E_BRIDGE_SONDE", {}),
    ("sonde du Bridge : execute absent", lambda c: c.fe["bridge_sonde"].pop("execute"), "E2E_BRIDGE_SONDE", {}),
    ("sonde du Bridge : validation != ACCEPTED", lambda c: c.fe["bridge_sonde"].update(validation="REJECTED"), "E2E_BRIDGE_SONDE", {}),
    ("sonde du Bridge : mode != RECOMMEND_ONLY", lambda c: c.fe["bridge_sonde"].update(mode="EXECUTE"), "E2E_BRIDGE_SONDE", {}),
    ("sonde du Bridge : occupation de / illisible", lambda c: c.fe["bridge_sonde"].update(usage=None), "E2E_BRIDGE_SONDE", {}),
    ("sonde du Bridge : occupation non numerique", lambda c: c.fe["bridge_sonde"].update(usage="67.1"), "E2E_BRIDGE_SONDE", {}),
    ("sonde du Bridge : occupation incoherente (+5 points)", lambda c: c.fe["bridge_sonde"].update(usage=c.fs.usage + 5.0), "E2E_BRIDGE_MESURE", {}),
    ("sonde du Bridge : occupation incoherente (-4 points)", lambda c: c.fe["bridge_sonde"].update(usage=c.fs.usage - 4.0), "E2E_BRIDGE_MESURE", {}),
    ("Backend non pret", lambda c: c.fe["backend"].update(pret=False), "E2E_BACKEND_NON_PRET", {}),
    ("Backend illisible", lambda c: c.fe.update(backend=None), "E2E_BACKEND_NON_PRET", {}),
    ("image du Backend inattendue (version sans routage DISK)", lambda c: c.fe["backend"].update(image="pfe-backend:0.2.164-vm3auto"),
     "E2E_BACKEND_IMAGE", {}),
    ("aucune image de Backend declaree", lambda c: None, "E2E_BACKEND_IMAGE_NON_DECLAREE", {"image_backend_attendue": None}),
    ("systemd-run absent", lambda c: c.fe["systemd"].update(systemd_run=False), "E2E_SYSTEMD_RUN", {}),
    ("awk absent", lambda c: c.fe["systemd"].update(awk=False), "E2E_AWK", {}),
    ("unites helios-diskfill-* residuelles", lambda c: c.fe["systemd"].update(unites_residuelles=["helios-diskfill-expire-x.timer"]),
     "E2E_UNITES_RESIDUELLES", {}),
    ("unites residuelles illisibles", lambda c: c.fe["systemd"].update(unites_residuelles=None), "E2E_UNITES_RESIDUELLES", {}),
    ("auto-test de la garde en echec", lambda c: c.fe["systemd"].update(garde_autotest=False), "E2E_GARDE_AUTOTEST", {}),
    ("protection systemd illisible", lambda c: c.fe.update(systemd=None), "E2E_SYSTEMD_ILLISIBLE", {}),
    ("cooldown du Backend : action il y a 10 min", lambda c: c.fe.update(actions_disque_recentes_s=[600]), "E2E_COOLDOWN_BACKEND", {}),
    ("cooldown du Backend : action il y a 1919 s", lambda c: c.fe.update(actions_disque_recentes_s=[3600, 1919]), "E2E_COOLDOWN_BACKEND", {}),
    ("cooldown : historique illisible", lambda c: c.fe.update(actions_disque_recentes_s=None), "E2E_COOLDOWN_ILLISIBLE", {}),
    ("incident disque concurrent sur .158 lui-meme", lambda c: c.fe.update(incidents_disque_ouverts=[{"app": "k8s-worker1", "id": 8}]),
     "E2E_INCIDENT_DISQUE_CONCURRENT", {}),
    ("incidents disque ouverts illisibles", lambda c: c.fe.update(incidents_disque_ouverts=None), "E2E_INCIDENTS_ILLISIBLES", {}),
    ("autre cible (.155)", lambda c: None, "E2E_CIBLE", {"cle": "155"}),
    ("parametres alteres", lambda c: None, "E2E_PARAMETRES",
     {"p": H.valider_parametres(cible=80.5, plafond=82.0, urgence=83.0, recuperation=77.0, bloc=512 * MIO, observe_s=720, expiration_s=1020)}),
]


class PrerequisTests(Base):
    def evaluer(self, ctx, **kw):
        kw.setdefault("image_backend_attendue", "pfe-backend:sim-disk158")
        return E.evaluer_e2e(ctx.faits, ctx.fe, **kw)

    def test_tout_pret_donne_vert(self):
        r = self.evaluer(self.ctx())
        self.assertEqual("VERT", r["verdict"], [x for x in r["raisons"] if x[0] != "INFO"])
        self.assertTrue(r["go"])
        self.assertEqual([], [x for x in r["raisons"] if x[0] == "ROUGE"])

    def test_chaque_prerequis_manquant_refuse(self):
        for nom, mutation, code, kw in CAS_REFUS:
            with self.subTest(prerequis=nom):
                ctx = self.ctx()
                mutation(ctx)
                r = self.evaluer(ctx, **kw)
                self.assertEqual("ROUGE", r["verdict"], nom)
                self.assertFalse(r["go"])
                rouges = [x[3] for x in r["raisons"] if x[0] == "ROUGE"]
                self.assertTrue(rouges, nom)
                if code:
                    self.assertIn(code, rouges, nom)

    def test_un_refus_n_injecte_aucune_charge_et_ne_cree_aucun_incident(self):
        for nom, mutation, code, kw in CAS_REFUS:
            if kw:      # evaluer_e2e(**kw) : cas testes ci-dessus (pas de parametre equivalent pour lancer_scenario)
                continue
            with self.subTest(prerequis=nom):
                ctx = self.ctx()
                mutation(ctx)
                out = self.lancer(ctx)
                self.assertEqual("REFUS_PREREQUIS", out["verdict"])
                self.assertFalse(out["injection"])
                self.assertFalse(out["incident"])
                m = ctx.monde
                self.assertEqual([], m.appels, "AUCUN appel au noeud")
                self.assertEqual({}, m.fichiers, "aucun fichier")
                self.assertIsNone(m.dossier, "repertoire jamais cree")
                self.assertIsNone(m.incident, "aucun incident artificiel")
                self.assertEqual({}, m.minuteurs)
                self.assertEqual({}, m.gardes)
                self.assertEqual([], m.chaine)
                self.assertEqual([], ctx.bridge.requetes, "le Bridge n'est ni demarre ni appele")
                self.assertEqual([], ctx.client.envois)
                self.assertEqual(0.0, m.t, "l'horloge simulee n'a pas avance")

    def test_les_cas_de_garde_a_parametres_refusent_aussi_sans_toucher_au_monde(self):
        for nom, mutation, code, kw in CAS_REFUS:
            if not kw or "p" in kw:
                continue
            with self.subTest(prerequis=nom):
                ctx = self.ctx()
                mutation(ctx)
                r = E.evaluer_e2e(ctx.faits, ctx.fe, **dict({"image_backend_attendue": "pfe-backend:sim-disk158"}, **kw))
                self.assertFalse(r["go"])
                self.assertEqual([], ctx.monde.appels)

    def test_frontieres_du_cooldown(self):
        self.assertFalse(self.evaluer(self._avec(actions_disque_recentes_s=[1919]))["go"])
        self.assertTrue(self.evaluer(self._avec(actions_disque_recentes_s=[1920]))["go"], "1920 s = 30 min + 2 min de marge : permis")
        self.assertTrue(self.evaluer(self._avec(actions_disque_recentes_s=[]))["go"])

    def test_le_cooldown_suit_remediation_cooldown_minutes_de_la_configmap(self):
        self.assertFalse(self.evaluer(self._avec(cooldown_minutes=2, actions_disque_recentes_s=[239]))["go"])
        self.assertTrue(self.evaluer(self._avec(cooldown_minutes=2, actions_disque_recentes_s=[240]))["go"], "2 min + 2 min de marge")
        self.assertFalse(self.evaluer(self._avec(cooldown_minutes=-1))["go"])
        self.assertFalse(self.evaluer(self._avec(cooldown_minutes="2"))["go"], "valeur non entiere : illisible")

    def test_le_risque_cnpg_n_est_jamais_presume_accepte(self):
        ctx = self.ctx()
        ctx.faits["cnpg"]["primaire_sur_cible"] = True
        r = self.evaluer(ctx)
        self.assertFalse(r["go"])
        textes = [x[1] for x in r["raisons"] if x[0] == "ROUGE"]
        self.assertTrue(any("HEBERGE LE PRIMAIRE CNPG" in t and "risque NON accepte" in t for t in textes), textes)
        self.assertFalse(any("risque accepte explicitement" in x[1] for x in r["raisons"]), "aucune voie d'acceptation sur le chemin nominal")

    def test_un_incident_de_fond_sur_monitoring_est_tolere(self):
        r = self.evaluer(self._avec(incidents_disque_ouverts=[{"app": "monitoring", "id": 3858}]))
        self.assertTrue(r["go"], r["raisons"])
        self.assertTrue(any(x[0] == "INFO" and "monitoring" in x[1] for x in r["raisons"]))

    def test_incident_historique_worker1_suffixe_exige_age_et_diskpressure_false(self):
        incident = {"app": "k8s-worker1-DiskPressure", "id": 1344, "age_s": 50 * 3600}
        fe = {"incidents_disque_ouverts": [incident],
              "diskpressure": {"k8s-worker1": "False", "k8s-worker2": "False"}}
        r = self.evaluer(self._avec(**fe))
        self.assertEqual("ORANGE", r["verdict"], r["raisons"])
        self.assertTrue(any(x[0] == "ORANGE" and x[3] == "E2E_INCIDENTS_DISQUE_ANCIENS" for x in r["raisons"]))

        fe["diskpressure"]["k8s-worker1"] = "True"
        r = self.evaluer(self._avec(**fe))
        self.assertEqual("ROUGE", r["verdict"], r["raisons"])

    def _avec_trois_orange(self):
        ctx = self.ctx()
        ctx.faits["incidents"]["notready_ages_s"] = [3600]                                      # ORANGE NOTREADY_2H
        ctx.faits["hote"]["gels"] = [(36000, 120, "04:36")]                                     # ORANGE GEL_HOTE_48H
        ctx.fe.update(incidents_disque_ouverts=[{"app": "k8s-worker2", "id": 7, "age_s": 50 * 3600}],
                      diskpressure={"k8s-worker1": "False", "k8s-worker2": "False"})  # ORANGE historique verifie
        return ctx

    def test_un_orange_non_acquitte_refuse_et_ne_s_acquitte_que_code_par_code(self):
        ctx = self._avec_trois_orange()
        tous = {"NOTREADY_2H", "GEL_HOTE_48H", "E2E_INCIDENTS_DISQUE_ANCIENS"}
        r = self.evaluer(ctx)
        self.assertEqual("ORANGE", r["verdict"])
        self.assertFalse(r["go"])
        self.assertEqual(tous, {x[3] for x in r["orange_non_acquittes"]})
        self.assertEqual([], [x for x in r["raisons"] if x[0] == "ROUGE"])
        for partiel in ({"NOTREADY_2H"}, {"NOTREADY_2H", "GEL_HOTE_48H"}, {"GEL_HOTE_48H", "E2E_INCIDENTS_DISQUE_ANCIENS"}, {"AUTRE_CODE"}):
            r = self.evaluer(ctx, acquittes=partiel)
            self.assertFalse(r["go"], partiel)
            self.assertEqual(tous - partiel, {x[3] for x in r["orange_non_acquittes"]})
        r = self.evaluer(ctx, acquittes=tous)
        self.assertTrue(r["go"], r["raisons"])
        self.assertEqual("ORANGE", r["verdict"])
        self.assertEqual([], r["orange_non_acquittes"])

    def test_un_acquittement_ne_couvre_jamais_un_rouge(self):
        ctx = self._avec_trois_orange()
        ctx.fe["bridge"]["sha256"] = "0" * 64
        r = self.evaluer(ctx, acquittes={"NOTREADY_2H", "GEL_HOTE_48H", "E2E_INCIDENTS_DISQUE_ANCIENS", "E2E_BRIDGE_VERSION"})
        self.assertEqual("ROUGE", r["verdict"])
        self.assertFalse(r["go"])

    def test_remediation_non_activee_et_le_risque_cnpg_ne_sont_jamais_acquittables(self):
        interdits = {"REMEDIATION_NON_ACTIVEE", "PRIMAIRE_CNPG_ACCEPTE", "E2E_DRAPEAU_DISQUE", "E2E_DRAPEAU_LEGACY"}
        ctx = self.ctx()
        ctx.faits["cnpg"]["primaire_sur_cible"] = True
        r = self.evaluer(ctx, acquittes=interdits)
        self.assertFalse(r["go"])
        ctx = self.ctx()
        ctx.fe["drapeaux"]["HOST_DISK_E2E_158_ENABLED"] = "false"
        r = self.evaluer(ctx, acquittes=interdits)
        self.assertFalse(r["go"])
        self.assertIn("E2E_DRAPEAU_DISQUE", [x[3] for x in r["raisons"] if x[0] == "ROUGE"])
        self.assertNotIn("REMEDIATION_NON_ACTIVEE", [x[3] for x in r["raisons"]], "aucun mode detection seule")

    def test_les_orange_de_la_porte_sont_acquittables_par_le_lanceur(self):
        self.assertIn(E.CODE_INCIDENTS_ANCIENS, H.CODES_ORANGE_ACQUITTABLES)
        self.assertEqual(({E.CODE_INCIDENTS_ANCIENS}, None), H.analyser_acquittements([E.CODE_INCIDENTS_ANCIENS]))
        codes = {x[3] for x in self.evaluer(self._avec_trois_orange())["raisons"] if x[0] == "ORANGE"}
        self.assertTrue(codes <= set(H.CODES_ORANGE_ACQUITTABLES), codes)

    def test_155_est_toujours_refuse_quels_que_soient_les_drapeaux_et_les_acquittements(self):
        tous = set(H.CODES_ORANGE)
        for scenario in ("true", "false", "absent"):
            for ancien in ("true", "false", "absent"):
                with self.subTest(scenario=scenario, ancien=ancien):
                    ctx = self.ctx()
                    ctx.fe["drapeaux"].update(HOST_DISK_E2E_158_ENABLED=scenario, HOST_DISK_REMEDIATION_ENABLED=ancien)
                    r = self.evaluer(ctx, cle="155", acquittes=tous)
                    self.assertFalse(r["go"])
                    self.assertEqual(["E2E_CIBLE"], [x[3] for x in r["raisons"] if x[0] == "ROUGE"])

    def _avec(self, **fe):
        ctx = self.ctx()
        ctx.fe.update(fe)
        return ctx


# ============================================================================================ preuve 1 : chemin nominal
class NominalTests(Base):
    @classmethod
    def setUpClass(cls):
        with GardeSansAccesReel():
            cls.ctx = E.monde_nominal()
            cls.out = E.lancer_scenario(cls.ctx, sortie=muet)

    def test_le_scenario_aboutit_a_resolved_par_le_chemin_nominal(self):
        out = self.out
        self.assertEqual("RESOLVED_E2E", out["verdict"], out.get("manque"))
        self.assertEqual([], out["manque"])
        self.assertEqual(E.CHEMIN_NOMINAL, tuple(out["etapes"]))
        self.assertEqual("OK", out["res"]["verdict"])
        self.assertEqual("RESOLVED", out["res"]["statut"])
        self.assertEqual("RESOLVED", self.ctx.monde.incident["statut"])
        self.assertEqual("HOST_RECLAIM_DISK:COMPLETED", self.ctx.monde.action)

    def test_aucune_escalade_dans_le_chemin_nominal(self):
        etapes = [e for _, e, _ in self.ctx.monde.chaine]
        self.assertNotIn("ESCALATE", etapes)
        self.assertNotIn("REFUS_POLITIQUE", etapes)
        self.assertEqual(["DETECTION", "INCIDENT", "HOST_RECLAIM_DISK", "BRIDGE", "VERIFY", "VERIFY", "RESOLVED"], etapes)

    def test_les_etapes_sont_dans_l_ordre_chronologique(self):
        temps = [t for t, _, _ in self.ctx.monde.chaine]
        self.assertEqual(sorted(temps), temps)
        self.assertLess(temps[0], temps[-1])

    def test_la_liberation_est_faite_par_helios_pas_par_les_filets(self):
        res = self.out["res"]
        self.assertEqual("helios", res["liberee_par"])
        self.assertEqual([], [j for j in self.ctx.monde.journal if j[1] in ("expiration", "garde")])

    def test_la_recuperation_est_mesuree_et_inferieure_ou_egale_a_77(self):
        res = self.out["res"]
        self.assertTrue(res["recupere"])
        self.assertLessEqual(res["usage_final"], 77.0)
        self.assertAlmostEqual(self.ctx.fs.usage, res["usage_final"], places=1)
        self.assertGreater(res["usage_atteint"], 80.0)
        self.assertLessEqual(self.ctx.monde.usage_vrai_max, 82.0)
        self.assertIsNone(self.ctx.monde.t_urgence, "l'arret d'urgence (83 %) n'a jamais ete atteint")

    def test_l_injection_respecte_les_limites(self):
        res = self.out["res"]
        self.assertLessEqual(res["total_alloue"], E.MAX_TOTAL_OCTETS)
        self.assertLessEqual(len(res["alloues"]), E.MAX_FICHIERS)
        self.assertTrue(all(H.RE_FICHIER.match(n) for n in res["alloues"]))

    def test_le_bridge_recoit_une_seule_requete_canonique_et_repond_avec_la_bonne_action(self):
        bridge = self.ctx.bridge
        actions = [(c, d) for c, d in bridge.requetes if c == "/action"]
        self.assertEqual(1, len(actions))
        self.assertEqual({"action": "HOST_RECLAIM_DISK", "type": "NODE_DISK_FULL", "ns": "k8s-nodes", "cible": "k8s-worker1",
                          "noeud": "k8s-worker1", "scenario": "DISK_E2E_158", "incident_id": "90001"}, actions[0][1])
        statut, corps = bridge.reponses[0]
        self.assertEqual(200, statut)
        self.assertEqual("HOST_RECLAIM_DISK", corps["action"], "jamais remplacee par HOST_IMAGE_GC")
        self.assertTrue(corps["ok"] and corps["execute"])
        self.assertEqual(0, corps["exitCode"])
        self.assertLessEqual(corps["usage_apres_pct"], 77.0)

    def test_le_bridge_n_a_emis_que_les_quatre_formes_de_commande_autorisees(self):
        cmds = self.ctx.bridge.exec.commandes
        self.assertTrue(cmds)
        formes = set("rm" if c.startswith(E.PREFIXE_RM) else c for c in cmds)
        self.assertEqual({E.CMD_STAT_DIR, E.CMD_FIND, E.CMD_STAT_FS, "rm"}, formes)
        self.assertFalse(any("systemctl" in c or "vacuum" in c for c in cmds), "ni arret des filets, ni nettoyage generique")

    def test_une_seule_suppression_groupee_des_seuls_fichiers_du_jeton(self):
        rm = self.ctx.bridge.exec.rm
        self.assertEqual(1, len(rm))
        self.assertEqual(sorted(self.out["res"]["alloues"]), sorted(rm[0]))

    def test_le_noeud_est_laisse_propre_et_les_filets_desarmes(self):
        m = self.ctx.monde
        self.assertEqual({}, m.fichiers)
        self.assertIsNone(m.dossier, "le repertoire cree par l'outil est retire")
        self.assertTrue(self.out["res"]["liberation_ok"])
        self.assertTrue(all(not v[2] for v in m.minuteurs.values()))
        self.assertTrue(all(not v[2] for v in m.gardes.values()))

    def test_la_verification_exige_deux_echantillons_sains_consecutifs(self):
        verifs = [d for _, e, d in self.ctx.monde.chaine if e == "VERIFY"]
        self.assertEqual(2, len(verifs))
        self.assertIn("sains consecutifs 1/2", verifs[0])
        self.assertIn("sains consecutifs 2/2", verifs[1])

    def test_le_module_du_bridge_est_restaure_apres_le_scenario(self):
        b = E.bridge_module()
        self.assertEqual("executer", b.executer.__name__)
        self.assertEqual("", b.JETON)
        self.assertEqual({}, b._DISQUE_LIAISONS)
        self.assertIsNotNone(b._tele)


class ConclureTests(Base):
    """Le verdict n'est RESOLVED_E2E que si CHAQUE preuve est presente : on falsifie une preuve a la fois sur un resultat nominal."""

    @classmethod
    def setUpClass(cls):
        with GardeSansAccesReel():
            cls.ctx = E.monde_nominal()
            cls.out = E.lancer_scenario(cls.ctx, sortie=muet)

    def standin(self, **surcharge):
        m = self.ctx.monde
        d = {"chaine": list(m.chaine), "incident": dict(m.incident), "action": m.action, "usage_vrai_max": m.usage_vrai_max,
             "t_urgence": m.t_urgence}
        res = dict(self.out["res"])
        for k, v in surcharge.items():
            if k.startswith("res_"):
                res[k[4:]] = v
            else:
                d[k] = v
        return res, SimpleNamespace(**d)

    def conclure(self, **surcharge):
        res, monde = self.standin(**surcharge)
        return E.conclure(res, monde)

    def test_reference(self):
        out = self.conclure()
        self.assertEqual("RESOLVED_E2E", out["verdict"])
        self.assertEqual([], out["manque"])

    def test_chaque_preuve_manquante_donne_echec_avec_exactement_cette_cause(self):
        chaine = list(self.ctx.monde.chaine)
        sans_bridge = [x for x in chaine if x[1] != "BRIDGE"]
        resolu_avant = [x for x in chaine if x[1] != "RESOLVED"]
        resolu_avant.insert(3, next(x for x in chaine if x[1] == "RESOLVED"))
        cas = [
            ("BRIDGE absent de la chaine", {"chaine": sans_bridge}, ["chemin_nominal_complet"]),
            ("RESOLVED avant la verification", {"chaine": resolu_avant}, ["chemin_nominal_complet"]),
            ("escalade dans la chaine", {"chaine": chaine + [(999.0, "ESCALATE", "x")]}, ["aucune_escalade"]),
            ("plafond de 82 % depasse", {"usage_vrai_max": 82.4}, ["plafond_respecte"]),
            ("urgence atteinte", {"t_urgence": 10.0}, ["plafond_respecte"]),
            ("liberation par l'expiration", {"res_liberee_par": "expiration"}, ["liberee_par_helios"]),
            ("liberation par la garde", {"res_liberee_par": "garde"}, ["liberee_par_helios"]),
            ("recuperation non confirmee", {"res_recupere": False}, ["recupere"]),
            ("occupation finale 77,01 %", {"res_usage_final": 77.01}, ["recupere"]),
            ("occupation finale illisible", {"res_usage_final": None}, ["recupere"]),
            ("liberation non verifiee", {"res_liberation_ok": False}, ["liberation_verifiee"]),
            ("verdict de l'outil != OK", {"res_verdict": "DETECTION_SEULE"}, ["outil_ok"]),
            ("action non completee", {"action": "HOST_RECLAIM_DISK:FAILED"}, ["action_completee"]),
            ("action absente", {"action": None}, ["action_completee"]),
            ("incident non resolu cote Backend", {"incident": {"id": 1, "statut": "RESOLUTION_FAILED"}}, ["incident_resolu"]),
            ("incident absent", {"incident": None}, ["incident_resolu"]),
            ("statut vu par l'outil != RESOLVED", {"res_statut": "RESOLUTION_FAILED"}, ["incident_resolu"]),
        ]
        for nom, surcharge, manque in cas:
            with self.subTest(preuve=nom):
                out = self.conclure(**surcharge)
                self.assertEqual("ECHEC_E2E", out["verdict"], nom)
                self.assertEqual(manque, out["manque"], nom)

    def test_la_frontiere_77_00_est_acceptee(self):
        self.assertEqual("RESOLVED_E2E", self.conclure(res_usage_final=77.0)["verdict"])


# ======================================================================================= preuve 2 : detection -> action
class FauxClient(object):
    def __init__(self, reponse=None):
        self.appels = []
        self.reponse = reponse or E.Reponse(True, True, 0, "ok")

    def envoyer_disque(self, *a):
        self.appels.append(a)
        return self.reponse


def incident(type_="NODE_DISK_FULL", app="k8s-worker1", ns="k8s-nodes", iid=4242):
    return {"id": iid, "type": type_, "ns": ns, "app": app, "statut": "OPEN"}


class DetectionVersActionTests(Base):
    def backend(self, **kw):
        return E.BackendModele(FauxClient(), **kw)

    def test_node_disk_full_sur_worker1_donne_host_reclaim_disk_automatique(self):
        b = self.backend()
        inc = incident()
        plan, raison = b.demarrer_si_eligible(inc, 1000.0)
        self.assertIsNotNone(plan, raison)
        self.assertEqual("HOST_RECLAIM_DISK", plan["action"])
        self.assertTrue(plan["auto"])
        self.assertFalse(plan["cooldown"])
        self.assertEqual("IN_PROGRESS", inc["statut"])

    def test_matrice_complete_drapeaux_cibles_types(self):
        """Pour chaque couple de drapeaux : k8s-worker1 + NODE_DISK_FULL = scenario (drapeau DEDIE seul, Bridge) ; NODE_DISK_FULL sur
        k8s-worker2 (.155) = JAMAIS ; PRESSURE / INODES = chemin d'origine STRICTEMENT inchange (ancien drapeau seul, pod hote, les deux
        workers comme avant)."""
        for scenario in (False, True):
            for legacy in (False, True):
                for cible in ("k8s-worker1", "k8s-worker2"):
                    for type_ in ("NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL"):
                        cas = "scenario=%s ancien=%s %s %s" % (scenario, legacy, cible, type_)
                        client = FauxClient()
                        b = E.BackendModele(client, drapeau_scenario=scenario, drapeau_legacy=legacy)
                        est_scenario = cible == "k8s-worker1" and type_ == "NODE_DISK_FULL"
                        plan, raison = b.demarrer_si_eligible(incident(type_, cible), 0.0)
                        if type_ == "NODE_DISK_FULL":
                            demarre = est_scenario and scenario                  # .155 : JAMAIS
                        else:
                            demarre = legacy                                     # PRESSURE / INODES : comme avant
                        self.assertEqual(demarre, plan is not None, cas + " : " + raison)
                        if plan is None:
                            self.assertEqual([], client.appels, cas)
                            self.assertEqual([], b.appels_legacy, cas)
                            continue
                        self.assertEqual("HOST_RECLAIM_DISK", plan["action"], cas)
                        self.assertTrue(plan["auto"], cas)
                        res = b.executer(plan)
                        self.assertEqual("bridge" if est_scenario else "pod_hote", res["voie"], cas)
                        self.assertEqual(1 if est_scenario else 0, len(client.appels), cas + " : le Bridge n'est appele que pour le scenario")
                        self.assertEqual([] if est_scenario else [(cible, type_)], b.appels_legacy,
                                         cas + " : le pod hote n'est JAMAIS utilise pour le scenario")

    def test_le_scenario_envoie_les_valeurs_canoniques_au_bridge(self):
        client = FauxClient()
        b = E.BackendModele(client)
        plan, _ = b.demarrer_si_eligible(incident("NODE_DISK_FULL", "k8s-worker1"), 0.0)
        self.assertEqual("bridge", b.executer(plan)["voie"])
        self.assertEqual(("HOST_RECLAIM_DISK", "NODE_DISK_FULL", "k8s-nodes", "k8s-worker1", "k8s-worker1", "DISK_E2E_158", 4242),
                         client.appels[0])

    def test_un_echec_du_bridge_n_ouvre_jamais_le_pod_hote_prive(self):
        """Meme avec les DEUX drapeaux actifs : un Bridge qui echoue (ou qui ne confirme pas) = echec net du scenario, jamais un repli."""
        for rep in (E.Reponse(False, True, -1, "NOT_RECOVERED"), E.Reponse(True, False, 0, "validation seule"), E.Reponse(True, True, 2, "code")):
            with self.subTest(rep=rep):
                client = FauxClient(rep)
                b = E.BackendModele(client, drapeau_scenario=True, drapeau_legacy=True, legacy_succes=True)
                plan, _ = b.demarrer_si_eligible(incident(), 0.0)
                res = b.executer(plan)
                self.assertFalse(res["succes"])
                self.assertEqual("bridge", res["voie"])
                self.assertEqual([], b.appels_legacy)
                self.assertEqual(1, len(client.appels))

    def test_worker2_est_refuse_par_l_executeur_pour_node_disk_full_quels_que_soient_les_drapeaux(self):
        for scenario, legacy in ((False, False), (True, False), (False, True), (True, True)):
            client = FauxClient()
            b = E.BackendModele(client, drapeau_scenario=scenario, drapeau_legacy=legacy)
            res = b.executer({"cible": "k8s-worker2", "ns": "k8s-nodes", "type": "NODE_DISK_FULL", "incident_id": 555})
            self.assertEqual("refus", res["voie"], (scenario, legacy))
            if legacy:
                self.assertIn("k8s-worker2/.155 jamais", res["message"])
            self.assertEqual(([], []), (client.appels, b.appels_legacy))

    def test_pressure_et_inodes_sur_worker2_gardent_le_chemin_d_origine(self):
        """Consigne : NODE_DISK_PRESSURE / NODE_INODES_FULL ne sont PAS modifies (memes cibles qu'avant, ancien drapeau)."""
        for type_ in ("NODE_DISK_PRESSURE", "NODE_INODES_FULL"):
            client = FauxClient()
            b = E.BackendModele(client, drapeau_scenario=False, drapeau_legacy=True, legacy_succes=True)
            plan, raison = b.demarrer_si_eligible(incident(type_, "k8s-worker2"), 0.0)
            self.assertIsNotNone(plan, raison)
            res = b.executer(plan)
            self.assertEqual(("pod_hote", True), (res["voie"], res["succes"]))
            self.assertEqual([("k8s-worker2", type_)], b.appels_legacy)
            self.assertEqual([], client.appels, "jamais le Bridge")
            ferme = E.BackendModele(FauxClient(), drapeau_scenario=True, drapeau_legacy=False)
            self.assertIsNone(ferme.demarrer_si_eligible(incident(type_, "k8s-worker2"), 0.0)[0], "le drapeau du scenario n'ouvre rien d'autre")

    def test_un_type_hors_disque_est_ignore(self):
        for type_ in ("NODE_HIGH_CPU", "NODE_NOT_READY", "POD_CRASH_LOOP", "NODE_HIGH_MEMORY"):
            with self.subTest(type=type_):
                plan, raison = self.backend().demarrer_si_eligible(incident(type_), 0.0)
                self.assertIsNone(plan)
                self.assertIn("hors DISK", raison)

    def test_drapeau_du_scenario_inactif_aucune_action_ni_requete_meme_si_l_ancien_drapeau_est_actif(self):
        for legacy in (False, True):
            client = FauxClient()
            b = E.BackendModele(client, drapeau_scenario=False, drapeau_legacy=legacy)
            inc = incident()
            plan, raison = b.demarrer_si_eligible(inc, 0.0)
            self.assertIsNone(plan)
            self.assertEqual("OPEN", inc["statut"])
            self.assertIn("HOST_DISK_E2E_158_ENABLED=false", " ".join(b.historique_incident))
            res = b.executer({"cible": "k8s-worker1", "ns": "k8s-nodes", "type": "NODE_DISK_FULL", "incident_id": 1})
            self.assertFalse(res["succes"])
            self.assertEqual("refus", res["voie"], "aucun repli vers le pod hote")
            self.assertEqual(([], []), (client.appels, b.appels_legacy))

    def test_cibles_et_namespaces_hors_perimetre_sont_refuses(self):
        for app, ns in (("k8s-master", "k8s-nodes"), ("k8s-worker3", "k8s-nodes"), ("monitoring", "k8s-nodes"), ("k8s-worker1", "pfe")):
            with self.subTest(app=app, ns=ns):
                client = FauxClient()
                plan, _ = E.BackendModele(client).demarrer_si_eligible(incident(app=app, ns=ns), 0.0)
                self.assertIsNone(plan)
                self.assertEqual([], client.appels)

    def test_automation_desactivee_ou_confiance_basse_pas_d_execution_automatique(self):
        for kw in ({"automation": False}, {"confiance": 0.2}):
            with self.subTest(kw=kw):
                plan, _ = self.backend(**kw).demarrer_si_eligible(incident(), 0.0)
                self.assertIsNone(plan)

    def test_cooldown_du_backend(self):
        t = 5000.0
        recente = [(t - 600, "k8s-worker1", "HOST_RECLAIM_DISK", "NODE_DISK_FULL")]
        ancienne = [(t - 1801, "k8s-worker1", "HOST_RECLAIM_DISK", "NODE_DISK_FULL")]
        autre_cible = [(t - 60, "k8s-worker2", "HOST_RECLAIM_DISK", "NODE_DISK_FULL")]
        autre_type = [(t - 600, "k8s-worker1", "HOST_RECLAIM_DISK", "NODE_INODES_FULL")]
        for hist, attendu in ((recente, True), (ancienne, False), (autre_cible, False), (autre_type, False), ([], False)):
            with self.subTest(historique=hist):
                plan, raison = E.BackendModele(FauxClient(), historique=hist).demarrer_si_eligible(incident(), t)
                self.assertEqual(attendu, plan is None, raison)

    def test_le_backend_n_ajoute_son_action_a_l_historique_qu_a_la_creation_du_plan(self):
        b = self.backend()
        b.demarrer_si_eligible(incident(), 100.0)
        self.assertEqual(1, len(b.historique))
        plan2, _ = b.demarrer_si_eligible(incident(iid=4243), 200.0)
        self.assertIsNone(plan2, "un second incident du meme type dans le cooldown n'est pas remedie automatiquement")

    def test_un_incident_sans_identifiant_valide_n_envoie_rien(self):
        for iid in (None, 0, -3, "12"):
            with self.subTest(iid=iid):
                client = FauxClient()
                res = E.BackendModele(client).executer({"cible": "k8s-worker1", "ns": "k8s-nodes", "type": "NODE_DISK_FULL", "incident_id": iid})
                self.assertFalse(res["succes"])
                self.assertEqual([], client.appels)

    def test_succes_exige_ok_execute_et_code_zero(self):
        for rep, attendu in ((E.Reponse(True, True, 0, "x"), True), (E.Reponse(True, False, 0, "x"), False),
                             (E.Reponse(False, True, -1, "x"), False), (E.Reponse(True, True, 2, "x"), False)):
            with self.subTest(rep=rep):
                b = E.BackendModele(FauxClient(rep))
                plan, _ = b.demarrer_si_eligible(incident(), 0.0)
                self.assertEqual(attendu, b.executer(plan)["succes"])

    def test_dans_le_monde_la_demande_n_est_pas_remplacee_par_host_image_gc(self):
        ctx = self.ctx()
        out = self.lancer(ctx)
        self.assertEqual("RESOLVED_E2E", out["verdict"])
        self.assertEqual(["HOST_RECLAIM_DISK"], [c[1]["action"] for c in ctx.bridge.requetes])
        self.assertFalse(any("journalctl" in c or "find /var/log" in c for c in ctx.bridge.exec.commandes))


# ================================================================================ preuve 3 : nettoyage exclusif du scenario
class NettoyageExclusifE2ETests(Base):
    def test_un_fichier_etranger_du_repertoire_n_est_ni_supprime_ni_modifie(self):
        ctx = self.ctx()
        a_linstant(ctx.monde, 50, lambda m: m.fichiers.__setitem__("README", 120))
        out = self.lancer(ctx)
        self.assertEqual("RESOLVED_E2E", out["verdict"], out.get("manque"))
        self.assertEqual({"README": 120}, ctx.monde.fichiers, "seul le fichier etranger subsiste, intact")
        self.assertEqual(1, len(ctx.bridge.exec.rm))
        self.assertNotIn("README", ctx.bridge.exec.rm[0])
        self.assertTrue(all(H.RE_FICHIER.match(n) for n in ctx.bridge.exec.rm[0]))
        corps = ctx.bridge.reponses[0][1]
        self.assertEqual(1, corps["etrangers"], "le fichier etranger est compte, jamais touche")
        self.assertEqual(len(out["res"]["alloues"]), corps["supprimes"])

    def test_un_second_jeton_present_fait_refuser_le_bridge_et_ses_fichiers_restent(self):
        ctx = self.ctx()
        autre = "helios-diskfill-%s-001.bin" % ("f" * 32)
        a_linstant(ctx.monde, 50, lambda m: m.fichiers.__setitem__(autre, 100 * MIO))
        out = self.lancer(ctx)
        self.assertEqual("ECHEC_E2E", out["verdict"], "le Bridge ne devine jamais lequel des deux jetons est le bon")
        self.assertEqual([], ctx.bridge.exec.rm, "aucune suppression par le Bridge")
        self.assertIn(autre, ctx.monde.fichiers, "les fichiers de l'autre jeton ne sont jamais touches")
        corps = ctx.bridge.reponses[0][1]
        self.assertEqual("AMBIGUOUS_TOKENS", corps["rejet"])
        self.assertFalse(corps["ok"])
        self.assertEqual("RESOLUTION_FAILED", ctx.monde.incident["statut"])
        self.assertIn("ESCALATE", [e for _, e, _ in ctx.monde.chaine], "un echec est une escalade, jamais un resultat nominal")
        res = out["res"]
        self.assertTrue(res["liberation_ok"], "l'outil libere lui-meme SES fichiers")
        self.assertEqual([autre], sorted(ctx.monde.fichiers))

    def test_un_repertoire_remplace_par_un_lien_ou_aux_droits_modifies_est_refuse(self):
        for panne in ("dossier_lien", "dossier_proprio", "dossier_mode"):
            with self.subTest(panne=panne):
                ctx = self.ctx()
                a_linstant(ctx.monde, 50, lambda m, p=panne: m.pannes.__setitem__(p, True))
                out = self.lancer(ctx)
                self.assertEqual("ECHEC_E2E", out["verdict"])
                self.assertEqual([], ctx.bridge.exec.rm, "aucune suppression dans un repertoire non conforme")
                self.assertEqual("UNSAFE_DIRECTORY", ctx.bridge.reponses[0][1]["rejet"])
                self.assertTrue(out["res"]["liberation_ok"])

    def test_la_suppression_est_verifiee_par_relecture_pas_par_le_code_retour(self):
        ctx = self.ctx(pannes={"bridge_rm_sans_effet": True})
        out = self.lancer(ctx)
        self.assertEqual("ECHEC_E2E", out["verdict"])
        corps = ctx.bridge.reponses[0][1]
        self.assertEqual("DELETION_INCOMPLETE", corps["rejet"])
        self.assertFalse(corps["ok"])
        self.assertEqual("RESOLUTION_FAILED", ctx.monde.incident["statut"])
        self.assertTrue(out["res"]["liberation_ok"], "l'outil libere ses fichiers (le Bridge n'a rien pu faire)")
        self.assertEqual({}, ctx.monde.fichiers)

    def test_les_commandes_du_bridge_ne_visent_que_le_repertoire_exclusif(self):
        ctx = self.ctx()
        out = self.lancer(ctx)
        self.assertEqual("RESOLVED_E2E", out["verdict"])
        for c in ctx.bridge.exec.commandes:
            if c.startswith(E.PREFIXE_RM):
                for chemin in c[len(E.PREFIXE_RM):].split():
                    self.assertTrue(chemin.startswith(E.DOSSIER + "/helios-diskfill-"), chemin)
            else:
                self.assertIn(c, (E.CMD_STAT_DIR, E.CMD_FIND, E.CMD_STAT_FS))


# ================================================================== preuve 4 : verification <= 77 %, jamais d'echec en RESOLVED
class VerificationE2ETests(Base):
    def test_occupation_encore_trop_haute_apres_la_suppression_n_est_jamais_resolved(self):
        ctx = self.ctx(pannes={"bridge_croissance_apres_rm": 0.115})     # un tiers ecrit juste apres le rm : 66,85 % + 11,5 % = 78,35 %
        out = self.lancer(ctx)
        self.assertEqual("ECHEC_E2E", out["verdict"])
        corps = ctx.bridge.reponses[0][1]
        self.assertEqual("NOT_RECOVERED", corps["rejet"])
        self.assertFalse(corps["ok"])
        self.assertGreater(corps["usage_apres_pct"], 77.0)
        self.assertEqual(len(out["res"]["alloues"]), corps["supprimes"], "les fichiers du scenario ont bien ete supprimes")
        self.assertEqual("RESOLUTION_FAILED", ctx.monde.incident["statut"])
        self.assertNotEqual("RESOLVED", out["res"]["statut"])
        self.assertIn("recupere", out["manque"])
        self.assertFalse(out["res"]["recupere"])
        self.assertEqual(0, len([e for _, e, _ in ctx.monde.chaine if e == "VERIFY"]), "la verification n'est meme pas tentee")

    def test_bridge_ok_mais_prometheus_reste_au_dessus_de_77_apres_8_echantillons(self):
        ctx = self.ctx()
        a_linstant(ctx.monde, 117, lambda m: setattr(m, "organique", m.organique + 0.115 * m.taille))   # juste apres l'action du Bridge
        out = self.lancer(ctx)
        self.assertEqual("ECHEC_E2E", out["verdict"])
        verifs = [d for _, e, d in ctx.monde.chaine if e == "VERIFY"]
        self.assertEqual(8, len(verifs), "8 echantillons au maximum (Math.min(8, 10))")
        self.assertTrue(all("NON sain" in v for v in verifs))
        self.assertEqual("RESOLUTION_FAILED", ctx.monde.incident["statut"])
        self.assertNotIn("RESOLVED", [e for _, e, _ in ctx.monde.chaine])
        self.assertIn("ESCALATE", [e for _, e, _ in ctx.monde.chaine])
        self.assertIn("incident_resolu", out["manque"])
        self.assertIn("aucune_escalade", out["manque"])
        self.assertFalse(out["res"]["recupere"])

    def test_un_echantillon_non_sain_remet_le_compteur_a_zero(self):
        b = E.BackendModele(FauxClient())
        etat = b.nouvelle_verification(0, 30)
        suite = [66.0, 78.0, 66.0, 66.0]
        etats = [b.echantillon(etat, v, 30 * (i + 1))[0] for i, v in enumerate(suite)]
        self.assertEqual(["CONTINUE", "CONTINUE", "CONTINUE", "RESOLU"], etats, "sain, NON sain, sain, sain : 2 consecutifs au 4e")

    def test_jamais_resolu_sur_une_alternance(self):
        b = E.BackendModele(FauxClient())
        etat = b.nouvelle_verification(0, 30)
        etats = [b.echantillon(etat, 66.0 if i % 2 == 0 else 80.0, 30 * (i + 1))[0] for i in range(8)]
        self.assertEqual(["CONTINUE"] * 7 + ["ECHEC"], etats)

    def test_frontiere_77_inclusive(self):
        for valeur, attendu in ((77.0, "sain"), (76.99, "sain"), (77.01, "NON sain"), (84.0, "NON sain"), (85.0, "NON sain")):
            with self.subTest(valeur=valeur):
                b = E.BackendModele(FauxClient())
                _, msg = b.echantillon(b.nouvelle_verification(0, 30), valeur, 30)
                self.assertIn("-> " + attendu + " ", msg)

    def test_une_mesure_absente_ou_hors_plage_n_est_jamais_saine(self):
        for valeur in (None, -1.0, 100.5):
            with self.subTest(valeur=valeur):
                b = E.BackendModele(FauxClient())
                etat = b.nouvelle_verification(0, 30)
                self.assertEqual("CONTINUE", b.echantillon(etat, valeur, 30)[0])
                self.assertEqual(0, etat["sains"])

    def test_le_seuil_des_inodes_reste_85(self):
        b = E.BackendModele(FauxClient())
        _, msg = b.echantillon(b.nouvelle_verification(0, 30), 84.0, 30, type_incident="NODE_INODES_FULL")
        self.assertIn("-> sain ", msg)

    def test_la_verification_de_l_outil_refuse_un_resolved_sans_recuperation(self):
        """Meme si l'incident etait RESOLVED, l'outil n'accorde OK que si l'occupation FINALE mesuree est <= 77 %."""
        ctx = self.ctx()
        a_linstant(ctx.monde, 160, lambda m: setattr(m, "organique", m.organique + 0.12 * m.taille))
        out = self.lancer(ctx)
        self.assertNotEqual("RESOLVED_E2E", out["verdict"])


# ================================================================ preuve 5 : deuxieme execution sans double suppression
class IdempotenceE2ETests(Base):
    def test_rejouer_le_nettoyage_apres_le_scenario_ne_supprime_rien(self):
        ctx = self.ctx()
        out = self.lancer(ctx)
        self.assertEqual("RESOLVED_E2E", out["verdict"])
        self.assertEqual(1, len(ctx.bridge.exec.rm))
        with ctx.bridge:
            plan = {"cible": "k8s-worker1", "ns": "k8s-nodes", "type": "NODE_DISK_FULL", "incident_id": 90001}
            rep1 = ctx.backend.executer(plan)
            rep2 = ctx.backend.executer(plan)
        self.assertTrue(rep1["succes"] and rep2["succes"], "rejouer reste un succes : occupation mesuree deja <= 77 %")
        self.assertEqual(1, len(ctx.bridge.exec.rm), "AUCUNE seconde suppression")
        for statut, corps in ctx.bridge.reponses[-2:]:
            self.assertTrue(corps["noop"])
            self.assertEqual(0, corps["supprimes"])
            self.assertEqual(0, corps["octets_liberes"])
            self.assertLessEqual(corps["usage_apres_pct"], 77.0)

    def test_deux_executions_du_travail_camunda_pendant_le_scenario_ne_suppriment_qu_une_fois(self):
        ctx = self.ctx()
        original = ctx.backend.executer

        def doublon(plan):
            premiere = original(plan)
            seconde = original(plan)             # un job Camunda rejoue (au moins une fois)
            self.assertTrue(premiere["succes"] and seconde["succes"])
            return premiere

        ctx.backend.executer = doublon
        out = self.lancer(ctx)
        self.assertEqual("RESOLVED_E2E", out["verdict"], out.get("manque"))
        self.assertEqual(1, len(ctx.bridge.exec.rm), "une seule suppression malgre deux requetes")
        self.assertEqual(2, len([c for c, _ in ctx.bridge.requetes if c == "/action"]))
        corps = [c for _, c in ctx.bridge.reponses]
        self.assertFalse(corps[0]["noop"])
        self.assertTrue(corps[1]["noop"])

    def test_deux_scenarios_successifs_sont_independants_et_propres(self):
        for _ in range(2):
            ctx = self.ctx()
            out = self.lancer(ctx)
            self.assertEqual("RESOLVED_E2E", out["verdict"])
            self.assertEqual({}, ctx.monde.fichiers)
        self.assertEqual({}, E.bridge_module()._DISQUE_LIAISONS)

    def test_une_requete_http_rejouee_directement_est_un_noop(self):
        ctx = self.ctx()
        self.lancer(ctx)
        charge = {"action": "HOST_RECLAIM_DISK", "type": "NODE_DISK_FULL", "ns": "k8s-nodes", "cible": "k8s-worker1", "noeud": "k8s-worker1",
                  "scenario": "DISK_E2E_158", "incident_id": "90001"}
        avant = len(ctx.bridge.exec.rm)
        with ctx.bridge:
            for _ in range(3):
                statut, corps = ctx.bridge.poster("/action", charge)
                self.assertEqual(200, statut)
                self.assertTrue(corps["ok"] and corps["noop"])
        self.assertEqual(avant, len(ctx.bridge.exec.rm))


# =========================================================== preuve 6 : l'expiration libere si Python disparait
class ExpirationSansPythonTests(Base):
    def monde_sans_helios(self):
        ctx = self.ctx()
        return H.MondeSimule(ctx.fs.taille, ctx.fs.dispo, helios="absent", hostname=E.NOEUD_K8S)

    def _remplir_puis_mourir(self, monde, jeton):
        """Ce que l'outil fait jusqu'a la fin de l'injection ; puis le processus Python MEURT : plus aucun appel (pas de finally)."""
        p = E.parametres()
        ops = H.NoeudSimule(monde)
        noms = H.noms_prevus(jeton)
        ops.creer_dossier()
        ops.armer_expiration(jeton, noms, p.expiration)
        ops.armer_garde(jeton, noms, H.seuil_garde_x100(p), p.expiration)
        for i, bloc in enumerate(H.planifier(ops.stats(), p), 1):
            ops.allouer(H.nom_fichier(jeton, i), bloc)
        return p

    def test_le_minuteur_libere_les_fichiers_sans_aucun_appel_de_l_outil(self):
        monde = self.monde_sans_helios()
        jeton = "ab" * 16
        p = self._remplir_puis_mourir(monde, jeton)
        self.assertGreater(H.Fs(monde.taille, monde.dispo()).usage, 80.0)
        self.assertTrue(monde.fichiers)
        monde.avancer(p.expiration - 5)
        self.assertTrue(monde.fichiers, "avant l'echeance, rien n'est libere")
        monde.avancer(10)
        self.assertEqual({}, monde.fichiers, "le minuteur a libere les fichiers")
        self.assertIn("expiration", [j[1] for j in monde.journal])
        self.assertLessEqual(H.Fs(monde.taille, monde.dispo()).usage, 77.0)
        self.assertEqual([], [a for a in monde.appels if a in ("rm", "systemctl-stop")], "l'outil n'a plus rien appele")

    def test_la_garde_libere_des_que_l_occupation_atteint_83_5_pour_cent(self):
        monde = self.monde_sans_helios()
        jeton = "cd" * 16
        self._remplir_puis_mourir(monde, jeton)
        monde.organique += 0.036 * monde.taille              # un tiers ecrit : 81 % + 3,6 % = 84,6 %
        self.assertGreaterEqual(H.Fs(monde.taille, monde.dispo()).usage, 83.5)
        monde.avancer(4)
        self.assertEqual({}, monde.fichiers, "la garde a libere les fichiers")
        self.assertIn("garde", [j[1] for j in monde.journal])
        self.assertLess(H.Fs(monde.taille, monde.dispo()).usage, 83.5)

    def test_la_garde_ne_tire_pas_avant_83_5(self):
        monde = self.monde_sans_helios()
        self._remplir_puis_mourir(monde, "ef" * 16)
        monde.organique += 0.015 * monde.taille              # 81 % + 1,5 % = 82,5 % < 83,5 %
        monde.avancer(10)
        self.assertTrue(monde.fichiers)
        self.assertNotIn("garde", [j[1] for j in monde.journal])

    def test_le_bridge_libere_d_abord_puis_le_minuteur_tire_a_vide_sans_dommage(self):
        ctx = self.ctx()
        jeton = "12" * 16
        p = E.parametres()
        with ctx.bridge:
            ops = H.NoeudSimule(ctx.monde)
            noms = H.noms_prevus(jeton)
            ops.creer_dossier()
            ops.armer_expiration(jeton, noms, p.expiration)
            ops.armer_garde(jeton, noms, H.seuil_garde_x100(p), p.expiration)
            for i, bloc in enumerate(H.planifier(ops.stats(), p), 1):
                ops.allouer(H.nom_fichier(jeton, i), bloc)
            ctx.monde.avancer(400)                          # HELIOS detecte, remedie via le Bridge et resout ; le processus Python est mort
        self.assertEqual("RESOLVED", ctx.monde.incident["statut"])
        self.assertEqual({}, ctx.monde.fichiers)
        usage = H.Fs(ctx.monde.taille, ctx.monde.dispo()).usage
        ctx.monde.avancer(p.expiration)                     # l'echeance du minuteur passe : il tire sur des fichiers deja absents
        self.assertIn("expiration", [j[1] for j in ctx.monde.journal])
        self.assertEqual({}, ctx.monde.fichiers)
        self.assertEqual(usage, H.Fs(ctx.monde.taille, ctx.monde.dispo()).usage, "aucun effet : pas de double suppression, pas d'erreur")
        self.assertEqual(1, len(ctx.bridge.exec.rm))

    def test_dans_le_scenario_complet_les_filets_n_ont_jamais_tire(self):
        ctx = self.ctx()
        out = self.lancer(ctx)
        self.assertEqual("RESOLVED_E2E", out["verdict"])
        self.assertEqual([], [j for j in ctx.monde.journal if j[1] in ("expiration", "garde")])


# ============================================================================== preuve 7 : incidents DISK etrangers
class IncidentsEtrangersE2ETests(Base):
    def test_un_incident_node_disk_full_sur_worker2_ne_touche_jamais_les_fichiers_de_worker1(self):
        ctx = self.ctx()
        jeton = "34" * 16
        ctx.monde.dossier = {}
        for i in range(1, 4):
            ctx.monde.fichiers[H.nom_fichier(jeton, i)] = 512 * MIO
        avant = dict(ctx.monde.fichiers)
        with ctx.bridge:
            for scenario, legacy in ((True, False), (False, True), (True, True)):
                ctx.backend.drapeau_scenario, ctx.backend.drapeau_legacy = scenario, legacy
                plan, raison = ctx.backend.demarrer_si_eligible(incident("NODE_DISK_FULL", app="k8s-worker2", iid=555), 0.0)
                self.assertIsNone(plan, "NODE_DISK_FULL sur k8s-worker2 (.155) n'est JAMAIS eligible : " + raison)
            # PRESSURE / INODES de k8s-worker2 : chemin d'origine inchange (pod hote), jamais le Bridge ni les fichiers du scenario
            ctx.backend.drapeau_scenario, ctx.backend.drapeau_legacy = False, True
            plan, _ = ctx.backend.demarrer_si_eligible(incident("NODE_DISK_PRESSURE", app="k8s-worker2", iid=556), 0.0)
            self.assertEqual("pod_hote", ctx.backend.executer(plan)["voie"])
        self.assertEqual([], ctx.bridge.requetes)
        self.assertEqual([], ctx.bridge.exec.commandes)
        self.assertEqual(avant, ctx.monde.fichiers)
        self.assertEqual([("k8s-worker2", "NODE_DISK_PRESSURE")], ctx.backend.appels_legacy, "le seul appel legacy est PRESSURE (comme avant)")

    def test_un_incident_disque_reel_sur_158_sans_fichier_du_scenario_n_est_ni_touche_ni_resolu(self):
        ctx = self.ctx(usage=86.0)                               # disque reellement plein (86 %), AUCUN fichier du scenario
        ctx.monde.fichiers["journal-applicatif.log"] = 700 * MIO
        ctx.monde.dossier = {}
        avant = dict(ctx.monde.fichiers)
        with ctx.bridge:
            ctx.monde.avancer(900)
        i = ctx.monde.incident
        self.assertIsNotNone(i)
        self.assertEqual("RESOLUTION_FAILED", i["statut"], "jamais RESOLVED : rien n'a ete libere")
        self.assertEqual([], ctx.bridge.exec.rm, "AUCUNE suppression")
        self.assertEqual(avant, ctx.monde.fichiers)
        corps = ctx.bridge.reponses[0][1]
        self.assertEqual("NO_SCENARIO_FILES", corps["rejet"])
        self.assertFalse(corps["ok"])
        self.assertIn("RIEN n a ete touche", corps["stderr"])
        self.assertIn("ESCALATE", [e for _, e, _ in ctx.monde.chaine])
        self.assertNotIn("RESOLVED", [e for _, e, _ in ctx.monde.chaine])

    def test_un_incident_etranger_avec_un_repertoire_de_scenario_absent_est_refuse_de_meme(self):
        ctx = self.ctx(usage=88.0)
        with ctx.bridge:
            ctx.monde.avancer(900)
        self.assertEqual("RESOLUTION_FAILED", ctx.monde.incident["statut"])
        self.assertEqual([], ctx.bridge.exec.rm)
        self.assertEqual("NO_SCENARIO_FILES", ctx.bridge.reponses[0][1]["rejet"])

    def test_le_bridge_refuse_un_scenario_ou_une_cible_etrangers_au_niveau_http(self):
        ctx = self.ctx()
        base = {"action": "HOST_RECLAIM_DISK", "type": "NODE_DISK_FULL", "ns": "k8s-nodes", "cible": "k8s-worker1", "noeud": "k8s-worker1",
                "scenario": "DISK_E2E_158", "incident_id": "9"}
        cas = [({"scenario": "AUTRE"}, "REJECTED_SCENARIO"), ({"cible": "k8s-worker2", "noeud": "k8s-worker2"}, "REJECTED_TARGET_NOT_ALLOWLISTED"),
               ({"type": "NODE_INODES_FULL"}, "REJECTED_TYPE"), ({"ns": "pfe"}, "REJECTED_NAMESPACE")]
        with ctx.bridge:
            for patch, rejet in cas:
                with self.subTest(patch=patch):
                    statut, corps = ctx.bridge.poster("/action", dict(base, **patch))
                    self.assertEqual(rejet, corps["rejet"])
                    self.assertFalse(corps["ok"])
        self.assertEqual([], ctx.bridge.exec.commandes, "aucune commande pour une requete refusee")

    def test_un_jeton_http_invalide_est_refuse_avant_toute_action(self):
        ctx = self.ctx()
        with ctx.bridge:
            statut, corps = ctx.bridge.poster("/action", {"action": "HOST_RECLAIM_DISK"}, jeton="faux")
        self.assertEqual(401, statut)
        self.assertEqual([], ctx.bridge.exec.commandes)


# ================================================================================ pannes de composants (echec ferme)
class PannesComposantsTests(Base):
    def verifier_echec(self, ctx, out, nom=""):
        self.assertEqual("ECHEC_E2E", out["verdict"], nom)
        self.assertNotEqual("RESOLVED", out["res"]["statut"], nom)
        self.assertNotIn("RESOLVED", [e for _, e, _ in ctx.monde.chaine], nom)
        self.assertTrue(out["res"]["liberation_ok"], "le noeud est laisse propre : " + nom)
        self.assertEqual({}, ctx.monde.fichiers, nom)

    def test_l_ancien_drapeau_seul_n_ouvre_pas_le_scenario(self):
        ctx = self.ctx(drapeau_scenario=False, drapeau_legacy=True)
        out = self.lancer(ctx)
        self.verifier_echec(ctx, out)
        self.assertEqual("OPEN", ctx.monde.incident["statut"])
        self.assertIsNone(ctx.monde.action, "aucune action creee")
        self.assertEqual([], ctx.bridge.requetes)
        self.assertEqual([], ctx.backend.appels_legacy, "aucun repli vers le pod hote prive")

    def test_drapeau_du_scenario_inactif_dans_le_backend_aucune_remediation(self):
        ctx = self.ctx(drapeau_scenario=False)
        out = self.lancer(ctx)      # la garde se fie aux faits (ici « true ») : on prouve ce qui se passe si le Backend deployé dit autre chose
        self.verifier_echec(ctx, out)
        self.assertEqual("OPEN", ctx.monde.incident["statut"])
        self.assertIsNone(ctx.monde.action, "aucune action creee")
        self.assertEqual([], ctx.bridge.requetes)
        self.assertEqual("DETECTION_SEULE", out["res"]["verdict"])

    def test_bridge_desactive_dans_le_backend(self):
        ctx = self.ctx(bridge_actif=False)
        out = self.lancer(ctx)
        self.verifier_echec(ctx, out)
        self.assertEqual([], ctx.bridge.requetes)
        self.assertIn("Bridge desactive", " ".join(d for _, e, d in ctx.monde.chaine if e == "BRIDGE"))

    def test_bridge_injoignable_par_ssh(self):
        ctx = self.ctx(pannes={"bridge_ssh_ko": True})
        out = self.lancer(ctx)
        self.verifier_echec(ctx, out)
        self.assertEqual("DIRECTORY_UNREADABLE", ctx.bridge.reponses[0][1]["rejet"])
        self.assertEqual([], ctx.bridge.exec.rm)

    def test_jeton_du_bridge_incorrect(self):
        ctx = self.ctx()
        ctx.client.jeton = "mauvais-jeton"
        out = self.lancer(ctx)
        self.verifier_echec(ctx, out)
        self.assertEqual(401, ctx.bridge.reponses[0][0])
        self.assertEqual([], ctx.bridge.exec.commandes)

    def test_rm_en_echec(self):
        ctx = self.ctx(pannes={"bridge_rm_echoue": True})
        out = self.lancer(ctx)
        self.verifier_echec(ctx, out)
        self.assertEqual("DELETION_INCOMPLETE", ctx.bridge.reponses[0][1]["rejet"])

    def test_cooldown_du_backend_empeche_la_remediation(self):
        ctx = self.ctx(historique=[(-600.0, "k8s-worker1", "HOST_RECLAIM_DISK", "NODE_DISK_FULL")])
        out = self.lancer(ctx)      # la garde ne voit pas cet historique ici : le Backend, lui, bloque (cas que la garde existe pour prevenir)
        self.verifier_echec(ctx, out)
        self.assertIn("REFUS_POLITIQUE", [e for _, e, _ in ctx.monde.chaine])
        self.assertIsNone(ctx.monde.action, "un refus de politique ne cree AUCUNE action")
        self.assertEqual("OPEN", ctx.monde.incident["statut"])
        self.assertEqual([], ctx.bridge.requetes)

    def test_automation_desactivee(self):
        ctx = self.ctx(automation=False)
        out = self.lancer(ctx)
        self.verifier_echec(ctx, out)
        self.assertIsNone(ctx.monde.action)
        self.assertEqual([], ctx.bridge.requetes)

    def test_protection_systemd_non_armee_aucune_allocation(self):
        for panne in ("armer", "armer_garde", "expiration_inactive", "garde_inactive"):
            with self.subTest(panne=panne):
                ctx = self.ctx(pannes={panne: True})
                out = self.lancer(ctx)
                self.assertEqual("ECHEC_E2E", out["verdict"])
                self.assertIn(out["res"]["verdict"], ("REFUS", "ERREUR"), "refus de securite ou erreur du noeud : jamais OK")
                self.assertEqual([], out["res"]["alloues"], "aucune charge injectee : les filets ne sont pas verifies actifs")
                self.assertFalse(out["injection"])
                self.assertIsNone(ctx.monde.incident, "aucun incident artificiel")
                self.assertEqual({}, ctx.monde.fichiers)
                self.assertEqual([], ctx.bridge.requetes)

    def test_identite_du_noeud_incorrecte_aucune_ecriture(self):
        ctx = self.ctx()
        ctx.monde.hostname = "k8s-worker2"
        out = self.lancer(ctx)
        self.assertEqual("REFUS", out["res"]["verdict"])
        self.assertEqual([], out["res"]["alloues"])
        self.assertEqual({}, ctx.monde.fichiers)
        self.assertIsNone(ctx.monde.dossier)

    def test_arret_d_urgence_pendant_le_suivi_libere_immediatement(self):
        ctx = self.ctx()
        a_linstant(ctx.monde, 40, lambda m: setattr(m, "organique", m.organique + 0.03 * m.taille))   # 81 % + 3 % = 84 % > urgence
        out = self.lancer(ctx)
        self.assertIn(out["res"]["verdict"], ("URGENCE",))
        self.assertEqual("ECHEC_E2E", out["verdict"])
        self.assertEqual({}, ctx.monde.fichiers)
        self.assertEqual([], ctx.bridge.requetes, "l'urgence precede la detection : le Bridge n'a pas eu a agir")


# ====================================================================================================== demonstration
class DemonstrationTests(Base):
    def test_demonstration_complete(self):
        lignes = []
        out = E.demonstration(lignes.append)
        self.assertEqual("RESOLVED_E2E", out["verdict"])
        texte = "\n".join(lignes)
        self.assertIn("DETECTION -> INCIDENT -> HOST_RECLAIM_DISK -> BRIDGE -> VERIFY -> RESOLVED", texte)
        self.assertIn("SCENARIO DISK_E2E_158 : RESOLVED_E2E", texte)
        self.assertNotIn("ESCALATE", texte)
        self.assertNotIn("preuves manquantes", texte)

    def test_cli_demo_et_refus(self):
        self.assertEqual(0, E.principal([], sortie=muet))
        self.assertEqual(0, E.principal(["--demo"], sortie=muet))
        self.assertEqual(0, E.principal(["--refus"], sortie=muet))

    def test_refus_affiche_aucune_charge_et_aucun_incident(self):
        lignes = []
        self.assertEqual(0, E.principal(["--refus"], sortie=lignes.append))
        texte = "\n".join(lignes)
        self.assertIn("AUCUNE charge injectee, AUCUN incident cree", texte)
        self.assertIn("REFUS_PREREQUIS", texte)
        self.assertNotIn("bloc 1", texte)

    def test_determinisme(self):
        a = E.demonstration(muet)
        b = E.demonstration(muet)
        chaine = lambda o: [(t, e) for t, e, _ in o["ctx"].monde.chaine]
        self.assertEqual(chaine(a), chaine(b), "memes parametres, memes delais : meme chaine, a la seconde pres")


# ===================================================================== collecte REELLE (lecture seule) de la porte, sur emulateur
class ExecScripte(object):
    """Executeur minimal : repond a chaque commande par une reponse scriptee (cle = tuple argv). Toute autre commande = echec 127."""

    def __init__(self, ssh=None, local=None):
        self.reponses_ssh, self.reponses_local = dict(ssh or {}), dict(local or {})
        self.appels = []

    def ssh(self, ip, argv, delai=45):
        self.appels.append(("ssh", ip, list(argv)))
        return self.reponses_ssh.get((ip, tuple(argv)), H.Res(127, "", "commande inconnue"))

    def local(self, argv, delai=40, entree=None):
        self.appels.append(("local", list(argv), entree))
        return self.reponses_local.get(tuple(argv), H.Res(127, "", "commande inconnue"))


class LecturesDeLaPorteTests(Base):
    """Chaque lecture : une valeur exacte quand tout va bien, None (=> ROUGE) quand quoi que ce soit est illisible ou incoherent."""

    def test_lire_bridge(self):
        ip, cmd = E.IP_BRIDGE, ("curl", "-sS", "--max-time", "8", E.URL_ACTIONS_BRIDGE)
        sha = "ab" * 32
        sha_cmd = (ip, ("sha256sum", E.BRIDGE_DEPLOYE))
        bon = ExecScripte(ssh={(ip, cmd): H.Res(0, '{"catalogue": ["RESTART_POD", "HOST_RECLAIM_DISK"]}', ""),
                               sha_cmd: H.Res(0, sha + "  " + E.BRIDGE_DEPLOYE + "\n", "")})
        self.assertEqual({"joignable": True, "capacites": ["HOST_RECLAIM_DISK", "RESTART_POD"], "sha256": sha}, E.lire_bridge(bon))
        cas = (("curl en echec", H.Res(7, "", "refused"), {"joignable": False, "capacites": None}),
               ("reponse non JSON", H.Res(0, "<html>", ""), {"joignable": False, "capacites": None}),
               ("JSON sans catalogue", H.Res(0, "{}", ""), {"joignable": True, "capacites": None}),
               ("catalogue vide", H.Res(0, '{"catalogue": []}', ""), {"joignable": True, "capacites": None}),
               ("catalogue de non-textes", H.Res(0, '{"catalogue": [1, 2]}', ""), {"joignable": True, "capacites": None}))
        for nom, rep, attendu in cas:
            with self.subTest(nom):
                r = E.lire_bridge(ExecScripte(ssh={(ip, cmd): rep, sha_cmd: H.Res(0, sha + "  x\n", "")}))
                self.assertEqual(attendu, {k: r[k] for k in attendu})
        for nom, rep in (("sha256sum en echec", H.Res(1, "", "")), ("empreinte trop courte", H.Res(0, "abc  f\n", "")),
                         ("empreinte majuscule", H.Res(0, "AB" * 32 + "  f\n", ""))):
            with self.subTest(nom):
                self.assertIsNone(E.lire_bridge(ExecScripte(ssh={(ip, cmd): H.Res(0, '{"catalogue": ["X"]}', ""), sha_cmd: rep}))["sha256"])

    def test_lire_systemd(self):
        ip = "192.168.148.158"
        base = {(ip, ("test", "-x", "/usr/bin/systemd-run")): H.Res(0, "", ""), (ip, ("test", "-x", "/usr/bin/awk")): H.Res(0, "", ""),
                (ip, ("systemctl", "list-units", "--all", "--no-legend", "--plain", "helios-diskfill-*")): H.Res(0, "", "")}
        r = E.lire_systemd(ExecScripte(ssh=base), ip)
        self.assertEqual((True, True, [], True), (r["systemd_run"], r["awk"], r["unites_residuelles"], r["garde_autotest"]))
        for rc, attendu in ((0, True), (1, False), (255, None), (2, None)):
            b = dict(base)
            b[(ip, ("test", "-x", "/usr/bin/awk"))] = H.Res(rc, "", "")
            self.assertIs(attendu, E.lire_systemd(ExecScripte(ssh=b), ip)["awk"], rc)
        b = dict(base)
        b[(ip, ("systemctl", "list-units", "--all", "--no-legend", "--plain", "helios-diskfill-*"))] = H.Res(
            0, "helios-diskfill-expire-aa.timer loaded active waiting x\nhelios-diskfill-garde-aa.service loaded active running y\n", "")
        self.assertEqual(["helios-diskfill-expire-aa.timer", "helios-diskfill-garde-aa.service"], E.lire_systemd(ExecScripte(ssh=b), ip)["unites_residuelles"])
        b[(ip, ("systemctl", "list-units", "--all", "--no-legend", "--plain", "helios-diskfill-*"))] = H.Res(1, "", "erreur")
        self.assertIsNone(E.lire_systemd(ExecScripte(ssh=b), ip)["unites_residuelles"])

    def test_lire_incidents_e2e(self):
        class Psql(object):
            def __init__(self, res):
                self.res, self.entrees = res, []

            def local(self, argv, delai=40, entree=None):
                self.entrees.append(entree)
                return self.res
        ok = Psql(H.Res(0, "AGE|250\nAGE|9000\nOUVERT|7|k8s-worker2|OPEN|180000\nOUVERT|3858|monitoring|IN_PROGRESS|200000\n", ""))
        self.assertEqual(([250, 9000], [{"id": 7, "app": "k8s-worker2", "statut": "OPEN", "age_s": 180000},
                                               {"id": 3858, "app": "monitoring", "statut": "IN_PROGRESS", "age_s": 200000}]),
                         E.lire_incidents_e2e(ok, "postgres-8"))
        self.assertEqual(([], []), E.lire_incidents_e2e(Psql(H.Res(0, "", "")), "postgres-8"))
        self.assertEqual((None, None), E.lire_incidents_e2e(Psql(H.Res(1, "", "boom")), "postgres-8"))
        self.assertEqual((None, None), E.lire_incidents_e2e(Psql(H.Res(0, "AGE|abc\n", "")), "postgres-8"))
        self.assertEqual((None, None), E.lire_incidents_e2e(Psql(H.Res(0, "OUVERT|7|seulement\n", "")), "postgres-8"))
        self.assertEqual((None, None), E.lire_incidents_e2e(Psql(H.Res(0, "AUTRE|1\n", "")), "postgres-8"))
        for sans_primaire in (None, ""):
            self.assertEqual((None, None), E.lire_incidents_e2e(ok, sans_primaire))
        sql = ok.entrees[0]
        for instruction in [i.strip() for i in sql.split(";") if i.strip()]:
            self.assertTrue(instruction.lower().startswith("select "), instruction)
        self.assertIn("a.type = 'HOST_RECLAIM_DISK'", sql)
        self.assertIn("a.target_namespace = 'k8s-nodes'", sql)
        self.assertIn("a.target_name = 'k8s-worker1'", sql)
        self.assertIn("i.created_at", sql)
        self.assertIn("Africa/Tunis", sql)

    def test_lire_pages_s(self):
        cmd = ("powershell", "-NoProfile", "-NonInteractive", "-Command", E.PS_PAGES)
        self.assertEqual(200.0, E.lire_pages_s(ExecScripte(local={cmd: H.Res(0, "100\n200\n300\n", "")})))
        for nom, rep in (("echec", H.Res(1, "", "")), ("un seul echantillon", H.Res(0, "100\n", "")),
                         ("valeur non numerique", H.Res(0, "100\nabc\n300\n", "")), ("sortie vide", H.Res(0, "", ""))):
            with self.subTest(nom):
                self.assertIsNone(E.lire_pages_s(ExecScripte(local={cmd: rep})))
        self.assertNotRegex(E.PS_PAGES, r"(?i)\b(set|remove|stop|restart|new|clear|invoke|out)-|start-(?!sleep)")      # lecture CIM seulement
        self.assertIn("Get-CimInstance", E.PS_PAGES)

    def test_lire_configmap_et_drapeaux_effectifs(self):
        cmd = ("kubectl", "-n", "pfe", "get", "configmap", "backend-config", "--show-managed-fields", "-o", "json")
        objet = {"data": {"HOST_DISK_E2E_158_ENABLED": "TRUE", "HELIOS_BRIDGE_ENABLED": " true ", "HOST_DISK_REMEDIATION_ENABLED": "no"},
                 "metadata": {"managedFields": [{"time": "2026-11-13T10:00:00Z"}, {"time": "2026-11-13T22:00:00Z"}]}}
        data, t = E.lire_configmap(ExecScripte(local={cmd: H.Res(0, json.dumps(objet), "")}))
        self.assertEqual(E._epoch("2026-11-13T22:00:00Z"), t)
        self.assertEqual({"HOST_DISK_E2E_158_ENABLED": "true", "HOST_DISK_REMEDIATION_ENABLED": "false", "HELIOS_BRIDGE_ENABLED": "true"},
                         E.drapeaux_effectifs(data, {}))
        self.assertEqual({"HOST_DISK_E2E_158_ENABLED": "illisible", "HOST_DISK_REMEDIATION_ENABLED": "true", "HELIOS_BRIDGE_ENABLED": "true"},
                         E.drapeaux_effectifs(data, {"HOST_DISK_E2E_158_ENABLED": None, "HOST_DISK_REMEDIATION_ENABLED": "TRUE"}))
        self.assertEqual({"HOST_DISK_E2E_158_ENABLED": "absent", "HOST_DISK_REMEDIATION_ENABLED": "absent", "HELIOS_BRIDGE_ENABLED": "absent"},
                         E.drapeaux_effectifs({}, {}))
        sans_date = dict(objet, metadata={})
        self.assertEqual((dict(objet["data"]), None), E.lire_configmap(ExecScripte(local={cmd: H.Res(0, json.dumps(sans_date), "")})))
        for nom, rep in (("echec", H.Res(1, "", "")), ("pas du JSON", H.Res(0, "x", "")), ("data non dict", H.Res(0, '{"data": [1]}', ""))):
            with self.subTest(nom):
                self.assertIsNone(E.lire_configmap(ExecScripte(local={cmd: rep})))
        mauvaise_date = dict(objet, metadata={"managedFields": [{"time": "hier"}]})
        self.assertEqual(None, E.lire_configmap(ExecScripte(local={cmd: H.Res(0, json.dumps(mauvaise_date), "")}))[1])

    def test_cooldown_effectif(self):
        self.assertEqual(30, E.cooldown_effectif({}, {}))
        self.assertEqual(2, E.cooldown_effectif({"REMEDIATION_COOLDOWN_MINUTES": "2"}, {}))
        self.assertEqual(5, E.cooldown_effectif({"REMEDIATION_COOLDOWN_MINUTES": "2"}, {"REMEDIATION_COOLDOWN_MINUTES": "5"}))
        for mauvais in ({"REMEDIATION_COOLDOWN_MINUTES": "deux"}, {"REMEDIATION_COOLDOWN_MINUTES": "-1"}, {"REMEDIATION_COOLDOWN_MINUTES": ""}):
            self.assertIsNone(E.cooldown_effectif(mauvais, {}))
        self.assertIsNone(E.cooldown_effectif({}, {"REMEDIATION_COOLDOWN_MINUTES": None}), "valueFrom : illisible")

    def test_lire_backend(self):
        cmd_d = ("kubectl", "-n", "pfe", "get", "deployment", "backend", "-o", "json")
        cmd_p = ("kubectl", "-n", "pfe", "get", "pods", "-l", "app=backend", "-o", "json")

        def deploiement(voulu=2, prets=2, maj=2, dispo=2, gen=7, vue=7, conteneurs=None, env=None):
            cs = conteneurs if conteneurs is not None else [{"name": "backend", "image": "img:1", "env": env or []}]
            status = {"observedGeneration": vue}
            for nom, v in (("replicas", voulu), ("readyReplicas", prets), ("updatedReplicas", maj), ("availableReplicas", dispo)):
                if v is not None:
                    status[nom] = v
            return json.dumps({"metadata": {"generation": gen}, "spec": {"replicas": voulu, "template": {"spec": {"containers": cs}}}, "status": status})

        def pods(*specs):
            return json.dumps({"items": [{"metadata": {"deletionTimestamp": "2026-11-14T00:40:00Z"} if term else {},
                                          "status": {"phase": phase, "startTime": debut},
                                          "spec": {"containers": [{"name": "backend", "image": image}, {"name": "sidecar", "image": "x:0"}]}}
                                         for image, phase, debut, term in specs]})
        t_cfg = E._epoch("2026-11-13T22:00:00Z")
        deux = pods(("img:1", "Running", "2026-11-14T00:30:00Z", False), ("img:1", "Running", "2026-11-14T00:31:00Z", False))

        def lire(d, p, t=t_cfg):
            return E.lire_backend(ExecScripte(local={cmd_d: H.Res(0, d, ""), cmd_p: H.Res(0, p, "")}), t)
        etat, env = lire(deploiement(env=[{"name": "A", "value": "1"}, {"name": "B", "valueFrom": {}}]), deux)
        self.assertEqual({"pret": True, "image": "img:1", "images_pods": ["img:1", "img:1"], "env_a_jour": True}, etat)
        self.assertEqual({"A": "1", "B": None}, env)
        self.assertFalse(lire(deploiement(prets=1), deux)[0]["pret"])
        self.assertFalse(lire(deploiement(dispo=None), deux)[0]["pret"])
        self.assertFalse(lire(deploiement(vue=6), deux)[0]["pret"])
        self.assertFalse(lire(deploiement(voulu=0, prets=0, maj=0, dispo=0), deux)[0]["pret"])
        self.assertFalse(lire(deploiement(), pods())[0]["pret"], "aucun pod : jamais pret")
        # pod en cours d'arret ou termine : ignore ; un pod plus ancien que la ConfigMap : environnement perime ; date illisible : inconnu
        ancien = pods(("img:1", "Running", "2026-11-14T00:30:00Z", False), ("img:0", "Running", "2026-11-14T00:00:00Z", True),
                      ("img:0", "Succeeded", "2026-11-12T00:00:00Z", False))
        etat, _ = lire(deploiement(voulu=1, prets=1, maj=1, dispo=1), ancien)
        self.assertEqual(["img:1"], etat["images_pods"])
        etat, _ = lire(deploiement(), pods(("img:1", "Running", "2026-11-13T20:00:00Z", False), ("img:1", "Running", "2026-11-14T00:31:00Z", False)))
        self.assertIs(False, etat["env_a_jour"])
        self.assertIsNone(lire(deploiement(), deux, t=None)[0]["env_a_jour"])
        self.assertIsNone(lire(deploiement(), pods(("img:1", "Running", "pas une date", False)))[0]["env_a_jour"])
        # illisible : (None, {})
        for d, p in ((deploiement(conteneurs=[{"name": "autre", "image": "x"}]), deux), ("pas du json", deux), (deploiement(), "pas du json"),
                     ("{}", deux), (deploiement(), "{}")):
            self.assertEqual((None, {}), lire(d, p))
        self.assertEqual((None, {}), E.lire_backend(ExecScripte(local={cmd_d: H.Res(1, "", ""), cmd_p: H.Res(0, deux, "")}), t_cfg))
        self.assertEqual((None, {}), E.lire_backend(ExecScripte(local={cmd_d: H.Res(0, deploiement(), ""), cmd_p: H.Res(1, "", "")}), t_cfg))

    def test_une_exception_imprevue_donne_illisible_et_jamais_un_plantage_ni_un_vert(self):
        class Explose(object):
            def ssh(self, *a, **k):
                raise RuntimeError("boom")

            def local(self, *a, **k):
                raise RuntimeError("boom")
        fe = E.collecter_faits_e2e(None, Explose(), "postgres-8")
        self.assertEqual({"drapeaux": None, "cooldown_minutes": None, "bridge": None, "bridge_sonde": {"tentee": False}, "backend": None, "systemd": None,
                          "actions_disque_recentes_s": None, "incidents_disque_ouverts": None, "diskpressure": None, "hote": {"pages_s": None}}, fe)
        ctx = self.ctx()
        rapport = E.evaluer_e2e(ctx.faits, fe, image_backend_attendue="pfe-backend:sim-disk158")
        self.assertFalse(rapport["go"])
        self.assertEqual("ROUGE", rapport["verdict"])

    def test_collecte_complete_sur_l_emulateur_donne_un_monde_vert(self):
        m = T.monde(dispo=12639 * MIO)
        exe = T.ExecEmule(m)
        fe = E.collecter_faits_e2e(None, exe, "postgres-8")
        self.assertEqual({"HOST_DISK_E2E_158_ENABLED": "true", "HOST_DISK_REMEDIATION_ENABLED": "absent", "HELIOS_BRIDGE_ENABLED": "true"},
                         fe["drapeaux"])
        self.assertEqual(2, fe["cooldown_minutes"])
        self.assertEqual({"pret": True, "image": T.E2E_IMAGE, "images_pods": [T.E2E_IMAGE, T.E2E_IMAGE], "env_a_jour": True}, fe["backend"])
        self.assertEqual(E.sha256_bridge_sot(), fe["bridge"]["sha256"])
        self.assertEqual({"tentee": True, "ok": True, "execute": False, "validation": "ACCEPTED", "mode": "RECOMMEND_ONLY", "usage": round(m.fs().usage, 2),
                          "fichiers": 0, "rejet": None, "stderr": ""}, fe["bridge_sonde"])
        self.assertEqual(([], []), (fe["actions_disque_recentes_s"], fe["incidents_disque_ouverts"]))
        self.assertEqual(130.0, fe["hote"]["pages_s"])
        ctx = self.ctx()
        r = E.evaluer_e2e(ctx.faits, fe, image_backend_attendue=T.E2E_IMAGE)
        self.assertEqual("VERT", r["verdict"], [x for x in r["raisons"] if x[0] != "INFO"])
        self.assertEqual(([], []), (exe.violations, exe.inconnues))


# ===================================================================== la SONDE du Bridge (validation seule, liaison ssh Bridge -> .158)
class SondeBridgeTests(Base):
    """La sonde envoie UNE requete `recommend_only` au Bridge DEPLOYE, depuis .161 : elle valide, LIT le repertoire exclusif et l'occupation de /
    par la liaison ssh du Bridge, et ne supprime RIEN. Jamais tentee si le Bridge n'est pas EXACTEMENT la source de verite."""

    @staticmethod
    def bridge(**kw):
        b = {"joignable": True, "capacites": ["HOST_RECLAIM_DISK", "RESTART_POD"], "sha256": E.sha256_bridge_sot()}
        b.update(kw)
        return b

    def test_le_corps_de_la_requete_est_la_validation_seule_du_scenario(self):
        self.assertEqual({"action": "HOST_RECLAIM_DISK", "type": "NODE_DISK_FULL", "ns": "k8s-nodes", "cible": "k8s-worker1", "noeud": "k8s-worker1",
                          "scenario": "DISK_E2E_158", "recommend_only": True}, json.loads(E.corps_sonde_bridge()))
        self.assertEqual(E.corps_sonde_bridge(), E.corps_sonde_bridge(), "deterministe")

    def test_le_script_ne_met_jamais_le_jeton_en_argument_ni_a_l_ecran_et_ne_fait_que_valider(self):
        s = E.script_sonde_bridge()
        self.assertIn("-H @-", s)                                                      # en-tete lu sur l'entree standard : jamais dans argv
        self.assertEqual(2, len(re.findall(r"\$T\b", s)), "le jeton n'apparait que dans le test de presence et le tube")
        self.assertNotRegex(s, r"echo\s+[\"']?\$T")
        self.assertNotRegex(s, r"-H\s+[\"']X-Helios-Token")
        self.assertNotIn("set -x", s)
        self.assertNotRegex(s, r"(?<![|])>(?!&)")                                        # aucune redirection vers un fichier
        for verbe in ("rm", "systemctl", "fallocate", "mkdir", "rmdir", "kubectl", "ssh", "scp", "kill", "chmod", "chown", "tee", "dd", "mv", "cp",
                      "systemd-run"):
            self.assertIsNone(re.search(r"\b%s\b" % re.escape(verbe), s), verbe)
        self.assertIn("recommend_only", s)
        self.assertIn(E.URL_ACTION_BRIDGE, s)
        self.assertIn(E.FICHIER_ENV_BRIDGE, s)

    def test_aucune_requete_n_est_envoyee_a_un_bridge_non_conforme(self):
        class Exe(object):
            def __init__(self):
                self.appels = []

            def ssh(self, *a, **k):
                self.appels.append(a)
                return H.Res(0, "{}", "")
        sha = E.sha256_bridge_sot()
        cas = (("aucun fait", None), ("injoignable", self.bridge(joignable=False)), ("joignable non booleen", self.bridge(joignable="oui")),
               ("capacite absente", self.bridge(capacites=["RESTART_POD"])), ("capacites illisibles", self.bridge(capacites=None)),
               ("capacites en texte (sous-chaine)", self.bridge(capacites="HOST_RECLAIM_DISK")), ("empreinte differente", self.bridge(sha256="0" * 64)),
               ("empreinte illisible", self.bridge(sha256=None)), ("pas un dict", ["HOST_RECLAIM_DISK"]))
        for nom, b in cas:
            with self.subTest(nom):
                exe = Exe()
                self.assertEqual({"tentee": False}, E.lire_sonde_bridge(exe, b, sha))
                self.assertEqual([], exe.appels, "aucune requete")
        exe = Exe()
        self.assertEqual({"tentee": False}, E.lire_sonde_bridge(exe, self.bridge(), "1" * 64), "empreinte attendue differente de celle du Bridge")
        self.assertEqual([], exe.appels)

    def test_lecture_des_reponses_de_la_sonde(self):
        ip, cmd = E.IP_BRIDGE, ("bash", "-c", E.script_sonde_bridge())
        bon = {"ok": True, "execute": False, "validation": "ACCEPTED", "execution_mode": "RECOMMEND_ONLY", "usage_avant_pct": 67.13, "fichiers": 0,
               "stderr": "", "seuil_recuperation_pct": 77.0}

        def sonde(rep):
            return E.lire_sonde_bridge(ExecScripte(ssh={(ip, cmd): rep}), self.bridge())
        self.assertEqual({"tentee": True, "ok": True, "execute": False, "validation": "ACCEPTED", "mode": "RECOMMEND_ONLY", "usage": 67.13, "fichiers": 0,
                          "rejet": None, "stderr": ""}, sonde(H.Res(0, json.dumps(bon) + "\n", "")))
        self.assertEqual("DIRECTORY_UNREADABLE", sonde(H.Res(0, json.dumps(dict(bon, ok=False, rejet="DIRECTORY_UNREADABLE")), ""))["rejet"])
        for nom, rep, motif in (("ssh en echec", H.Res(255, "", "refused"), "aucune reponse"), ("sortie vide", H.Res(0, "", ""), "aucune reponse"),
                                ("pas du JSON", H.Res(0, "<html>502</html>", ""), "illisible"), ("JSON non objet", H.Res(0, "[1, 2]", ""), "illisible"),
                                ("jeton illisible", H.Res(0, '{"sonde_erreur":"jeton_illisible"}', ""), "jeton_illisible")):
            with self.subTest(nom):
                r = sonde(rep)
                self.assertEqual((True, None), (r["tentee"], r["ok"]))
                self.assertIn(motif, r["erreur"])
        derniere = sonde(H.Res(0, "avertissement\n" + json.dumps(bon) + "\n", ""))
        self.assertIs(True, derniere["ok"], "la reponse est la derniere ligne non vide")

    def test_la_requete_de_la_sonde_est_acceptee_par_le_vrai_bridge_et_ne_supprime_rien_meme_avec_des_fichiers_presents(self):
        ctx = self.ctx()
        jeton = "ab" * 16
        ctx.monde.dossier = {}
        ctx.monde.fichiers.update({"helios-diskfill-%s-001.bin" % jeton: 512 * MIO, "helios-diskfill-%s-002.bin" % jeton: 512 * MIO})
        with ctx.bridge:
            statut, rep = ctx.bridge.poster("/action", json.loads(E.corps_sonde_bridge()))
        self.assertEqual(200, statut)
        self.assertEqual((True, False, "ACCEPTED", "RECOMMEND_ONLY"), (rep["ok"], rep["execute"], rep["validation"], rep["execution_mode"]), rep)
        self.assertEqual(2, rep["fichiers"])
        self.assertIsInstance(rep["usage_avant_pct"], float)
        self.assertEqual([], ctx.bridge.exec.rm, "validation seule : AUCUNE suppression")
        self.assertEqual(2, len(ctx.monde.fichiers), "les fichiers du scenario sont intacts")
        for c in ctx.bridge.exec.commandes:
            self.assertIn(c, (E.CMD_STAT_DIR, E.CMD_FIND, E.CMD_STAT_FS), "lectures seulement : %r" % c)

    def test_la_meme_requete_visant_155_est_refusee_par_le_vrai_bridge_sans_aucune_commande(self):
        ctx = self.ctx()
        corps = json.loads(E.corps_sonde_bridge())
        corps.update(cible="k8s-worker2", noeud="k8s-worker2")
        with ctx.bridge:
            statut, rep = ctx.bridge.poster("/action", corps)
        self.assertEqual(200, statut)
        self.assertFalse(rep["ok"])
        self.assertEqual("REJECTED_TARGET_NOT_ALLOWLISTED", rep["rejet"])
        self.assertEqual([], ctx.bridge.exec.commandes, "refus AVANT toute commande ssh")

    def test_une_sonde_ok_ne_donne_qu_un_info_et_une_sonde_ko_un_rouge_mais_jamais_d_injection(self):
        r = E.evaluer_e2e(self.ctx().faits, self.ctx().fe, image_backend_attendue="pfe-backend:sim-disk158")
        info = [x for x in r["raisons"] if x[0] == "INFO" and "liaison ssh Bridge" in x[1]]
        self.assertEqual(1, len(info), r["raisons"])
        self.assertNotIn(E.CODE_SONDE, [x[3] for x in r["raisons"]])
        ctx = self.ctx()
        ctx.fe["bridge_sonde"].update(ok=False, rejet="UNSAFE_DIRECTORY")
        out = self.lancer(ctx)
        self.assertEqual("REFUS_PREREQUIS", out["verdict"])
        self.assertIn(E.CODE_SONDE, out["codes"])
        self.assertEqual([], ctx.monde.appels, "aucun appel au noeud")


# ===================================================================== le module de la porte : aucune E/S propre, lectures bornees
class SourceDeLaPorteTests(Base):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "helios_disk_e2e.py"), encoding="utf-8") as f:
            cls.source = f.read()
        cls.arbre = ast.parse(cls.source)
        noms = {"lire_configmap", "lire_backend", "lire_bridge", "lire_systemd", "lire_incidents_e2e", "lire_diskpressure", "lire_pages_s", "lire_sonde_bridge"}
        cls.lecteurs = [n for n in ast.walk(cls.arbre) if isinstance(n, ast.FunctionDef) and n.name in noms]

    def commandes(self):
        for f in self.lecteurs:
            for n in ast.walk(f):
                if (isinstance(n, ast.List) and n.elts and isinstance(n.elts[0], ast.Constant) and isinstance(n.elts[0].value, str)
                        and re.fullmatch(r"[a-z][a-z0-9-]*", n.elts[0].value)):
                    yield [e.value for e in n.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]

    def test_les_sept_lecteurs_existent(self):
        self.assertEqual(7, len(self.lecteurs))

    def test_aucun_sous_processus_ni_commande_destructrice(self):
        for motif in ("subprocess", "Popen", "os.system", "socket.socket", "shell=True"):
            self.assertNotIn(motif, self.source.replace("mock", ""), motif)
        for n in ast.walk(self.arbre):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                self.assertNotIn(n.func.id, ("eval", "exec", "compile", "__import__", "system"), n.lineno)

    def test_les_commandes_de_la_porte_sont_des_lectures(self):
        cmds = list(self.commandes())
        self.assertGreaterEqual(len(cmds), 8)
        for mots in cmds:
            self.assertIn(mots[0], ("kubectl", "curl", "sha256sum", "test", "systemctl", "powershell", "bash"), mots)
            if mots[0] == "bash":
                # la SEULE commande `bash -c` est la SONDE du Bridge (script construit par script_sonde_bridge, valide par SondeBridgeTests)
                self.assertEqual(["bash", "-c"], mots, mots)
                continue
            if mots[0] == "kubectl":
                self.assertIn("get", mots)
                self.assertFalse({"delete", "apply", "patch", "scale", "edit", "drain", "cordon", "exec", "create", "replace", "rollout"} & set(mots), mots)
            if mots[0] == "systemctl":
                self.assertEqual("list-units", mots[1], mots)               # jamais start / stop / restart / is-active ici
            if mots[0] == "test":
                self.assertEqual(["test", "-x"], mots[:2])
            if mots[0] != "kubectl":
                self.assertFalse({"-X", "--data", "-d", "--upload-file", "--output", "sh", "bash", "-c"} & set(mots[1:]), mots)

    def test_les_requetes_sql_de_la_porte_sont_des_select(self):
        selects = []
        for f in self.lecteurs:
            for n in ast.walk(f):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.match(r"(?is)\s*select\s", n.value):
                    selects.append(n.value)
        instructions = [i.strip() for s in selects for i in s.split(";") if i.strip()]
        self.assertGreaterEqual(len(instructions), 2)                      # cooldown (AGE) et incidents ouverts (OUVERT)
        for i in instructions:
            self.assertTrue(i.lower().startswith("select "), i[:60])
        for s in selects:
            self.assertIsNone(re.search(r"(?i)\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|into|copy|call)\b", s), s[:80])


# ===================================================================== preuve de bout en bout par le VRAI lanceur
class NominalViaLeLanceurReelTests(Base):
    """`helios_disk_test.principal(--target 158 --go)` sur l'emulateur du noeud : la porte lit un monde E2E pret, puis le VRAI code du Bridge
    (HTTP local) libere les fichiers du scenario sur un noeud simule : DETECTION -> INCIDENT -> HOST_RECLAIM_DISK -> BRIDGE -> VERIFY -> RESOLVED."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="helios_disk_e2e_cli_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        calme = "2026-11-13T10:00:00.000Z In(05) vmx Guest: tout va bien\n"
        T.fabriquer_vm(self.tmp, "k8s-worker1", journal=calme)
        T.fabriquer_vm(self.tmp, "k8s-worker2", journal=calme)

    def lancer(self, ctx, e2e=None, extra=(), image=T.E2E_IMAGE):
        exe = T.ExecEmule(ctx.monde, primaire_sur="k8s-worker2", e2e=T.e2e_pret() if e2e is None else e2e)
        deps = T.DepsFaux(exe, H.HorlogeSimulee(ctx.monde), 4000, 36.1)
        argv = ["--target", "158", "--go", "--confirm", "DISK-158-81-82-83", "--vm-base", self.tmp] + list(extra)
        if image:
            argv += ["--image-backend", image]
        lignes = []
        with ctx.bridge:
            code = H.principal(argv, deps, lignes.append)
        return code, "\n".join(lignes), exe

    def test_chemin_nominal_complet_par_le_lanceur_reel(self):
        ctx = self.ctx()
        code, s, exe = self.lancer(ctx)
        self.assertEqual(0, code, s)
        self.assertIn("PRE-VOL DU SCENARIO DISK_E2E_158", s)
        self.assertIn("VERDICT : VERT", s)
        self.assertIn("BILAN : OK", s)
        etapes = []
        for _, etape, _ in ctx.monde.chaine:
            if etape in E.CHEMIN_NOMINAL and (not etapes or etapes[-1] != etape):
                etapes.append(etape)
        self.assertEqual(list(E.CHEMIN_NOMINAL), etapes)
        self.assertNotIn("ESCALATE", [e for _, e, _ in ctx.monde.chaine])
        self.assertEqual("RESOLVED", ctx.monde.incident["statut"])
        self.assertEqual("HOST_RECLAIM_DISK:COMPLETED", ctx.monde.action)
        self.assertEqual(({}, None), (ctx.monde.fichiers, ctx.monde.dossier))
        self.assertEqual(1, sum(1 for c, _ in ctx.bridge.requetes if c == "/action"), "une seule requete au Bridge")
        self.assertEqual([], ctx.backend.appels_legacy, "jamais le pod hote prive")
        self.assertIn("par : helios", s, "les fichiers sont liberes par HELIOS (Bridge), ni par la garde ni par l'expiration")
        self.assertLessEqual(ctx.monde.usage_vrai_max, 82.0)
        self.assertEqual((exe.violations, exe.inconnues), ([], []))

    def test_un_refus_de_la_porte_ne_touche_ni_le_noeud_ni_le_bridge(self):
        for nom, e2e in (("Bridge ancien", T.e2e_pret(bridge_actions=["HOST_IMAGE_GC"])), ("drapeau absent", T.e2e_pret(configmap={"HELIOS_BRIDGE_ENABLED": "true"})),
                         ("Backend ancien", T.e2e_pret(deploy=dict(T.e2e_pret()["deploy"], image="pfe-backend:0.2.164-vm3auto")))):
            with self.subTest(nom):
                ctx = self.ctx()
                code, s, exe = self.lancer(ctx, e2e=e2e)
                self.assertEqual(1, code, s)
                self.assertIn("REFUS : pre-vol ROUGE", s)
                self.assertEqual([], T.ecritures(ctx.monde), "AUCUN appel d'ecriture au noeud (les lectures du pre-vol sont permises)")
                self.assertIsNone(ctx.monde.incident)
                self.assertEqual([], ctx.monde.chaine)
                self.assertEqual([], ctx.bridge.requetes)
                self.assertEqual(0.0, ctx.monde.t)

    def test_le_backend_du_monde_sans_drapeau_ne_remedie_pas_et_le_noeud_est_laisse_propre(self):
        """Si le Backend REEL ignorait le drapeau que la porte a lu (cas que la porte existe pour prevenir) : jamais RESOLVED, noeud propre."""
        ctx = self.ctx(drapeau_scenario=False)
        code, s, exe = self.lancer(ctx)
        self.assertNotEqual(0, code, s)
        self.assertNotIn("BILAN : OK", s)
        self.assertEqual("OPEN", ctx.monde.incident["statut"])
        self.assertNotIn("RESOLVED", [e for _, e, _ in ctx.monde.chaine])
        self.assertEqual(({}, None), (ctx.monde.fichiers, ctx.monde.dossier))


if __name__ == "__main__":
    unittest.main(verbosity=2)
