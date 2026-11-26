# -*- coding: utf-8 -*-
"""Tests du PRE-VOL FACULTATIF et du DEMARRAGE DIRECT du service local DISK de .158 (scripts/helios-ops/helios_diskfill_service.py).

(Nom de fichier historique : `arm` = la suite d'origine testait l'armement ; il a ete SUPPRIME le 15 nov. 2026 sur decision de l'exploitant.)

Banc d'essai hote <-> noeud : un MondeEmule joue .158 ; un faux executeur route les commandes ssh vers le VRAI programme du noeud (diskfill.py)
execute sur ce monde emule. Le pre-vol du lanceur DISK (helios_disk_test + porte helios_disk_e2e) est le VRAI code, alimente par des faits simules.

Preuves : le pre-vol du lanceur est CONSERVE (memes fonctions), execute automatiquement par l'agent Windows a chaque start ; `--armer` est obsolete ;
le demarrage exige une attestation HMAC fraiche, ciblee .158, strictement VERT ; aucun ORANGE n'est accepte automatiquement ; les ROUGE/ORANGE
Windows, agent absent ou attestation invalide refusent avant allocation. Le watchdog VM_DOWN :9099 reste independant.

AUCUN reseau, AUCUNE VM, AUCUN ssh, AUCUN subprocess, AUCUN sleep reel.

Lancer : python -I -B test_helios_diskfill_arm.py -v        (depuis scripts/helios-ops)
"""
import ast
import contextlib
import copy
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import helios_disk_test as H          # noqa: E402
import helios_diskfill_service as S   # noqa: E402

E = H.module_porte_e2e()
D = S.noeud_module()
MIO = H.MIO
IMAGE = "pfe-backend:0.2.165-disk-e2e"
CHEMIN_SERVICE_TEST = os.path.join(HERE, "test_helios_diskfill_service.py")


def _charger(nom, chemin):
    spec = importlib.util.spec_from_file_location(nom, chemin)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nom] = mod
    spec.loader.exec_module(mod)
    return mod


TS = _charger("test_service_pour_banc", CHEMIN_SERVICE_TEST)       # MondeEmule du noeud (importer ce module n'execute aucun test)


def _interdit(*a, **k):
    raise AssertionError("ACCES REEL INTERDIT dans un test : %r" % (a[:1],))


@contextlib.contextmanager
def attestation_requise():
    """Mode OPTIONNEL « attestation Windows requise » : la constante est fixee sur les DEUX copies du programme du noeud (celle du module hote et celle
    du monde emule). DEFAUT : False (demarrage autonome, comme helios-cpuload.service)."""
    with mock.patch.object(D, "ATTESTATION_HOTE_REQUISE", True), mock.patch.object(TS.D, "ATTESTATION_HOTE_REQUISE", True):
        yield


class SansInfra(unittest.TestCase):
    def setUp(self):
        cibles = [(subprocess, "run"), (subprocess, "Popen"), (subprocess, "call"), (subprocess, "check_call"), (subprocess, "check_output"),
                  (urllib.request, "urlopen"), (socket, "create_connection"), (socket.socket, "connect"), (time, "sleep"), (os, "system")]
        self._patchs = [mock.patch.object(o, n, _interdit) for o, n in cibles]
        for p in self._patchs:
            p.start()
            self.addCleanup(p.stop)


def ns_sim():
    return types.SimpleNamespace(sim_taille_mo=None, sim_usage=None, sim_primaire_cnpg=None, sim_ram_mo=None, sim_drapeau=None)


class ExeFictif(object):
    """Executeur du lanceur (ssh / local) qui n'accepte QUE les deux LECTURES du module sur .158 (sha256sum, `diskfill.py etat --json`) et les route
    vers le vrai programme du noeud. Toute autre commande (dont `armer`, qui n'existe plus) fait echouer le test : AUCUNE ecriture sur .158."""

    def __init__(self, banc):
        self.banc = banc
        self.appels = []
        self.ecritures = []

    def ssh(self, ip, argv, delai=45):
        self.appels.append((ip, list(argv)))
        assert ip == D.IP_CIBLE, "ssh vers %s : seule .158 est jointe par ce module" % ip
        m = self.banc.m
        if argv[:4] == ["sudo", "-n", "sha256sum", "--"]:
            chemins = argv[4:]
            par_chemin = dict((c, n) for n, c in S.chemins_noeud().items())
            lignes = []
            for c in chemins:
                sha = m.contenu_sha.get(par_chemin[c])
                if sha:
                    lignes.append("%s  %s" % (sha, c))
            return H.Res(0 if len(lignes) == len(chemins) else 1, "\n".join(lignes) + ("\n" if lignes else ""), "")
        if argv[:6] == ["sudo", "-n", "/usr/bin/python3", "-I", "-B", D.CHEMIN_SCRIPT]:
            sous = argv[6:]
            assert sous == ["etat", "--json"], "sous-commande du noeud inattendue (lecture seule uniquement) : %r" % (sous,)
            sortie = []
            code = TS.D.principal(sous, m, sortie.append)
            return H.Res(code, "\n".join(sortie) + ("\n" if sortie else ""), "")
        raise AssertionError("commande ssh inattendue : %r" % (argv,))

    def local(self, argv, delai=40, entree=None):
        raise AssertionError("commande locale inattendue : %r" % (argv,))

    def http_json(self, url, delai=25):
        raise AssertionError("HTTP inattendu : %r" % (url,))


class FauxDeps(object):
    def __init__(self, exe):
        self._exe = exe

    def executeur(self):
        return self._exe


class Banc(object):
    def __init__(self, **kw_monde):
        self.m = TS.MondeEmule(**kw_monde)
        self.m.contenu_sha = S.sommes_calculees()
        self.exe = ExeFictif(self)
        self.faits = H.faits_simules(S.CLE, ns_sim())[1]
        self.faits_porte = E.faits_e2e_simules(image=IMAGE)
        self.faits_porte["cooldown_minutes"] = 2                      # valeur REELLEMENT appliquee par le Backend en production

    def collecteur(self, deps, a):
        return self.faits, self.faits_porte, {"exe": self.exe, "primaire": "postgres-8", "id0": 0}

    def lancer(self, *args):
        out = []
        code = S.principal(list(args), deps=FauxDeps(self.exe), sortie=out.append, collecteur=self.collecteur)
        return code, "\n".join(out)

    def preflight(self, *extra):
        return self.lancer("--preflight", "--image-backend", IMAGE, *extra)


