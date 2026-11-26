# -*- coding: utf-8 -*-
"""Tests du lanceur de test CPU (scripts/helios-ops/helios_cpu_test.py) : analyse du pre-vol (fonction pure `evaluer`),
parsing des releves et coherence avec le detecteur. Aucun reseau, aucune VM, aucune ecriture.

Lancer : python -I test_helios_cpu_test.py -v        (depuis scripts/helios-ops)
"""
import importlib.util
import io
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("helios_cpu_test_mod", os.path.join(HERE, "helios_cpu_test.py"))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)

DETECTEUR = os.path.join(HERE, "detecter.candidate.py")

SORTIE_NOEUD = """LoadState=loaded
ActiveState=inactive
ActiveEnterTimestamp=
CPUQuotaPerSecUSec=infinity
DropInPaths=
LOAD=0.42
NOW=1800000000
"""


def noeud(**kw):
    d = {"joignable": True, "load_state": "loaded", "active": False, "depuis": "", "quota": "infinity",
         "plafond_temporaire": False, "expire_dans_s": None, "load": 0.5}
    d.update(kw)
    return d


def faits(**kw):
    f = {"noeuds": {n: noeud() for n in H.NOEUDS}, "cluster": (3, 3),
         "detecteur": {"type": "NO_INCIDENT", "cible": "aucun", "matches": [], "fiable": True, "v3": True},
         "disque_160": 70.0, "wal_mo": 300.0, "wal_echecs": 0, "pipeline": {"bridge": "active", "backend_pret": 2},
         "hote_ram_mo": 4000, "incidents_cpu": {}}
    f.update(kw)
    return f


def niveaux(rapport, n=None):
    raisons = rapport["noeuds"][n] if n else rapport["global"]
    return [x[0] for x in raisons]


class ParsingTests(unittest.TestCase):
    def test_etat_noeud_libre(self):
        e = H.etat_noeud(SORTIE_NOEUD)
        self.assertEqual((e["load_state"], e["active"], e["quota"], e["plafond_temporaire"], e["load"]),
                         ("loaded", False, "infinity", False, 0.42))
        self.assertIsNone(e["expire_dans_s"])

    def test_etat_noeud_avec_plafond_temporaire_et_expiration(self):
        sortie = SORTIE_NOEUD.replace("CPUQuotaPerSecUSec=infinity", "CPUQuotaPerSecUSec=250ms").replace(
            "DropInPaths=", "DropInPaths=/run/systemd/system/helios-cpuload.service.d/zzzzzzzz-helios-temporary-cpu-quota.conf"
        ) + "EXPIRE=1800000600\n"
        e = H.etat_noeud(sortie)
        self.assertTrue(e["plafond_temporaire"])
        self.assertEqual(e["quota"], "250ms")
        self.assertEqual(e["expire_dans_s"], 600)

    def test_parse_detecteur(self):
        ok = '{"type": "NO_INCIDENT", "cible": "aucun", "matches": [], "monitoringFiable": true}\n\n---\n1\n'
        d = H.parse_detecteur(ok)
        self.assertEqual((d["type"], d["matches"], d["fiable"], d["v3"]), ("NO_INCIDENT", [], True, True))
        self.assertFalse(H.parse_detecteur(ok.replace("\n1\n", "\n0\n"))["v3"])
        self.assertFalse(H.parse_detecteur('{"type": "X", "monitoringFiable": false}\n---\n0\n')["fiable"])
        self.assertIsNone(H.parse_detecteur("pas du json ---\n1\n"))
        self.assertIsNone(H.parse_detecteur(""))
        self.assertIsNone(H.parse_detecteur("[1,2]\n---\n1\n"))