# ------------------------------------------------------------------------------------------------ empreintes du depot
class TestSommesDepot(SansInfra):
    def test_les_empreintes_portent_sur_le_contenu_lf(self):
        with tempfile.TemporaryDirectory() as t:
            for n in S.NOMS_SOT:
                brut = S.lire_source(n)
                with open(os.path.join(t, n), "wb") as f:
                    f.write(brut.replace(b"\n", b"\r\n"))                    # copie checkout CRLF (core.autocrlf=true)
            ref = S.sommes_calculees()
            with mock.patch.object(S, "DIR_SVC", t):
                self.assertEqual(S.sommes_calculees(), ref, "CRLF et LF donnent la MEME empreinte")
                self.assertNotIn(b"\r", S.lire_source("diskfill.py"))

    def test_le_depot_a_des_empreintes_a_jour(self):
        self.assertEqual(S.lire_sommes_fichier(), S.sommes_calculees())

    def test_ecrire_et_relire_les_sommes(self):
        with tempfile.TemporaryDirectory() as t:
            for n in S.NOMS_SOT:
                with open(os.path.join(t, n), "wb") as f:
                    f.write(S.lire_source(n))
            with mock.patch.object(S, "DIR_SVC", t):
                self.assertIsNone(S.lire_sommes_fichier())
                sommes = S.ecrire_sommes()
                self.assertEqual(S.lire_sommes_fichier(), sommes)
                data = open(os.path.join(t, "SHA256SUMS"), "rb").read()
                self.assertTrue(data.endswith(b"\n"))
                self.assertNotIn(b"\r", data)
                self.assertEqual(data.decode("ascii"), S.contenu_sommes(sommes))
                with open(os.path.join(t, "SHA256SUMS"), "wb") as f:
                    f.write(b"pas une empreinte  diskfill.py\n")
                self.assertIsNone(S.lire_sommes_fichier())

    def test_seuls_les_fichiers_de_la_source_de_verite_sont_lisibles(self):
        for mauvais in ("../helios_disk_test.py", "SHA256SUMS", "autre.py", ""):
            with self.assertRaises(ValueError):
                S.lire_source(mauvais)

    def test_la_commande_sommes_est_la_seule_ecriture_locale(self):
        with tempfile.TemporaryDirectory() as t:
            for n in S.NOMS_SOT:
                with open(os.path.join(t, n), "wb") as f:
                    f.write(S.lire_source(n))
            out = []
            with mock.patch.object(S, "DIR_SVC", t):
                self.assertEqual(S.principal(["--sommes"], sortie=out.append), 0)
            self.assertEqual(sorted(os.listdir(t)), sorted(list(S.NOMS_SOT) + ["SHA256SUMS"]))

    def test_les_empreintes_des_unites_du_depot_sont_celles_figees_dans_le_programme_du_noeud(self):
        sommes = S.sommes_calculees()
        for unite, sha in D.SHA_UNITES.items():
            self.assertEqual(sha, sommes[unite], "%s : SHA_UNITES (diskfill.py) != empreinte du depot" % unite)


# ------------------------------------------------------------------------------------------------ controles du service (purs)
def installation_ok(banc=None):
    m = banc.m if banc else TS.MondeEmule()
    out = []
    TS.D.etat_cmd(m, True, out.append)
    etat = json.loads(out[0])
    sha = dict(S.sommes_calculees())
    return {"sha256": sha, "etat": etat}


def sommes_ok():
    s = S.sommes_calculees()
    return {"calculees": s, "fichier": dict(s)}


def codes(raisons, niveau="ROUGE"):
    return sorted(set(r[3] for r in raisons if r[0] == niveau))


class TestEvaluerService(SansInfra):
    def test_nominal_aucun_rouge_aucun_orange(self):
        r = S.evaluer_service(sommes_ok(), installation_ok(), 2)
        self.assertEqual(codes(r), [])
        self.assertEqual(set(x[0] for x in r), {"INFO"})
        textes = " ".join(x[1] for x in r)
        self.assertIn("StateDirectoryMode=0700", textes)
        self.assertIn("chargee (= fichier)", textes)
        self.assertIn("REELLEMENT applique : 2 min", textes)
        self.assertNotIn("armement", textes.lower())

    def cas(self, mutation, attendu, cooldown_backend=2, sommes=None):
        svc = installation_ok()
        mutation(svc)
        r = S.evaluer_service(sommes or sommes_ok(), svc, cooldown_backend)
        self.assertIn(attendu, codes(r), r)
        self.assertEqual(set(x[0] for x in r) - {"ROUGE", "INFO"}, set(), "le service ne cree JAMAIS d'ORANGE")
        return r

    def test_chaque_defaut_est_un_rouge_jamais_un_orange(self):
        U, G = D.UNITE_PRINCIPALE, D.UNITE_GARDE
        cas = [
            ("non_installe", lambda s: s["sha256"].update({"diskfill.py": None}), "SVC_NON_INSTALLE"),
            ("unite_non_installee", lambda s: s["sha256"].update({"helios-diskguard.service": None}), "SVC_NON_INSTALLE"),
            ("sha_script_different", lambda s: s["sha256"].update({"diskfill.py": "9" * 64}), "SVC_SHA_DISKFILL_PY"),
            ("sha_unite_differente", lambda s: s["sha256"].update({"helios-diskfill.service": "9" * 64}), "SVC_SHA_HELIOS_DISKFILL_SERVICE"),
            ("sha_garde_differente", lambda s: s["sha256"].update({"helios-diskguard.service": "9" * 64}), "SVC_SHA_HELIOS_DISKGUARD_SERVICE"),
            ("etat_illisible", lambda s: s.update(etat=None), "SVC_ETAT_ILLISIBLE"),
            ("hostname", lambda s: s["etat"].update(hostname="k8s-worker2"), "SVC_IDENTITE"),
            ("ip_absente", lambda s: s["etat"].update(ip_presente=False), "SVC_IDENTITE"),
            ("ip_illisible", lambda s: s["etat"].update(ip_presente=None), "SVC_IDENTITE"),
            ("unite_illisible", lambda s: s["etat"]["unites"].update({U: None}), "SVC_UNITE_ILLISIBLE"),
            ("unite_non_chargee", lambda s: s["etat"]["unites"][U].update(LoadState="not-found"), "SVC_UNITE"),
            ("drop_in", lambda s: s["etat"]["unites"][G].update(DropInPaths="/etc/systemd/system/x.d/y.conf"), "SVC_UNITE"),
            ("enable", lambda s: s["etat"]["unites"][U].update(UnitFileState="enabled"), "SVC_UNITE"),
            ("state_directory_mode_0755", lambda s: s["etat"]["unites"][U].update(StateDirectoryMode="0755"), "SVC_UNITE"),
            ("state_directory_mode_garde", lambda s: s["etat"]["unites"][G].update(StateDirectoryMode="0750"), "SVC_UNITE"),
            ("state_directory_mode_absent", lambda s: s["etat"]["unites"][U].update(StateDirectoryMode=""), "SVC_UNITE"),
            ("restart", lambda s: s["etat"]["unites"][U].update(Restart="always"), "SVC_UNITE"),
            ("unite_a_recharger", lambda s: s["etat"]["unites"][U].update(NeedDaemonReload="yes"), "SVC_UNITE"),
            ("garde_a_recharger", lambda s: s["etat"]["unites"][G].update(NeedDaemonReload="yes"), "SVC_UNITE"),
            ("need_daemon_reload_illisible", lambda s: s["etat"]["unites"][U].pop("NeedDaemonReload"), "SVC_UNITE"),
            ("unite_active", lambda s: s["etat"]["unites_occupees"].update({U: True}), "SVC_UNITE_ACTIVE"),
            ("garde_active", lambda s: s["etat"]["unites_occupees"].update({G: True}), "SVC_UNITE_ACTIVE"),
            ("etat_unite_illisible", lambda s: s["etat"]["unites_occupees"].update({U: None}), "SVC_UNITE_ACTIVE"),
            ("dossier_mode", lambda s: s["etat"].update(dossier={"existe": True, "anomalies": ["permissions 755 au lieu de 700"], "info": {}}), "SVC_DOSSIER"),
            ("dossier_illisible", lambda s: s["etat"].update(dossier=None), "SVC_DOSSIER"),
            ("dossier_anomalies_illisibles", lambda s: s["etat"].update(dossier={"existe": True, "anomalies": None, "info": {}}), "SVC_DOSSIER"),
            ("orphelins", lambda s: s["etat"].update(contenu_dossier=["helios-diskfill-" + "e" * 32 + "-001.bin"]), "SVC_ORPHELINS"),
            ("contenu_illisible", lambda s: s["etat"].update(contenu_dossier=None), "SVC_DOSSIER"),
            ("residus_du_lanceur", lambda s: s["etat"].update(unites_transitoires=["helios-diskfill-garde-" + "a" * 32 + ".service"]), "SVC_RESIDUS_LANCEUR"),
            ("residus_illisibles", lambda s: s["etat"].update(unites_transitoires=None), "SVC_RESIDUS_LANCEUR"),
            ("cooldown_local", lambda s: s["etat"].update(cooldown={"restant_s": 120, "erreur": None, "local_min": 5}), "SVC_COOLDOWN_LOCAL"),
            ("cooldown_illisible", lambda s: s["etat"].update(cooldown={"restant_s": None, "erreur": "horodatage altere", "local_min": 5}), "SVC_COOLDOWN_LOCAL"),
            ("lancement_en_cours", lambda s: s["etat"].update(etat={"phase": "observation", "jeton8": "abc", "verdict": None}), "SVC_LANCEMENT_EN_COURS"),
            ("liberation_incomplete", lambda s: s["etat"].update(etat={"phase": "liberation_incomplete", "jeton8": "abc", "verdict": "ERREUR"}), "SVC_LANCEMENT_EN_COURS"),
        ]
        for nom, mutation, attendu in cas:
            with self.subTest(nom):
                self.cas(mutation, attendu)

    def test_un_lancement_termine_n_est_pas_bloquant(self):
        svc = installation_ok()
        svc["etat"]["etat"] = {"phase": "terminee", "jeton8": "abc", "verdict": "OK"}
        self.assertEqual(codes(S.evaluer_service(sommes_ok(), svc, 2)), [])

    def test_sommes_du_depot_perimees_rouge(self):
        s = sommes_ok()
        s["fichier"] = None
        r = S.evaluer_service(s, installation_ok(), 2)
        self.assertIn("SVC_SOMMES_DEPOT", codes(r))
        s = sommes_ok()
        s["fichier"]["diskfill.py"] = "0" * 64
        self.assertIn("SVC_SOMMES_DEPOT", codes(S.evaluer_service(s, installation_ok(), 2)))

    def test_installation_illisible_rouge(self):
        self.assertIn("SVC_ILLISIBLE", codes(S.evaluer_service(sommes_ok(), None, 2)))

    def test_le_cooldown_du_backend_plus_long_que_le_local_est_un_rouge(self):
        local_min = D.COOLDOWN_LOCAL_S // 60
        self.assertEqual(codes(S.evaluer_service(sommes_ok(), installation_ok(), 2)), [], "2 min (valeur de production) <= cooldown local : couvert")
        self.assertEqual(codes(S.evaluer_service(sommes_ok(), installation_ok(), local_min)), [], "egalite : couvert")
        self.assertIn("SVC_COOLDOWN_INFERIEUR_BACKEND", codes(S.evaluer_service(sommes_ok(), installation_ok(), local_min + 1)))
        self.assertIn("SVC_COOLDOWN_INFERIEUR_BACKEND", codes(S.evaluer_service(sommes_ok(), installation_ok(), 30)),
                      "le defaut du code Backend (30 min, cle absente de la ConfigMap) n'est plus couvert depuis le 17 nov. 2026 : ROUGE")
        self.assertIn("SVC_COOLDOWN_INFERIEUR_BACKEND", codes(S.evaluer_service(sommes_ok(), installation_ok(), 120)))
        self.assertEqual(codes(S.evaluer_service(sommes_ok(), installation_ok(), None)), [], "illisible : deja ROUGE dans la porte du lanceur")

    def test_les_defauts_multiples_sont_tous_signales(self):
        svc = installation_ok()
        svc["sha256"]["diskfill.py"] = "9" * 64
        svc["etat"]["hostname"] = "autre"
        svc["etat"]["unites"][D.UNITE_PRINCIPALE]["StateDirectoryMode"] = "0755"
        c = codes(S.evaluer_service(sommes_ok(), svc, 2))
        self.assertGreaterEqual(len(c), 3)

    def test_plus_aucune_notion_d_armement_dans_les_raisons(self):
        svc = installation_ok()
        svc["etat"]["armement"] = {"present": True, "valide": False, "nonce": "0" * 32}          # un ancien programme du noeud pourrait encore le dire
        textes = " ".join(x[1] for x in S.evaluer_service(sommes_ok(), svc, 2)).lower()
        self.assertNotIn("armement", textes)