class EvaluationTests(unittest.TestCase):
    def test_tout_au_vert(self):
        r = H.evaluer(faits(), ["158", "155", "153"])
        self.assertEqual(r["verdict"], "VERT")
        self.assertEqual(r["global"], [])
        self.assertTrue(all(v == "VERT" for v in r["verdicts"].values()))

    def test_plafond_temporaire_bloque_ce_noeud_seulement(self):
        f = faits()
        f["noeuds"]["158"] = noeud(plafond_temporaire=True, quota="250ms", expire_dans_s=540)
        r = H.evaluer(f, ["158", "155"])
        self.assertEqual((r["verdicts"]["158"], r["verdicts"]["155"], r["verdict"]), ("ROUGE", "VERT", "ROUGE"))
        msg = " ".join(x[1] for x in r["noeuds"]["158"])
        self.assertIn("bridee", msg)
        self.assertIn("9 min", msg)

    def test_incident_cpu_actif_est_un_frein(self):
        f = faits(incidents_cpu={"k8s-worker2": [{"id": "4032", "statut": "OPEN", "age_s": 99999}]})
        r = H.evaluer(f, ["155", "153"])
        self.assertEqual((r["verdicts"]["155"], r["verdicts"]["153"]), ("ROUGE", "VERT"))
        self.assertIn("CORRELE", r["noeuds"]["155"][0][1])

    def test_anti_flapping_5_min_est_une_information_sans_effet(self):
        """Le backend ne correle que si la NOUVELLE detection survient moins de 5 min apres l'incident precedent ; une charge
        lancee maintenant n'est detectee qu'~8 min plus tard : jamais bloquant (constate le 12 nov. 2026)."""
        f = faits(incidents_cpu={"k8s-master": [{"id": "9801", "statut": "RESOLVED", "age_s": 120}]})
        r = H.evaluer(f, ["153"])
        self.assertEqual(r["verdicts"]["153"], "VERT")
        self.assertEqual(niveaux(r, "153"), ["INFO"])
        f = faits(incidents_cpu={"k8s-master": [{"id": "9801", "statut": "RESOLVED", "age_s": 400}]})
        self.assertEqual(niveaux(H.evaluer(f, ["153"]), "153"), [])

    def test_cluster_degrade_bloque_tout(self):
        r = H.evaluer(faits(cluster=(2, 3)), ["158", "155", "153"])
        self.assertTrue(all(v == "ROUGE" for v in r["verdicts"].values()))
        self.assertEqual(niveaux(r), ["ROUGE"])

    def test_cluster_inconnu_orange(self):
        self.assertEqual(H.evaluer(faits(cluster=None), ["158"])["verdict"], "ORANGE")

    def test_detecteur_injoignable_ou_supervision_non_fiable(self):
        self.assertEqual(H.evaluer(faits(detecteur=None), ["158"])["verdict"], "ROUGE")
        d = {"type": "NO_INCIDENT", "cible": "aucun", "matches": [], "fiable": False, "v3": True}
        r = H.evaluer(faits(detecteur=d), ["158"])
        self.assertEqual(r["verdict"], "ROUGE")
        self.assertIn("NO_INCIDENT", r["global"][0][1])

    def test_anomalie_de_rang_superieur_orange_et_message_selon_la_version(self):
        for v3 in (True, False):
            d = {"type": "NODE_HIGH_MEMORY", "cible": "iacamunda", "matches": ["NODE_HIGH_MEMORY"], "fiable": True, "v3": v3}
            r = H.evaluer(faits(detecteur=d), ["158"])
            self.assertEqual(r["verdict"], "ORANGE")
            self.assertIn("NODE_HIGH_MEMORY", r["global"][0][1])
        d3 = {"type": "x", "cible": "y", "matches": ["NODE_HIGH_MEMORY"], "fiable": True, "v3": True}
        self.assertIn("10 min", H.evaluer(faits(detecteur=d3), ["158"])["global"][0][2])

    def test_le_cpu_lui_meme_n_est_pas_un_frein(self):
        d = {"type": "NODE_HIGH_CPU", "cible": "k8s-worker1", "matches": ["NODE_HIGH_CPU"], "fiable": True, "v3": True}
        self.assertEqual(H.evaluer(faits(detecteur=d), ["158"])["verdict"], "VERT")

    def test_disque_160(self):
        v3 = {"type": "x", "cible": "y", "matches": [], "fiable": True, "v3": True}
        v2 = dict(v3, v3=False)
        self.assertEqual(H.evaluer(faits(disque_160=70.0), ["158"])["verdict"], "VERT")
        self.assertEqual(H.evaluer(faits(disque_160=86.0), ["158"])["verdict"], "ORANGE")
        self.assertEqual(H.evaluer(faits(disque_160=92.0, detecteur=v3), ["158"])["verdict"], "ORANGE")
        self.assertEqual(H.evaluer(faits(disque_160=92.0, detecteur=v2), ["158"])["verdict"], "ROUGE")

    def test_wal_prometheus_anormal(self):
        self.assertEqual(H.evaluer(faits(wal_mo=1424.0), ["158"])["verdict"], "ORANGE")
        self.assertEqual(H.evaluer(faits(wal_echecs=6), ["158"])["verdict"], "ORANGE")
        self.assertEqual(H.evaluer(faits(wal_mo=300.0, wal_echecs=0), ["158"])["verdict"], "VERT")
        # apres la reparation : WAL petit et compteur remis a zero par le redemarrage => plus d'alerte
        self.assertEqual(niveaux(H.evaluer(faits(wal_mo=19.0, wal_echecs=0), ["158"])), [])

    def test_chaine_helios(self):
        self.assertEqual(H.evaluer(faits(pipeline={"bridge": "inactive", "backend_pret": 2}), ["158"])["verdict"], "ROUGE")
        self.assertEqual(H.evaluer(faits(pipeline={"bridge": "active", "backend_pret": 0}), ["158"])["verdict"], "ROUGE")
        self.assertEqual(H.evaluer(faits(pipeline={"bridge": "active", "backend_pret": 1}), ["158"])["verdict"], "ORANGE")

    def test_noeud_injoignable_ou_unite_absente(self):
        f = faits()
        f["noeuds"]["158"] = {"joignable": False}
        f["noeuds"]["155"] = noeud(load_state="not-found")
        r = H.evaluer(f, ["158", "155", "153"])
        self.assertEqual((r["verdicts"]["158"], r["verdicts"]["155"], r["verdicts"]["153"]), ("ROUGE", "ROUGE", "VERT"))

    def test_charge_deja_active_n_est_qu_une_information(self):
        f = faits()
        f["noeuds"]["158"] = noeud(active=True, depuis="Thu 2026-11-12 07:21:12 UTC")
        r = H.evaluer(f, ["158"])
        self.assertEqual(r["verdicts"]["158"], "VERT")
        self.assertEqual(niveaux(r, "158"), ["INFO"])

    def test_quota_anormal_sans_plafond_connu(self):
        f = faits()
        f["noeuds"]["153"] = noeud(quota="250ms")
        self.assertEqual(H.evaluer(f, ["153"])["verdicts"]["153"], "ROUGE")

    def test_ram_hote_basse(self):
        self.assertEqual(H.evaluer(faits(hote_ram_mo=800), ["158"])["verdict"], "ORANGE")

    def test_et_si_cpu_pris_en_charge(self):
        f = faits(et_si={n: ("NODE_HIGH_CPU", H.NOEUDS[n][0]) for n in H.NOEUDS})
        r = H.evaluer(f, ["158", "155", "153"])
        self.assertEqual(r["verdict"], "VERT")

    def test_et_si_cpu_masque_est_un_frein_precis(self):
        f = faits(et_si={"158": ("NODE_HIGH_MEMORY", "iacamunda"), "155": ("NODE_HIGH_CPU", "k8s-worker2")})
        r = H.evaluer(f, ["158", "155"])
        self.assertEqual((r["verdicts"]["158"], r["verdicts"]["155"]), ("ROUGE", "VERT"))
        self.assertIn("MASQUE", r["noeuds"]["158"][0][1])
        self.assertIn("NODE_HIGH_MEMORY", r["noeuds"]["158"][0][1])

    def test_et_si_remplace_l_heuristique_des_anomalies_de_rang_superieur(self):
        d = {"type": "NODE_DISK_FULL", "cible": "monitoring", "matches": ["NODE_DISK_FULL"], "fiable": True, "v3": False}
        sans = H.evaluer(faits(detecteur=d), ["158"])
        self.assertEqual(sans["verdict"], "ORANGE")                       # sans « et si » : heuristique prudente
        avec = H.evaluer(faits(detecteur=d, et_si={"158": ("NODE_HIGH_CPU", "k8s-worker1")}), ["158"])
        self.assertEqual(avec["verdict"], "VERT")                         # avec « et si » : sans effet sur le test
        self.assertEqual(niveaux(avec), ["INFO"])

    def test_parse_et_si(self):
        self.assertEqual(H.parse_et_si("bruit\nETSI|NODE_HIGH_CPU|k8s-worker1\n"), ("NODE_HIGH_CPU", "k8s-worker1"))
        self.assertIsNone(H.parse_et_si("ETSI|incomplet"))
        self.assertIsNone(H.parse_et_si(""))
        self.assertIsNone(H.parse_et_si(None))

    def test_affichage(self):
        f = faits(disque_160=88.0)
        r = H.evaluer(f, ["158", "155"])
        sortie = io.StringIO()
        H.afficher(r, ["158", "155"], sortie)
        texte = sortie.getvalue()
        self.assertIn("PRE-VOL DU TEST CPU", texte)
        self.assertIn(".158 (k8s-worker1) : ORANGE", texte)
        self.assertIn("VERDICT GLOBAL : ORANGE", texte)

    def test_noeud_inconnu(self):
        self.assertEqual(H.principal(["999"]), 2)