# ------------------------------------------------------------------------------------------------ fusion avec le pre-vol du lanceur
class TestFusionOrange(SansInfra):
    def rapport(self, banc, acquittes=()):
        return E.evaluer_e2e(banc.faits, banc.faits_porte, E.parametres(), S.CLE, image_backend_attendue=IMAGE, acquittes=set(acquittes))

    def test_le_gel_d_hote_reste_un_orange_acquittable_jamais_un_blocage(self):
        b = Banc()
        b.faits["hote"]["gels"] = [(7200, 150, "k8s-worker1")]                 # un gel il y a 2 h (dans les 48 h, pas dans l'heure)
        r = self.rapport(b)
        self.assertEqual(r["verdict"], "ORANGE")
        self.assertIn("GEL_HOTE_48H", [x[3] for x in r["raisons"] if x[0] == "ORANGE"])
        fusion = S.fusionner(r, S.evaluer_service(sommes_ok(), installation_ok(b), 2), set())
        self.assertEqual(fusion["verdict"], "ORANGE")
        self.assertFalse(fusion["go"], "ORANGE non acquitte : la porte du lanceur ne dirait pas `go`")
        fusion = S.fusionner(self.rapport(b, {"GEL_HOTE_48H"}), S.evaluer_service(sommes_ok(), installation_ok(b), 2), {"GEL_HOTE_48H"})
        self.assertEqual(fusion["verdict"], "ORANGE", "l'ORANGE n'est pas efface par l'acquittement : il reste un ORANGE")
        self.assertTrue(fusion["go"], "acquitte code par code (comme --go du lanceur)")

    def test_un_rouge_du_service_bloque_meme_avec_tous_les_acquittements(self):
        b = Banc()
        svc = installation_ok(b)
        svc["etat"]["hostname"] = "autre"
        tous = set(H.CODES_ORANGE_ACQUITTABLES)
        fusion = S.fusionner(self.rapport(b, tous), S.evaluer_service(sommes_ok(), svc, 2), tous)
        self.assertEqual(fusion["verdict"], "ROUGE")
        self.assertFalse(fusion["go"])

    def test_le_risque_cnpg_n_est_jamais_acquitte_par_ce_module(self):
        r = {"raisons": [("ORANGE", "x", "", H.CODE_CNPG_ACCEPTE)], "verdict": "ORANGE", "go": False}
        fusion = S.fusionner(r, [], {H.CODE_CNPG_ACCEPTE})
        self.assertEqual(fusion["orange_non_acquittes"], [], "le code CNPG a son propre drapeau dans le lanceur ; il n'est pas compte ici")

    def test_le_module_ne_porte_plus_aucun_outil_d_armement(self):
        for nom in ("construire_charge", "deposer_armement", "orange_a_enregistrer", "charge_en_base64", "jeton_confirmation",
                    "TTL_DEFAUT_MIN", "TTL_MIN_MIN", "TTL_MAX_MIN", "OUTIL"):
            self.assertFalse(hasattr(S, nom), "%s : vestige de l'armement" % nom)


# ------------------------------------------------------------------------------------------------ ligne de commande
ROUGES_DU_LANCEUR = [
    ("ram_basse", lambda b: b.faits["hote"].update(ram_mo=1000)),
    ("gel_recent_moins_d_1h", lambda b: b.faits["hote"].update(gels=[(1800, 120, "k8s-worker1")])),
    ("occupation_80_5", lambda b: b.faits["noeud"].update(fs=H.Fs(38121 * MIO, int(38121 * MIO * (1 - 0.805))))),
    ("cnpg_non_sain", lambda b: b.faits["cnpg"].update(sain=False)),
    ("noeud_non_ready", lambda b: b.faits["noeud_k8s"].update(ready=False)),
    ("cluster_degrade", lambda b: b.faits.update(cluster=(2, 3))),
    ("disk_pressure", lambda b: b.faits["noeud_k8s"].update(disk_pressure=True)),
    ("drapeau_scenario_absent", lambda b: b.faits_porte["drapeaux"].update({E.DRAPEAU_SCENARIO: "absent"})),
    ("ancien_drapeau_actif", lambda b: b.faits_porte["drapeaux"].update({E.DRAPEAU_ANCIEN: "true"})),
    ("bridge_version", lambda b: b.faits_porte["bridge"].update(sha256="0" * 64)),
    ("bridge_capacite_absente", lambda b: b.faits_porte["bridge"].update(capacites=["HOST_IMAGE_GC"])),
    ("bridge_injoignable", lambda b: b.faits_porte.update(bridge={"joignable": False, "capacites": None, "sha256": None})),
    ("sonde_bridge_ko", lambda b: b.faits_porte.update(bridge_sonde={"tentee": True, "ok": False})),
    ("image_backend", lambda b: b.faits_porte["backend"].update(image="autre:1")),
    ("pod_backend_ancienne_image", lambda b: b.faits_porte["backend"].update(images_pods=[IMAGE, "pfe-backend:0.2.164-vm3auto"])),
    ("env_backend_perime", lambda b: b.faits_porte["backend"].update(env_a_jour=False)),
    ("cooldown_backend", lambda b: b.faits_porte.update(actions_disque_recentes_s=[60])),
    ("incident_concurrent", lambda b: b.faits_porte.update(incidents_disque_ouverts=[{"id": 9, "app": "k8s-worker1", "statut": "OPEN"}])),
    ("pages_hote", lambda b: b.faits_porte["hote"].update(pages_s=9000.0)),
    ("pages_illisibles", lambda b: b.faits_porte["hote"].update(pages_s=None)),
    ("prom_incident_actif", lambda b: b.faits["prom"].update(incident_actif=1)),
    ("prom_illisible", lambda b: b.faits["prom"].update(illisibles=["usage"])),
    ("vmdk_snapshot", lambda b: b.faits["hote"]["vmdk"].update(snapshots=1)),
    ("c_plein", lambda b: b.faits["hote"].update(c_libre_go=10.0)),
    ("identite_hostname", lambda b: b.faits["noeud"].update(hostname="k8s-worker2")),
    ("systemd_run_absent", lambda b: b.faits_porte["systemd"].update(systemd_run=False)),
    ("residus_lanceur_porte", lambda b: b.faits_porte["systemd"].update(unites_residuelles=["helios-diskfill-garde-x.service"])),
]


class TestLigneDeCommande(SansInfra):
    def test_preflight_nominal_ne_modifie_rien_et_indique_la_commande_directe(self):
        b = Banc()
        avant = copy.deepcopy(b.m.textes)
        code, sortie = b.preflight()
        self.assertEqual(code, 0, sortie)
        self.assertEqual(b.exe.ecritures, [])
        self.assertEqual(b.m.textes, avant, "pre-vol : RIEN n'est ecrit sur .158")
        self.assertIn("VERDICT : VERT", sortie)
        self.assertIn("RIEN n'a ete ecrit ni depose", sortie)
        self.assertIn("FACULTATIF", sortie)
        self.assertIn("UNE SEULE commande, SANS armement", sortie)
        self.assertIn("sudo systemctl start helios-diskfill.service", sortie)
        self.assertIn("journalctl -fu helios-diskfill -u helios-diskguard", sortie)
        self.assertNotIn("--armer", sortie.replace("--armer est", ""), "plus aucune instruction d'armement")
        self.assertNotIn("DISKFILL-158-81-82-83", sortie, "plus de texte de confirmation d'armement")
        for ip, argv in b.exe.appels:
            self.assertIn(argv[5] if argv[2] == "/usr/bin/python3" else argv[2], ("sha256sum", D.CHEMIN_SCRIPT))
            if argv[2] == "/usr/bin/python3":
                self.assertEqual(argv[6:], ["etat", "--json"], "seule lecture du noeud : etat --json")

    def test_armer_est_obsolete_aucune_connexion_aucun_depot(self):
        for args in (["--armer"], ["--armer", "--confirm", "DISKFILL-158-81-82-83", "--image-backend", IMAGE],
                     ["--armer", "--confirm", "DISKFILL-158-81-82-83", "--image-backend", IMAGE, "--accepte-orange", "GEL_HOTE_48H", "--ttl-min", "10"],
                     ["--image-backend", IMAGE, "--armer"]):
            b = Banc()
            avant = copy.deepcopy(b.m.textes)
            code, sortie = b.lancer(*args)
            self.assertEqual(code, 2, args)
            self.assertIn("OBSOLETE", sortie)
            self.assertIn("sudo systemctl start helios-diskfill.service", sortie)
            self.assertEqual(b.exe.appels, [], "aucune connexion, aucune lecture du noeud")
            self.assertEqual(b.m.textes, avant, "rien n'est depose")

    def test_image_backend_obligatoire(self):
        b = Banc()
        code, sortie = b.lancer("--preflight")
        self.assertEqual(code, 2)
        self.assertIn("--image-backend", sortie)
        self.assertEqual(b.exe.appels, [])

    def test_aucun_demarrage_aucune_injection_par_ce_module(self):
        b = Banc()
        b.preflight()
        b.lancer("--statut")
        for ip, argv in b.exe.appels:
            texte = " ".join(argv)
            for interdit in ("systemctl", "fallocate", "start", "systemd-run", "rm ", "kubectl", "armer"):
                self.assertNotIn(interdit, texte)
        self.assertEqual(b.exe.ecritures, [])
        self.assertEqual(b.m.appels_de("allouer"), [])
        self.assertEqual(b.m.appels_de("liberer"), [])
        self.assertEqual(b.m.fichiers, {})

    def test_les_rouge_du_lanceur_sont_affiches_et_signales_jamais_ignores_par_le_pre_vol(self):
        for nom, mutation in ROUGES_DU_LANCEUR:
            with self.subTest(nom):
                b = Banc()
                mutation(b)
                code, sortie = b.preflight()
                self.assertEqual(code, 1, "%s doit rester un ROUGE du pre-vol\n%s" % (nom, sortie[-600:]))
                self.assertIn("ATTENTION : le diagnostic manuel est ROUGE", sortie)
                self.assertIn("ExecStartPre", sortie)
                self.assertEqual(b.exe.ecritures, [])

    def test_les_rouge_du_service_sont_affiches(self):
        def sha(m): m.contenu_sha["diskfill.py"] = "9" * 64

        def orphelin(m): m.fichiers[D.nom_fichier("e" * 32, 1)] = {"taille": MIO, "uid": 0, "nlink": 1, "type": "f"}

        def mode(m): m.unites[D.UNITE_PRINCIPALE].update(StateDirectoryMode="0755")

        def recharger(m): m.unites[D.UNITE_PRINCIPALE].update(NeedDaemonReload="yes")

        def cooldown(m): m.textes[D.F_COOLDOWN] = {"debut_epoch": m.t - 60}

        def occupe(m): m.occupees[D.UNITE_PRINCIPALE] = True

        def transitoire(m): m.transitoires = ["helios-diskfill-expire-" + "a" * 32 + ".timer"]

        def non_installe(m): m.contenu_sha["helios-diskguard.service"] = None
        tous = ",".join(c for c in H.CODES_ORANGE_ACQUITTABLES if c != H.CODE_REMEDIATION_NON_ACTIVEE)
        for nom, f in (("sha", sha), ("orphelin", orphelin), ("state_directory_mode", mode), ("a_recharger", recharger), ("cooldown", cooldown),
                       ("occupe", occupe), ("transitoire", transitoire), ("non_installe", non_installe)):
            with self.subTest(nom):
                b = Banc()
                f(b.m)
                code, sortie = b.preflight("--accepte-orange", tous)
                self.assertEqual(code, 1, sortie[-500:])
                self.assertEqual(b.exe.ecritures, [])

    def test_orange_affiche_code_zero_et_l_acquittement_ne_depose_rien(self):
        b = Banc()
        b.faits["hote"]["gels"] = [(7200, 150, "k8s-worker1")]
        avant = copy.deepcopy(b.m.textes)
        code, sortie = b.preflight()
        self.assertEqual(code, 0, "un ORANGE n'est pas un ROUGE")
        self.assertIn("GEL_HOTE_48H", sortie)
        code2, sortie2 = b.preflight("--accepte-orange", "GEL_HOTE_48H")
        self.assertEqual(code2, 0, sortie2)
        self.assertEqual(b.m.textes, avant, "l'acquittement est un AFFICHAGE : rien n'est enregistre sur .158")
        self.assertEqual(b.exe.ecritures, [])
        self.assertIn("non bloquants pour le demarrage direct", sortie)
        self.assertIn("acquittement exige pour --go", sortie)
        self.assertIn("acquitte par --accepte-orange GEL_HOTE_48H", sortie2)
        self.assertNotIn("ExecStartPre exige VERT strict", sortie, "en mode autonome, le demarrage n'exige rien de Windows")

    def test_en_mode_requis_un_orange_est_annonce_comme_bloquant_pour_le_demarrage(self):
        with attestation_requise():
            b = Banc()
            b.faits["hote"]["gels"] = [(7200, 150, "k8s-worker1")]
            code, sortie = b.preflight()
            self.assertEqual(code, 0)
            self.assertIn("ExecStartPre exige VERT strict", sortie)
            code2, sortie2 = b.preflight("--accepte-orange", "GEL_HOTE_48H")
            self.assertIn("acquitte par --accepte-orange GEL_HOTE_48H", sortie2)
            self.assertIn("ExecStartPre exige VERT strict", sortie2)

    def test_le_statut_indique_le_mode_de_l_attestation(self):
        b = Banc()
        code, sortie = b.lancer("--statut")
        self.assertEqual(code, 0)
        self.assertIn("attestation de l'hote Windows : NON requise", sortie)
        with attestation_requise():
            b = Banc()
            code, sortie = b.lancer("--statut")
            self.assertIn("attestation de l'hote Windows : REQUISE", sortie)

    def test_acquittements_refuses(self):
        for args in (["--accepte-orange", "REMEDIATION_NON_ACTIVEE"], ["--accepte-orange", "PRIMAIRE_CNPG_ACCEPTE"], ["--accepte-orange", "INCONNU"],
                     ["--accepte-orange", "GEL_HOTE_48H,REMEDIATION_NON_ACTIVEE"], ["--accepte-orange", "gel_hote_48h"]):
            b = Banc()
            code, sortie = b.preflight(*args)
            self.assertEqual(code, 2, args)
            self.assertEqual(b.exe.appels, [])

    def test_le_risque_cnpg_n_a_aucune_option(self):
        b = Banc()
        with self.assertRaises(SystemExit):
            with mock.patch.object(sys, "stderr"):
                b.lancer("--preflight", "--image-backend", IMAGE, "--accepte-risque-primaire-cnpg")

    def test_les_options_d_armement_n_existent_plus(self):
        for args in (["--preflight", "--image-backend", IMAGE, "--confirm", "x"], ["--preflight", "--image-backend", IMAGE, "--ttl-min", "10"]):
            b = Banc()
            with self.assertRaises(SystemExit):
                with mock.patch.object(sys, "stderr"):
                    b.lancer(*args)
            self.assertEqual(b.exe.appels, [])

    def test_option_abregee_refusee(self):
        b = Banc()
        with self.assertRaises(SystemExit):
            with mock.patch.object(sys, "stderr"):
                b.lancer("--prefl", "--image-backend", IMAGE)

    def test_statut_lecture_seule(self):
        b = Banc()
        avant = copy.deepcopy(b.m.textes)
        code, sortie = b.lancer("--statut")
        self.assertEqual(code, 0)
        self.assertEqual(b.m.textes, avant)
        self.assertEqual(b.exe.ecritures, [])
        self.assertIn("demarrage : DIRECT (aucun armement)", sortie)
        self.assertIn("identique a la source de verite", sortie)
        self.assertIn("inodes de / : 11.0 %", sortie)
        self.assertNotIn("armement : present", sortie)

    def test_statut_non_installe(self):
        b = Banc()
        b.m.contenu_sha = dict((k, None) for k in b.m.contenu_sha)
        code, sortie = b.lancer("--statut")
        self.assertEqual(code, 0)
        self.assertIn("NON INSTALLE", sortie)

    def test_le_preflight_du_lanceur_est_appele_tel_quel(self):
        """Le module appelle les MEMES fonctions que `helios_disk_test.py --target 158 --go` : collecte reelle, porte du scenario, evaluation."""
        b = Banc()
        deps = FauxDeps(b.exe)
        appels = []
        porte = H.module_porte_e2e()
        vrai_evaluer = porte.evaluer_e2e

        def evaluer_espion(*a, **k):
            appels.append((a, k))
            return vrai_evaluer(*a, **k)
        with mock.patch.object(porte, "evaluer_e2e", evaluer_espion), \
                mock.patch.object(H, "collecter_reel", lambda d, cle, vm_base: (b.faits, {"exe": b.exe, "primaire": "postgres-8", "id0": 0})), \
                mock.patch.object(porte, "collecter_faits_e2e", lambda d, exe, prim: b.faits_porte):
            out = []
            code = S.principal(["--preflight", "--image-backend", IMAGE], deps=deps, sortie=out.append)          # collecteur PAR DEFAUT
        self.assertEqual(code, 0, "\n".join(out))
        self.assertEqual(len(appels), 1)
        a, k = appels[0]
        self.assertEqual(a[3], "158")
        self.assertEqual(tuple(a[2]), tuple(porte.parametres()), "parametres FIGES du scenario")
        self.assertEqual(k["image_backend_attendue"], IMAGE)

    def test_le_collecteur_par_defaut_est_celui_du_lanceur(self):
        src = open(os.path.join(HERE, "helios_diskfill_service.py"), "rb").read().decode("ascii")
        self.assertIn("H.collecter_reel(deps, CLE, a.vm_base)", src)
        self.assertIn("porte.collecter_faits_e2e(deps, ctx[\"exe\"], ctx[\"primaire\"])", src)
        self.assertIn("porte.evaluer_e2e(faits, faits_porte, porte.parametres(), CLE", src)