class CoherenceAvecLeDetecteurTests(unittest.TestCase):
    def test_les_regles_qui_passent_avant_le_cpu_sont_celles_du_detecteur(self):
        s = importlib.util.spec_from_file_location("det_pour_cpu_test", DETECTEUR)
        d = importlib.util.module_from_spec(s)
        s.loader.exec_module(d)
        noms = [r[0] for r in d.REGLES]
        self.assertEqual(noms[:10], H.REGLES_AU_DESSUS_DU_CPU)
        self.assertEqual(noms[10], "NODE_HIGH_CPU")

    def test_les_commandes_distantes_ne_dependent_pas_du_code_retour(self):
        """grep -c rend 1 quand il compte 0 et `[ -n ... ] && ...` rend 1 sans plafond : sans `true` final, le lanceur
        declarait a tort les VM injoignables (constate au premier essai reel)."""
        self.assertTrue(H.CMD_NOEUD.rstrip().endswith("true"))
        self.assertTrue(H.CMD_DETECTEUR.rstrip().endswith("true"))

    def test_l_expiration_du_plafond_se_lit_avec_list_timers(self):
        """`systemctl show 'glob' --value` ne rend RIEN pour les minuteurs transitoires de HELIOS (constate le 12 nov. 2026 :
        « expiration inconnue ») ; `list-timers` rend la date de la prochaine echeance."""
        self.assertIn("list-timers", H.CMD_NOEUD)
        self.assertNotIn("NextElapseUSecRealtime", H.CMD_NOEUD)

    def test_la_duree_annoncee_est_celle_mesuree(self):
        """Le plafond temporaire dure ~4 min (mesure du 12 nov. 2026 : pose 11:47:35, echeance 11:51:35), pas 15 min."""
        f = faits()
        f["noeuds"]["158"] = noeud(plafond_temporaire=True, quota="250ms", expire_dans_s=120)
        texte = " ".join(x[1] + " " + x[2] for x in H.evaluer(f, ["158"])["noeuds"]["158"])
        self.assertIn("4 min", texte)
        self.assertNotIn("15 min", texte)

    def test_noeuds_et_delais(self):
        self.assertEqual({n: v[0] for n, v in H.NOEUDS.items()}, {"158": "k8s-worker1", "155": "k8s-worker2", "153": "k8s-master"})
        self.assertEqual(H.DELAI_DETECTION_S, 480)
        self.assertEqual(H.ANTI_FLAPPING_S, 300)


if __name__ == "__main__":
    unittest.main()