# ------------------------------------------------------------------------------------------------ hote -> noeud, de bout en bout (monde emule)
class TestBoutEnBout(SansInfra):
    def test_demarrage_direct_sans_aucune_preparation_puis_helios_libere(self):
        b = Banc(helios="resolve")
        c1, c2, c3, out = TS.cycle(b.m)
        self.assertEqual((c1, c2, c3), (0, 0, 0), "\n".join(out))
        self.assertEqual(b.exe.appels, [], "l'hote n'a RIEN fait : aucun armement, aucun pre-vol, aucune connexion")
        self.assertEqual(b.m.appels_de("liberer"), [], "NOMINAL : seul HELIOS libere")
        self.assertIsNone(b.m.bridge_refus)
        self.assertLessEqual(b.m.usage_max, 82.0)
        self.assertTrue(any("VERIFICATION OK : demarrage direct" in l for l in out))

    def test_le_pre_vol_facultatif_ne_change_rien_au_demarrage(self):
        b = Banc(helios="resolve")
        avant = copy.deepcopy(b.m.textes)
        self.assertEqual(b.preflight()[0], 0)
        self.assertEqual(b.m.textes, avant)
        c1, c2, c3, out = TS.cycle(b.m)
        self.assertEqual((c1, c2, c3), (0, 0, 0), "\n".join(out))

    def test_par_defaut_un_pre_vol_rouge_est_signale_mais_le_noeud_demarre_sans_consulter_windows(self):
        """LIMITE ASSUMEE du demarrage autonome (decision du 17 nov. 2026) : les conditions de l'HOTE ne sont plus une porte ; le pre-vol facultatif les
        signale ; le noeud, lui, reste borne par TOUTES ses protections."""
        for nom, mutation in (("ram_basse", ROUGES_DU_LANCEUR[0][1]), ("bridge_injoignable", ROUGES_DU_LANCEUR[11][1]),
                              ("drapeau_scenario_absent", ROUGES_DU_LANCEUR[7][1])):
            with self.subTest(nom):
                b = Banc(helios="resolve")
                mutation(b)
                code, sortie = b.preflight()
                self.assertEqual(code, 1, "le diagnostic signale le ROUGE")
                self.assertIn("NE le consulte PAS", sortie)
                b.m.preflight_hote_resultat = {"verdict": "ROUGE", "go": False, "reasons": [{"level": "ROUGE", "code": nom}]}
                c1, c2, c3, out = TS.cycle(b.m)
                self.assertEqual((c1, c2, c3), (0, 0, 0), "le noeud demarre : il ne consulte pas Windows\n" + "\n".join(out))
                self.assertEqual(b.m.appels_de("preflight_hote"), [])
                self.assertLessEqual(b.m.usage_max, D.PLAFOND_PCT + 1e-9, "toutes les limites du noeud restent appliquees")

    def test_en_mode_requis_un_preflight_hote_rouge_refuse_le_demarrage_direct_sans_allocation(self):
        """Mode OPTIONNEL : le preflight Windows est execute automatiquement a chaque start ; un ROUGE ne peut pas etre ignore."""
        with attestation_requise():
            for nom, mutation in (("ram_basse", ROUGES_DU_LANCEUR[0][1]), ("bridge_injoignable", ROUGES_DU_LANCEUR[11][1]),
                                  ("drapeau_scenario_absent", ROUGES_DU_LANCEUR[7][1])):
                with self.subTest(nom):
                    b = Banc(helios="resolve")
                    mutation(b)
                    code, sortie = b.preflight()
                    self.assertEqual(code, 1, "le diagnostic Windows signale le ROUGE")
                    self.assertIn("sera refuse par ExecStartPre", sortie, "en mode requis, le ROUGE est annonce comme bloquant")
                    self.assertNotIn("NE le consulte PAS", sortie)
                    b.m.preflight_hote_resultat = {"verdict": "ROUGE", "go": False,
                                                   "reasons": [{"level": "ROUGE", "code": nom}]}
                    c1, c2, c3, out = TS.cycle(b.m)
                    self.assertEqual((c1, c2, c3), (2, None, 0), "le noeud refuse avant allocation\n" + "\n".join(out))
                    self.assertEqual(b.m.appels_de("allouer"), [])

    def test_sans_helios_le_noeud_libere_lui_meme_en_securite_et_reste_sous_le_plafond(self):
        """Hote non pret / HELIOS absent : le demarrage direct ne laisse JAMAIS le disque rempli (fin d'observation) et ne depasse jamais le plafond."""
        b = Banc(helios="absent")
        c1, c2, c3, out = TS.cycle(b.m)
        self.assertEqual(c1, 0, "\n".join(out))
        self.assertEqual(c2, D.CODES_SORTIE["AUCUNE_DETECTION"], "\n".join(out))
        self.assertEqual(sorted(b.m.fichiers), [], "les fichiers sont liberes par le service (securite)")
        self.assertLessEqual(b.m.usage_max, D.PLAFOND_PCT + 1e-9)

    def test_deux_demarrages_directs_consecutifs_le_cooldown_protege(self):
        b = Banc(helios="resolve")
        self.assertEqual(TS.cycle(b.m)[:2], (0, 0))
        b.m.reinit_helios()
        b.m.demarrer_garde()
        b.m.inv = "inv-0002"
        c1, c2, c3, out = TS.cycle(b.m)
        self.assertEqual(c1, 2)
        self.assertTrue(any("[KO] COOLDOWN" in l for l in out), "\n".join(out))
        code, sortie = b.preflight()
        self.assertEqual(code, 1)
        self.assertIn("SVC_COOLDOWN_LOCAL", sortie, "le pre-vol facultatif voit aussi le cooldown local")
        b.m.dormir(D.COOLDOWN_LOCAL_S)
        b.m.demarrer_garde()
        code, sortie = b.preflight()
        self.assertEqual(code, 0, sortie[-800:])
        b.m.reinit_helios()
        self.assertEqual(TS.cycle(b.m)[:2], (0, 0))

    def test_un_fichier_d_unite_modifie_est_refuse_au_demarrage_direct(self):
        for unite in ("helios-diskfill.service", "helios-diskguard.service"):
            b = Banc(helios="resolve")
            b.m.contenu_sha[unite] = "9" * 64
            c1, c2, c3, out = TS.cycle(b.m)
            self.assertEqual((c1, c2), (2, None), unite)
            self.assertEqual(b.m.appels_de("allouer"), [])
            self.assertTrue(any("EMPREINTE DIFFERENTE" in l for l in out))

    def test_les_orphelins_bloquent_le_demarrage_direct_et_aucun_nettoyage_n_est_fait(self):
        b = Banc(helios="absent")
        self.assertEqual(TS.cycle(b.m)[0], 0)
        b.m.pannes.add("liberer_refuse")
        b.m.dormir(D.COOLDOWN_LOCAL_S)
        b.m.demarrer_garde()
        b.m.inv = "inv-0002"
        TS.cycle(b.m)
        reste = sorted(b.m.fichiers)
        self.assertGreater(len(reste), 0)
        b.m.pannes.discard("liberer_refuse")
        b.m.garde_active = False
        b.m.dormir(D.COOLDOWN_LOCAL_S)
        b.m.demarrer_garde()
        b.m.inv = "inv-0003"
        c1, c2, c3, out = TS.cycle(b.m)
        self.assertEqual(c1, 2)
        self.assertTrue(any("ORPHELINS" in l and "nettoyer-orphelins" in l for l in out), "\n".join(out))
        code, sortie = b.preflight()
        self.assertEqual(code, 1)
        self.assertIn("SVC_ORPHELINS", sortie)
        self.assertEqual(sorted(b.m.fichiers), reste, "AUCUN nettoyage automatique")


# ------------------------------------------------------------------------------------------------ source verrouille
class TestSourceHote(SansInfra):
    @classmethod
    def setUpClass(cls):
        cls.texte = open(os.path.join(HERE, "helios_diskfill_service.py"), "rb").read().decode("ascii")
        cls.arbre = ast.parse(cls.texte)

    def test_aucun_sous_processus_aucune_connexion(self):
        importes = set()
        for n in ast.walk(self.arbre):
            if isinstance(n, ast.Import):
                importes.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                importes.add(n.module)
        for interdit in ("subprocess", "socket", "urllib.request", "http.client", "shutil", "ctypes", "paramiko", "base64", "secrets"):
            self.assertNotIn(interdit, importes)
        self.assertNotIn("os.system", self.texte)
        self.assertNotIn("shell=True", self.texte)

    def test_ecritures_locales_limitees(self):
        ouvertures = [n for n in ast.walk(self.arbre) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open"]
        modes = []
        for n in ouvertures:
            mode = n.args[1].value if len(n.args) > 1 and isinstance(n.args[1], ast.Constant) else "r"
            modes.append(mode)
        self.assertEqual(sorted(modes), ["rb", "rb", "wb"], "deux lectures et UNE ecriture locale (SHA256SUMS)")

    def test_aucune_commande_d_ecriture_distante(self):
        appels = [n for n in ast.walk(self.arbre) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "argv_script"]
        mots = sorted(a.args[0].value for a in appels if a.args and isinstance(a.args[0], ast.Constant))
        self.assertEqual(mots, ["etat"], "la SEULE commande du noeud est `etat --json` (lecture seule)")
        self.assertEqual(self.texte.count('"armer", "--charge"'), 0)
        chaines = [n.value for n in ast.walk(self.arbre) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for c in chaines:
            self.assertNotIn("armer --charge", c)

    def test_aucun_orange_cree_par_le_module(self):
        self.assertIn('NIVEAUX_SERVICE = ("ROUGE", "INFO")', self.texte)
        self.assertNotIn('add("ORANGE"', self.texte)

    def test_la_porte_est_importee_par_le_lanceur_pas_directement(self):
        self.assertNotIn("import helios_disk_e2e", self.texte)
        self.assertIn("H.module_porte_e2e()", self.texte)

    def test_options_de_ligne_de_commande(self):
        options = []
        for n in ast.walk(self.arbre):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_argument":
                options.append(n.args[0].value)
        self.assertEqual(sorted(options), sorted(["--preflight", "--statut", "--sommes", "--accepte-orange", "--image-backend", "--vm-base"]))
        self.assertNotIn("--armer", options, "--armer est traite AVANT le parseur, comme obsolete (aucune connexion)")
        self.assertNotIn("--accepte-risque-primaire-cnpg", options)

    def test_armer_est_intercepte_avant_toute_lecture(self):
        principal = [f for f in self.arbre.body if isinstance(f, ast.FunctionDef) and f.name == "principal"][0]
        src = ast.get_source_segment(self.texte, principal)
        self.assertLess(src.index('"--armer" in mots'), src.index("construire_parseur()"))
        self.assertLess(src.index('"--armer" in mots'), src.index("H.Dependances()"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
