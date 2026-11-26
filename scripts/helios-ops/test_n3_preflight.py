# -*- coding: utf-8 -*-
"""Tests du pré-vol N3 (fonctions pures : aucun réseau, aucun kubectl, aucun PowerShell).

Lancer :  python -I scripts/helios-ops/test_n3_preflight.py

Les tests de la classe CoherenceTest interdisent le retour de la contradiction signalée le 6 nov. 2026 : les cibles du pré-vol, la liste
du script d'activation et les VM `mode: AUTO` de la politique (infra/helios-vm-policy.yaml) doivent rester IDENTIQUES. Consigne finale :
QUATRE cibles (.155 .158 .162 + .153 depuis la consigne du 17 nov. 2026 qui inverse « .153 jamais cible ») ; .160 (supervision) en
DÉTECTION SEULE.
"""
import contextlib
import datetime
import gzip
import hashlib
import importlib.util
import io
import os
import re
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOL = os.path.join(REPO, "infra", "helios-sot", "tools", "n3_preflight.py")
spec = importlib.util.spec_from_file_location("n3_preflight", TOOL)
n3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n3)


def facts(vm="k8s-worker1"):
    states = {n: {"mode": "AUTO", "etat": "RUNNING", "age_ms": 400, "refus": ["STATE_RUNNING"], "cooldown_s": 0,
                  "dernier_demarrage": None}
              for n in ("k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-master")}
    states["k8s-monitoring"] = {"mode": "DETECT_ONLY", "etat": "RUNNING", "age_ms": 400,
                                "refus": ["DETECTION_ONLY", "STATE_RUNNING"], "cooldown_s": 0, "dernier_demarrage": None}
    sain = {"ok": True, "detail": "k8s_api OPEN=True, k8s_masters UP=True, serveurs=7 dont hors-UP=0"}
    out = {
        "vm": vm, "vm_states": states,
        "watchdog_check": {"vmrun_found": True, "vmx": {vm: True}, "token_configured": True},
        "host": {"ecouteur_9099": 1, "ecouteur_9999": 0, "taches": {"HELIOS-Watchdog": "Running"}, "drapeaux_maintenance": 0,
                 "ram_dispo_mo": 3400, "sur_secteur": True, "batterie_pct": "100", "disque_c_libre_go": 33.5,
                 "disque_c_libre_pct": 7.1,
                 "verrou_vmx": {"lu": True, "proprietaire": None}},   # HA-155-20261122 : aucun verrou .vmx sur la cible
        "config": {"HELIOS_VMPOWER_ENABLED": "true", "HELIOS_VMPOWER_DRYRUN": "true", "HELIOS_VMPOWER_ACTIVE_VMS": ""},
        "pods": [{"name": "backend-x-aaaaa", "node": "k8s-worker2", "ready": True, "restarts": 0, "warn_recent": 0},
                 {"name": "backend-x-bbbbb", "node": "k8s-worker1", "ready": True, "restarts": 0, "warn_recent": 0}],
        "leader_node": "k8s-worker2",
        "nodes": {"k8s-master": True, "k8s-worker1": True, "k8s-worker2": True},
        "etcd": {ip: True for ip in n3.ETCD_IPS},
        "etcd_leader": "192.168.148.153",
        "cnpg": {"pods": [{"name": "postgres-5", "node": "k8s-worker2", "ready": True, "role": "primary"},
                          {"name": "postgres-7", "node": "k8s-worker1", "ready": True, "role": "replica"}],
                 "repl_state": "streaming", "repl_lag_bytes": 0},
        "db": {"open_incidents": 0, "recent_restart_actions": 0},
        "backup_age_min": 45.0, "ram_history": [3400, 3500], "revive_present": True,
        "host_stalls": {"evalue": True, "fenetres": [], "derniere_fin_il_y_a_min": None},
        "loop": {"last_ts": "2026-11-06T09:24:00+01:00", "last_verdict": "OK"},
        "policy": {"mode": "AUTO", "restart_route": n3.TARGETS[vm]["route"], "notifications": "EMAIL_AND_UI"},
    }
    if n3.TARGETS[vm]["kind"] in ("infra", "control-plane"):   # .162 : .160 doit tenir l'entrée ; .153 : les DEUX HAProxy portent l'API
        out["lb"] = {"ha": {ip: dict(sain) for ip in n3.LB_IPS}}
    if n3.TARGETS[vm]["kind"] == "control-plane":   # relevé réel du 17 nov. 2026 : opérateur CNPG ÉPINGLÉ sur .153, leaders sur les workers
        out["cp"] = {"apiserver": {"192.168.148.155": True, "192.168.148.158": True},
                     "leaders": {"kube-controller-manager": "k8s-worker1", "kube-scheduler": "k8s-worker2"},
                     "operator_node": "k8s-master", "kubeconfig_server": "https://api.helios.orange.tn:6443"}
    return out


def blocking_labels(f):
    return [label for st, label, _ in n3.evaluate(f) if st == n3.NOGO]


class GoTest(unittest.TestCase):
    def test_etat_nominal_est_go_pour_chaque_cible(self):
        for vm in n3.TARGETS:
            rows = n3.evaluate(facts(vm))
            self.assertEqual(n3.verdict(rows), n3.GO, (vm, [r for r in rows if r[0] != n3.GO]))

    def test_aucune_ligne_attention_dans_letat_nominal(self):
        for vm in n3.TARGETS:
            warns = [lab for st, lab, _ in n3.evaluate(facts(vm)) if st == n3.WARN]
            # la fixture reproduit l'état réel : le leader du backend et le primaire CNPG sont sur .155 (k8s-worker2) ; le leader etcd
            # ET l'opérateur CNPG (épinglé) sont sur .153 (k8s-master) : 2 attentions dans chacun de ces deux cas
            attendu = 2 if vm in ("k8s-worker2", "k8s-master") else 0
            self.assertEqual(len(warns), attendu, (vm, warns))


class NoGoTest(unittest.TestCase):
    def check_nogo(self, mutate, expected_fragment, vm="k8s-worker1"):
        f = facts(vm)
        mutate(f)
        rows = n3.evaluate(f)
        self.assertEqual(n3.verdict(rows), n3.NOGO)
        self.assertTrue(any(expected_fragment in lab for lab in blocking_labels(f)), (expected_fragment, blocking_labels(f)))

    def test_une_vm_pas_running(self):
        self.check_nogo(lambda f: f["vm_states"]["k8s-lb2"].update(etat="POWERED_OFF"), "RUNNING")

    def test_preuve_du_watchdog_perimee(self):
        self.check_nogo(lambda f: f["vm_states"]["k8s-lb2"].update(age_ms=60000), "RUNNING")

    def test_cible_pas_en_auto(self):
        self.check_nogo(lambda f: f["vm_states"]["k8s-worker1"].update(mode="DETECT_ONLY"), "mode AUTO")

    def test_monitoring_qui_nest_plus_detect_only(self):
        """.160 (supervision) ne doit JAMAIS être armable : si le watchdog la déclare AUTO, aucun essai n'est GO."""
        self.check_nogo(lambda f: f["vm_states"]["k8s-monitoring"].update(mode="AUTO"), "k8s-monitoring")

    def test_cible_master_pas_en_auto_cote_watchdog(self):
        """Tant que le watchdog DÉPLOYÉ dit DENY_RESTART pour .153 (avant le palier 3), le pré-vol de .153 est NO-GO."""
        self.check_nogo(lambda f: f["vm_states"]["k8s-master"].update(mode="DENY_RESTART"), "mode AUTO", vm="k8s-master")

    def test_maintenance_cooldown_ou_demarrage_anterieur(self):
        self.check_nogo(lambda f: f["vm_states"]["k8s-worker1"].update(refus=["STATE_RUNNING", "MAINTENANCE"]), "maintenance")
        self.check_nogo(lambda f: f["vm_states"]["k8s-worker1"].update(cooldown_s=900), "cooldown")

    def test_un_demarrage_anterieur_est_une_information_pas_un_blocage(self):
        # re-validation après un premier essai réussi : le cooldown écoulé et l'absence de maintenance suffisent
        f = facts("k8s-worker1")
        f["vm_states"]["k8s-worker1"].update(dernier_demarrage={"at": "2026-11-06T09:43:11Z", "incident": 4888, "result": "STARTED"})
        rows = n3.evaluate(f)
        self.assertEqual(n3.verdict(rows), n3.GO)
        self.assertTrue(any(st == n3.WARN and "démarrage antérieur" in lab for st, lab, _ in rows))

    def test_fichier_vmx_ou_vmrun_ou_jeton_absent(self):
        self.check_nogo(lambda f: f["watchdog_check"]["vmx"].update({"k8s-worker1": False}), "vmx")
        self.check_nogo(lambda f: f["watchdog_check"].update(vmrun_found=False), "vmx")
        self.check_nogo(lambda f: f["watchdog_check"].update(token_configured=False), "vmx")

    def test_watchdog_hors_service_ou_agent_9999_de_retour(self):
        self.check_nogo(lambda f: f["host"].update(ecouteur_9099=0), "watchdog en marche")
        self.check_nogo(lambda f: f["host"].update(ecouteur_9999=1), "watchdog en marche")

    def test_drapeau_de_maintenance_sur_lhote(self):
        self.check_nogo(lambda f: f["host"].update(drapeaux_maintenance=1), "drapeau")

    # --- HA-155-20261122 : l'interface VMware qui tient le verrou .vmx de la cible bloque `vmrun start` -------------------
    def test_interface_vmware_qui_tient_le_verrou_de_la_cible_est_bloquante(self):
        for vm in n3.TARGETS:
            self.check_nogo(lambda f: f["host"].update(verrou_vmx={
                "lu": True, "proprietaire": {"pid": 2272, "exe": "vmware.exe", "alive": True}}), "interface VMware", vm=vm)

    def test_verrou_non_lu_ou_fait_absent_est_bloquant_fail_closed(self):
        self.check_nogo(lambda f: f["host"].update(verrou_vmx={"lu": False, "proprietaire": None}), "interface VMware")
        self.check_nogo(lambda f: f["host"].pop("verrou_vmx"), "interface VMware")

    def test_processus_indeterminable_est_traite_comme_vivant(self):
        self.check_nogo(lambda f: f["host"].update(verrou_vmx={
            "lu": True, "proprietaire": {"pid": 2272, "exe": "vmware.exe", "alive": None}}), "interface VMware")

    def test_verrou_orphelin_ou_d_un_autre_processus_n_est_pas_bloquant(self):
        for proprio in ({"pid": 4816, "exe": "vmware.exe", "alive": False},      # interface disparue : VMware nettoie le verrou
                        {"pid": 777, "exe": "vmware-vmx.exe", "alive": True}):    # processus de la VM : sans effet sur le démarrage
            f = facts("k8s-worker2")
            f["host"]["verrou_vmx"] = {"lu": True, "proprietaire": proprio}
            rows = n3.evaluate(f)
            self.assertEqual(n3.verdict(rows), n3.GO, [r for r in rows if r[0] != n3.GO])

    def test_vmx_lock_owner_lit_le_fichier_de_verrou_sans_rien_modifier(self):
        with tempfile.TemporaryDirectory() as root:
            vm = "k8s-worker2"
            self.assertEqual({"lu": True, "proprietaire": None}, n3.vmx_lock_owner(vm, root=root))     # aucun dossier de verrou
            d = os.path.join(root, vm, vm + ".vmx.lck")
            os.makedirs(d)
            self.assertEqual({"lu": True, "proprietaire": None}, n3.vmx_lock_owner(vm, root=root))     # dossier vide
            fichier = os.path.join(d, "M23772.lck")
            with open(fichier, "wb") as fh:
                fh.write(b"uuid=89-38-78-ec-5b-f3-ec-11-81-0f-e4-a8-df-de-60-33\x00\x01 2272-134397777182469068(vmware.exe) 1 X 1\x00\n")
            avant = os.stat(fichier).st_mtime_ns
            got = n3.vmx_lock_owner(vm, root=root, alive=lambda pid, exe: (pid, exe) == (2272, "vmware.exe"))
            self.assertEqual({"lu": True, "proprietaire": {"pid": 2272, "exe": "vmware.exe", "alive": True}}, got)
            got = n3.vmx_lock_owner(vm, root=root, alive=lambda pid, exe: False)
            self.assertIs(False, got["proprietaire"]["alive"])
            self.assertEqual(avant, os.stat(fichier).st_mtime_ns)       # lecture seule
            self.assertEqual(["M23772.lck"], os.listdir(d))

    def test_surveillance_desactivee(self):
        self.check_nogo(lambda f: f["config"].update(HELIOS_VMPOWER_ENABLED="false"), "ENABLED")

    def test_backend_pas_2_sur_2(self):
        self.check_nogo(lambda f: f["pods"][0].update(ready=False), "2 pods Ready")
        self.check_nogo(lambda f: f["pods"].pop(), "2 pods Ready")

    def test_signe_de_panne_camunda_recent_sur_une_cible_camunda(self):
        self.check_nogo(lambda f: f["pods"][0].update(warn_recent=3), "Camunda")

    def test_lb2_est_aussi_soumise_au_controle_camunda(self):
        f = facts("k8s-lb2")
        f["pods"][0]["warn_recent"] = 3
        self.assertEqual(n3.verdict(n3.evaluate(f)), n3.NOGO)

    def check_nogo_lb(self, mutate, fragment, vm="k8s-lb2"):
        self.check_nogo(mutate, fragment, vm=vm)

    def test_repartiteurs_autre_haproxy_en_panne_ou_haproxy_de_la_cible_malade(self):
        malade = {"ok": False, "detail": "serveurs=8 dont hors-UP=2"}
        # .162 : l'autre répartiteur est .160
        self.check_nogo_lb(lambda f: f["lb"]["ha"].update({"192.168.148.160": malade}), "AUTRE répartiteur")
        self.check_nogo_lb(lambda f: f["lb"]["ha"].pop("192.168.148.160"), "AUTRE répartiteur")
        self.check_nogo_lb(lambda f: f["lb"]["ha"].update({"192.168.148.162": malade}), "HAProxy de la cible")

    def test_repartiteur_sans_facts_est_refuse_fail_closed(self):
        f = facts("k8s-lb2")
        f["lb"] = {"ha": {}}   # collecte vide : rien n'est prouvé
        self.assertEqual(n3.verdict(n3.evaluate(f)), n3.NOGO)

    # ---- plan de contrôle (.153, consigne du 17 nov. 2026) ----
    def check_nogo_cp(self, mutate, fragment):
        self.check_nogo(mutate, fragment, vm="k8s-master")

    def test_plan_de_controle_apiserver_d_un_autre_noeud_en_panne_ou_non_lu(self):
        self.check_nogo_cp(lambda f: f["cp"]["apiserver"].update({"192.168.148.158": False}), "apiservers des DEUX autres")
        self.check_nogo_cp(lambda f: f["cp"]["apiserver"].pop("192.168.148.158"), "apiservers des DEUX autres")

    def test_plan_de_controle_kubeconfig_sur_la_cible_ou_illisible(self):
        self.check_nogo_cp(lambda f: f["cp"].update(kubeconfig_server="https://192.168.148.153:6443"), "kubeconfig")
        self.check_nogo_cp(lambda f: f["cp"].update(kubeconfig_server=None), "kubeconfig")

    def test_plan_de_controle_un_haproxy_malade_ou_non_lu(self):
        malade = {"ok": False, "detail": "serveurs=8 dont hors-UP=2"}
        self.check_nogo_cp(lambda f: f["lb"]["ha"].update({"192.168.148.160": malade}), "DEUX répartiteurs")
        self.check_nogo_cp(lambda f: f["lb"]["ha"].update({"192.168.148.162": malade}), "DEUX répartiteurs")
        self.check_nogo_cp(lambda f: f.pop("lb"), "DEUX répartiteurs")        # fait absent = NO-GO (fail-closed)

    def test_plan_de_controle_sans_faits_est_refuse_fail_closed(self):
        f = facts("k8s-master")
        f.pop("cp")
        self.assertEqual(n3.verdict(n3.evaluate(f)), n3.NOGO)
        self.assertTrue(any("apiservers" in lab for lab in blocking_labels(f)))
        self.assertTrue(any("kubeconfig" in lab for lab in blocking_labels(f)))

    def test_criteres_du_plan_de_controle_absents_pour_les_autres_cibles(self):
        for vm in ("k8s-worker1", "k8s-worker2", "k8s-lb2"):
            labels = " ".join(lab for _, lab, _ in n3.evaluate(facts(vm)))
            self.assertNotIn("kubeconfig", labels, vm)
            self.assertNotIn("apiservers", labels, vm)

    def test_critere_de_repartiteur_absent_pour_un_worker(self):
        labels = [lab for _, lab, _ in n3.evaluate(facts("k8s-worker1"))]
        self.assertFalse(any("répartiteur" in lab or "HAProxy" in lab for lab in labels))

    def test_politique_du_depot_non_conforme_ou_absente(self):
        self.check_nogo(lambda f: f["policy"].update(mode="DETECT_ONLY"), "politique du dépôt")
        self.check_nogo(lambda f: f["policy"].update(restart_route="BACKEND_DIRECT"), "politique du dépôt")
        self.check_nogo(lambda f: f["policy"].update(notifications="UI_ONLY"), "politique du dépôt")
        self.check_nogo(lambda f: f.update(policy=None), "politique du dépôt")

    def test_noeud_pas_ready(self):
        self.check_nogo(lambda f: f["nodes"].update({"k8s-master": False}), "nœuds")

    def test_etcd_4_sur_5(self):
        self.check_nogo(lambda f: f["etcd"].update({"192.168.148.160": False}), "etcd")
        self.check_nogo(lambda f: f["etcd"].pop("192.168.148.160"), "etcd")

    def test_cnpg_replication_cassee_ou_retard(self):
        self.check_nogo(lambda f: f["cnpg"].update(repl_state="none"), "streaming")
        self.check_nogo(lambda f: f["cnpg"].update(repl_state="streaming", repl_lag_bytes=50_000_000), "streaming")
        self.check_nogo(lambda f: f["cnpg"].update(repl_lag_bytes=None), "streaming")

    def test_cnpg_pods_incomplets(self):
        self.check_nogo(lambda f: f["cnpg"]["pods"].pop(), "CNPG")
        self.check_nogo(lambda f: f["cnpg"]["pods"][1].update(ready=False), "CNPG")

    def test_incident_ouvert_ou_action_recente_sur_la_cible(self):
        self.check_nogo(lambda f: f["db"].update(open_incidents=1), "incident ouvert")
        self.check_nogo(lambda f: f["db"].update(recent_restart_actions=1), "RESTART_VM")
        self.check_nogo(lambda f: f.update(db={}), "incident ouvert")

    def test_sauvegarde_trop_ancienne_ou_inconnue(self):
        self.check_nogo(lambda f: f.update(backup_age_min=382.0), "sauvegarde")
        self.check_nogo(lambda f: f.update(backup_age_min=None), "sauvegarde")

    def test_ram_basse_maintenant_ou_dans_lhistorique(self):
        self.check_nogo(lambda f: f["host"].update(ram_dispo_mo=1980), "maintenant")
        self.check_nogo(lambda f: f.update(ram_history=[2189, 3405]), "relevés de la boucle")

    def test_hote_sur_batterie_ou_disque_plein(self):
        self.check_nogo(lambda f: f["host"].update(sur_secteur=False), "secteur")
        self.check_nogo(lambda f: f["host"].update(disque_c_libre_go=8.0), "disque")

    def test_filet_de_securite_absent(self):
        self.check_nogo(lambda f: f.update(revive_present=False), "vm_revive")

    def test_gel_d_hote_recent_est_bloquant(self):
        """Écart C4 (6 nov. 2026) : un gel d'E/S de l'hôte terminé 87 s avant l'arrêt réel n'était pas un critère du pré-vol."""
        for vm in n3.TARGETS:
            self.check_nogo(lambda f: f.update(host_stalls={"evalue": True, "fenetres": [(100.0, 140.0)], "derniere_fin_il_y_a_min": 1.5}),
                            "gel d'E/S", vm=vm)

    def test_prometheus_muet_est_un_no_go_fail_closed(self):
        self.check_nogo(lambda f: f.update(host_stalls={"evalue": False, "fenetres": [], "derniere_fin_il_y_a_min": None}), "gel d'E/S")
        self.check_nogo(lambda f: f.pop("host_stalls"), "gel d'E/S")                     # fait absent = NO-GO
        self.check_nogo(lambda f: f.update(host_stalls=None), "gel d'E/S")


class GelsDHoteTest(unittest.TestCase):
    """host_stalls_recent : fenêtres des 60 dernières minutes lues dans Prometheus (source simulée ici : aucun réseau)."""
    NOW = 1_700_000_000.0          # dans le passé : stall_windows borne la fin de requête à l'heure réelle

    def fake(self, pts):
        return lambda q, a, b, step: [{"values": [[self.NOW - 60 * 60 + s, str(n)] for s, n in pts]}]

    def test_aucun_gel_dans_l_heure(self):
        r = n3.host_stalls_recent(now=self.NOW, fetch=self.fake([(10, 1), (15, 2), (20, 0)]))   # jamais 3 membres à la fois
        self.assertEqual(r, {"evalue": True, "fenetres": [], "derniere_fin_il_y_a_min": None})

    def test_un_gel_il_y_a_quelques_minutes(self):
        s = 50 * 60                                                                          # 10 min avant maintenant
        r = n3.host_stalls_recent(now=self.NOW, fetch=self.fake([(s, 4), (s + 5, 5)]))
        self.assertTrue(r["evalue"])
        self.assertEqual(len(r["fenetres"]), 1)
        self.assertAlmostEqual(r["derniere_fin_il_y_a_min"], round((10 * 60 - 5) / 60.0, 1), places=1)

    def test_prometheus_injoignable_n_est_pas_evalue(self):
        def boom(*a):
            raise OSError("Prometheus injoignable")
        self.assertEqual(n3.host_stalls_recent(now=self.NOW, fetch=boom), {"evalue": False, "fenetres": [], "derniere_fin_il_y_a_min": None})

    def test_le_critere_est_dans_la_documentation_de_l_outil(self):
        self.assertIn("aucun gel d'E/S ou réseau de l'hôte", n3.__doc__)
        self.assertEqual(n3.STALL_LOOKBACK_MIN, 60)


class AttentionTest(unittest.TestCase):
    def test_historique_de_ram_trop_court_est_une_attention_pas_un_blocage(self):
        f = facts()
        f["ram_history"] = [3400]
        rows = n3.evaluate(f)
        self.assertEqual(n3.verdict(rows), n3.GO)
        self.assertTrue(any(st == n3.WARN and "relevés de la boucle" in lab for st, lab, _ in rows))

    def test_leader_primaire_et_etcd_sur_la_cible_sont_des_attentions(self):
        f = facts("k8s-worker2")
        f["etcd_leader"] = "192.168.148.155"
        rows = n3.evaluate(f)
        warns = [lab for st, lab, _ in rows if st == n3.WARN]
        self.assertEqual(n3.verdict(rows), n3.GO)
        self.assertTrue(any("leader du backend" in w for w in warns))
        self.assertTrue(any("primaire" in w for w in warns))
        self.assertTrue(any("leader etcd" in w for w in warns))

    def test_plan_de_controle_leaders_operateur_et_pods_sur_la_cible_sont_des_attentions(self):
        f = facts("k8s-master")
        f["cp"]["leaders"] = {"kube-controller-manager": "k8s-master", "kube-scheduler": "k8s-master"}
        f["pods"][0]["node"] = "k8s-master"
        rows = n3.evaluate(f)
        warns = [lab for st, lab, _ in rows if st == n3.WARN]
        self.assertEqual(n3.verdict(rows), n3.GO)        # des attentions, JAMAIS des blocages
        self.assertTrue(any("leaders controller-manager" in w for w in warns), warns)
        self.assertTrue(any("opérateur CNPG" in w for w in warns), warns)
        self.assertTrue(any("aucun pod HELIOS" in w for w in warns), warns)

    def test_leader_etcd_sur_un_repartiteur_est_une_attention(self):
        f = facts("k8s-lb2")
        f["etcd_leader"] = "192.168.148.162"
        rows = n3.evaluate(f)
        self.assertEqual(n3.verdict(rows), n3.GO)
        self.assertTrue(any(st == n3.WARN and "leader etcd" in lab for st, lab, _ in rows))

    def test_leader_etcd_non_etabli_est_une_attention_pas_un_succes(self):
        f = facts()
        f["etcd_leader"] = None
        rows = n3.evaluate(f)
        self.assertEqual(n3.verdict(rows), n3.GO)
        self.assertTrue(any(st == n3.WARN and "leader etcd" in lab for st, lab, _ in rows))

    def test_derniere_observation_pas_ok_ou_pod_redemarre(self):
        f = facts()
        f["loop"]["last_verdict"] = "WARN"
        f["pods"][0]["restarts"] = 1
        rows = n3.evaluate(f)
        self.assertEqual(n3.verdict(rows), n3.GO)
        self.assertEqual(len([r for r in rows if r[0] == n3.WARN]), 2)


class PointDObservationTest(unittest.TestCase):
    """Juste après un redémarrage du backend, le bail pointe ~60 s vers un pod disparu : le pré-vol doit quand même LIRE l'état."""
    PODS = [{"name": "backend-new-aaa", "ready": True}, {"name": "backend-new-bbb", "ready": True}]

    def test_le_leader_vivant_et_pret_est_prefere(self):
        self.assertEqual(n3.pick_vantage(self.PODS, "backend-new-bbb"), "backend-new-bbb")

    def test_bail_vers_un_pod_disparu_repli_sur_un_pod_pret(self):
        self.assertEqual(n3.pick_vantage(self.PODS, "backend-old-zzz"), "backend-new-aaa")
        self.assertEqual(n3.pick_vantage(self.PODS, None), "backend-new-aaa")

    def test_leader_non_pret_repli_sur_un_pod_pret(self):
        pods = [{"name": "a", "ready": False}, {"name": "b", "ready": True}]
        self.assertEqual(n3.pick_vantage(pods, "a"), "b")

    def test_aucun_pod_pret_aucun_point_d_observation(self):
        self.assertIsNone(n3.pick_vantage([{"name": "a", "ready": False}], "a"))
        self.assertIsNone(n3.pick_vantage([], None))

    def test_leader_inconnu_est_une_attention_jamais_un_succes_silencieux(self):
        for vm in n3.TARGETS:
            f = facts(vm)
            f["leader_node"] = None
            rows = n3.evaluate(f)
            self.assertEqual(n3.verdict(rows), n3.GO, vm)
            self.assertTrue(any(st == n3.WARN and "leader du backend" in lab for st, lab, _ in rows), vm)


class ConsequencesTest(unittest.TestCase):
    def test_etat_calme_aucun_effet_particulier_pour_worker1(self):
        self.assertEqual(n3.consequences(facts("k8s-worker1")), [])

    def test_worker2_porte_leader_primaire_et_etcd_les_trois_effets_sont_annonces(self):
        f = facts("k8s-worker2")
        f["etcd_leader"] = "192.168.148.155"
        texte = " ".join(n3.consequences(f))
        self.assertIn("leader du backend", texte)
        self.assertIn("PRIMAIRE PostgreSQL", texte)
        self.assertIn("aucun switchover", texte)
        self.assertIn("LEADER etcd", texte)

    def test_master_annonce_etcd_apiserver_operateur_cnpg_epingle_et_calico(self):
        f = facts("k8s-master")
        texte = " ".join(n3.consequences(f))
        self.assertIn("LEADER etcd", texte)               # le leader etcd du relevé est sur .153
        self.assertIn("kube-apiserver", texte)
        self.assertIn("ÉPINGLÉ", texte)                   # opérateur CNPG épinglé sur la cible
        self.assertIn("aucune bascule PostgreSQL automatique", texte)
        self.assertIn("calico-kube-controllers", texte)
        f["cp"]["operator_node"] = "k8s-worker1"
        self.assertNotIn("ÉPINGLÉ", " ".join(n3.consequences(f)))

    def test_lb2_annonce_la_bascule_de_lentree_et_les_mesures_ha01(self):
        self.assertIn("HAProxy de .160", " ".join(n3.consequences(facts("k8s-lb2"))))



class HaproxyCsvTest(unittest.TestCase):
    HDR = "# pxname,svname,qcur,qmax,scur,smax,slim,stot,bin,bout,dreq,dresp,ereq,econ,eresp,wretr,wredis,status,weight"

    @classmethod
    def csv(cls, api="OPEN", masters="UP", servers=("UP", "UP", "UP")):
        rows = [cls.HDR, f"k8s_api,FRONTEND,,,0,1,2000,5,0,0,0,0,0,,,,,{api},",
                f"k8s_masters,BACKEND,0,0,0,1,200,5,0,0,0,0,,0,0,0,0,{masters},1"]
        rows += [f"k8s_masters,master-{i},0,0,0,1,,5,0,0,,0,,0,0,0,0,{s},1" for i, s in enumerate(servers)]
        return "\n".join(rows) + "\n"

    def test_page_de_stats_saine(self):
        ok, detail = n3.parse_haproxy_csv(self.csv())
        self.assertTrue(ok, detail)

    def test_un_serveur_pas_up_est_refuse(self):
        self.assertFalse(n3.parse_haproxy_csv(self.csv(servers=("UP", "DOWN", "UP")))[0])
        self.assertFalse(n3.parse_haproxy_csv(self.csv(servers=("UP", "MAINT", "UP")))[0])

    def test_frontend_api_ferme_ou_backend_masters_down_sont_refuses(self):
        self.assertFalse(n3.parse_haproxy_csv(self.csv(api="STOP"))[0])
        self.assertFalse(n3.parse_haproxy_csv(self.csv(masters="DOWN"))[0])

    def test_transition_up_1_sur_3_est_acceptee_comme_dans_le_verificateur(self):
        self.assertTrue(n3.parse_haproxy_csv(self.csv(servers=("UP 1/3", "UP", "UP")))[0])

    def test_page_vide_illisible_ou_sans_serveur_est_refusee(self):
        for txt in ("", None, "pas du csv", self.HDR + "\n", "# a,b,c\nx,y,z\n"):
            self.assertFalse(n3.parse_haproxy_csv(txt)[0], txt)
        self.assertFalse(n3.parse_haproxy_csv(self.csv(servers=()))[0])

    def test_ligne_tronquee_est_refusee(self):
        self.assertFalse(n3.parse_haproxy_csv(self.HDR + "\nk8s_api,FRONTEND,1\n")[0])


class SauvegardeTest(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 6, 9, 0, tzinfo=datetime.timezone.utc)

    @staticmethod
    def job(name, done, succeeded=1, owner=None):
        meta = {"name": name}
        if owner:
            meta["ownerReferences"] = [{"kind": "CronJob", "name": owner}]
        st = {"succeeded": succeeded}
        if done:
            st["completionTime"] = done
        return {"metadata": meta, "status": st}

    def test_sauvegarde_manuelle_reconnue_par_son_proprietaire_pas_par_son_nom(self):
        jobs = [self.job("postgres-backup-29898840", "2026-11-06T02:00:08Z"),
                self.job("n3-bk-a-0935", "2026-11-06T08:56:43Z", owner="postgres-backup")]
        self.assertAlmostEqual(n3.latest_backup_age_min(jobs, self.NOW), 3.28, places=1)

    def test_planifiee_reconnue_par_son_nom_meme_sans_proprietaire(self):
        self.assertAlmostEqual(n3.latest_backup_age_min([self.job("postgres-backup-b-29898870", "2026-11-06T02:30:07Z")], self.NOW),
                               389.9, places=1)

    def test_job_etranger_echoue_ou_sans_fin_ignore(self):
        jobs = [self.job("autre-job", "2026-11-06T08:59:00Z"),
                self.job("n3-bk-x", "2026-11-06T08:59:00Z", owner="un-autre-cronjob"),
                self.job("n3-bk-y", "2026-11-06T08:59:00Z", succeeded=None, owner="postgres-backup"),
                self.job("n3-bk-z", None, owner="postgres-backup")]
        self.assertIsNone(n3.latest_backup_age_min(jobs, self.NOW))

    def test_aucun_job(self):
        self.assertIsNone(n3.latest_backup_age_min([], self.NOW))


class RamAcquitteeTest(unittest.TestCase):
    """`--ack-ram-mo` : acquittement EXPLICITE (17 nov. 2026, « j'accepte la RAM à 2 Go ») du SEUL critère « RAM hôte >= 2300 Mo »."""

    @staticmethod
    def ligne(f):
        return next(r for r in n3.evaluate(f) if "RAM hôte disponible" in r[1])

    @staticmethod
    def facts_ram(ram, ack=None):
        f = facts("k8s-master")
        f["host"].update(ram_dispo_mo=ram)
        if ack is not None:
            f["ack_ram_mo"] = ack
        return f

    def test_sans_acquittement_2061_mo_reste_NO_GO(self):
        self.assertEqual(self.ligne(self.facts_ram(2061))[0], n3.NOGO)

    def test_avec_acquittement_a_1900_2061_mo_donne_GO_et_le_dit(self):
        st, label, detail = self.ligne(self.facts_ram(2061, 1900))
        self.assertEqual(st, n3.GO)
        self.assertIn("ACQUITTÉ", label)
        self.assertIn("--ack-ram-mo", label)
        self.assertIn("1500", detail, "la garde du watchdog (plancher jamais franchi) est rappelée")

    def test_sous_le_seuil_acquitte_reste_NO_GO(self):
        st, _, detail = self.ligne(self.facts_ram(1850, 1900))
        self.assertEqual(st, n3.NOGO)
        self.assertIn("NON satisfait", detail)

    def test_ram_suffisante_passe_normalement_meme_avec_acquittement(self):
        st, label, _ = self.ligne(self.facts_ram(2400, 1900))
        self.assertEqual(st, n3.GO)
        self.assertNotIn("ACQUITTÉ", label)

    def test_un_acquittement_hors_bornes_est_ignore(self):
        for hors in (1000, 1499, 2300, 5000, True):
            st, label, _ = self.ligne(self.facts_ram(2061, hors))
            self.assertEqual(st, n3.NOGO, hors)
            self.assertNotIn("ACQUITTÉ", label, hors)

    def test_l_acquittement_ne_touche_aucun_autre_critere(self):
        f = self.facts_ram(2061, 1900)
        f["host_stalls"] = {"evalue": True, "fenetres": [(100.0, 140.0)], "derniere_fin_il_y_a_min": 25.0}
        rows = n3.evaluate(f)
        gel = next(r for r in rows if "gel d'E/S" in r[1])
        self.assertEqual(gel[0], n3.NOGO, "le gel d'E/S reste bloquant malgré l'acquittement RAM")
        self.assertEqual(n3.verdict(rows), n3.NOGO)
        f2 = self.facts_ram(2061, 1900)
        f2["backup_age_min"] = 1148.0                                       # sauvegarde périmée : toujours bloquante
        self.assertEqual(next(r for r in n3.evaluate(f2) if "sauvegarde PostgreSQL" in r[1])[0], n3.NOGO)

    def test_avec_tout_le_reste_GO_l_acquittement_suffit_au_verdict(self):
        self.assertEqual(n3.verdict(n3.evaluate(self.facts_ram(2061, 1900))), n3.GO)
        self.assertEqual(n3.verdict(n3.evaluate(self.facts_ram(2061))), n3.NOGO)

    def test_refus_en_ligne_de_commande_hors_bornes_sans_aucune_collecte(self):
        original = n3.collect
        n3.collect = lambda *a, **k: self.fail("aucune collecte ne doit avoir lieu pour une option refusée")
        try:
            for v in ("1000", "1499", "2300", "5000"):
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(n3.main(["--vm", "k8s-master", "--ack-ram-mo", v]), 2, v)
                self.assertIn("REFUSÉ", out.getvalue())
        finally:
            n3.collect = original


class CopieHoteTest(unittest.TestCase):
    """`--accept-host-copy` : une copie hors cluster VÉRIFIÉE (sans rotation) vaut preuve de sauvegarde ; sans l'option, rien ne change."""
    NOW = datetime.datetime(2026, 11, 17, 22, 0, tzinfo=datetime.timezone.utc)
    MARQUEUR = b"\n--\n-- PostgreSQL database dump complete\n--\n"

    def ecrire(self, dossier, nom, corps=None, marqueur=True, tronquer=0):
        corps = os.urandom(70_000) if corps is None else corps          # incompressible : taille compressée >= 50 000 o
        chemin = os.path.join(dossier, nom)
        with gzip.open(chemin, "wb") as gz:
            gz.write(corps + (self.MARQUEUR if marqueur else b"\n"))
        if tronquer:
            with open(chemin, "rb") as fh:
                data = fh.read()
            with open(chemin, "wb") as fh:
                fh.write(data[:-tronquer])
        return chemin

    def test_copie_saine_est_acceptee_avec_son_age_et_son_empreinte(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.ecrire(d, "pfe_db-hostcopy-20261117-215500.sql.gz")
            r = n3.verify_host_copy(p, self.NOW)
            self.assertTrue(r["ok"], r)
            self.assertAlmostEqual(r["age_min"], 5.0, places=3)
            with open(p, "rb") as fh:
                self.assertEqual(r["sha256"], hashlib.sha256(fh.read()).hexdigest())

    def test_copie_tronquee_sans_marqueur_trop_petite_mal_nommee_ou_du_futur_est_refusee(self):
        with tempfile.TemporaryDirectory() as d:
            cas = {
                "tronquee": (self.ecrire(d, "pfe_db-hostcopy-20261117-215500.sql.gz", tronquer=200), "illisible"),
                "sans_marqueur": (self.ecrire(d, "pfe_db-hostcopy-20261117-215501.sql.gz", marqueur=False), "marqueur"),
                "trop_petite": (self.ecrire(d, "pfe_db-hostcopy-20261117-215502.sql.gz", corps=b"x" * 100), "trop petite"),
                "mal_nommee": (self.ecrire(d, "copie-sauvage.sql.gz"), "nom inattendu"),
                "du_futur": (self.ecrire(d, "pfe_db-hostcopy-20261118-090000.sql.gz"), "futur"),
            }
            for nom, (chemin, fragment) in cas.items():
                r = n3.verify_host_copy(chemin, self.NOW)
                self.assertFalse(r["ok"], nom)
                self.assertIn(fragment, r["raison"], nom)

    def test_la_plus_recente_est_retenue_et_une_recente_corrompue_n_est_pas_remplacee_par_une_ancienne_saine(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(n3.latest_verified_host_copy(d, self.NOW), "dossier vide")
            self.assertIsNone(n3.latest_verified_host_copy(os.path.join(d, "inexistant"), self.NOW))
            self.ecrire(d, "pfe_db-hostcopy-20261117-200000.sql.gz")
            recente = self.ecrire(d, "pfe_db-hostcopy-20261117-215000.sql.gz")
            r = n3.latest_verified_host_copy(d, self.NOW)
            self.assertTrue(r["ok"])
            self.assertEqual(r["path"], recente)
            self.ecrire(d, "pfe_db-hostcopy-20261117-215900.sql.gz", marqueur=False)       # plus récente MAIS incomplète
            r = n3.latest_verified_host_copy(d, self.NOW)
            self.assertFalse(r["ok"], "fail-closed : la copie la plus récente est mauvaise, l'ancienne saine ne la remplace pas")

    # ── effet sur le critère du pré-vol ────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def ligne(f):
        return next(r for r in n3.evaluate(f) if "sauvegarde PostgreSQL" in r[1])

    @staticmethod
    def copie(age, ok=True, raison=""):
        return {"ok": ok, "path": r"C:\x\pfe_db-hostcopy-20261117-215500.sql.gz", "age_min": age, "size": 276737,
                "sha256": "ab" * 32, "raison": raison}

    def test_sans_l_option_le_critere_est_inchange_meme_avec_une_copie_parfaite(self):
        f = facts("k8s-master")
        f.update(backup_age_min=1148.0, host_copy=self.copie(30.0))          # accept_host_copy absent : la copie est IGNORÉE
        st, label, detail = self.ligne(f)
        self.assertEqual(st, n3.NOGO)
        self.assertNotIn("COPIE HÔTE", label)
        self.assertIn("1148", detail)

    def test_avec_l_option_une_copie_recente_verifiee_donne_GO_sans_rotation(self):
        f = facts("k8s-master")
        f.update(backup_age_min=1148.0, accept_host_copy=True, host_copy=self.copie(30.0))
        st, label, detail = self.ligne(f)
        self.assertEqual(st, n3.GO)
        self.assertIn("COPIE HÔTE", label)
        self.assertIn("--accept-host-copy", label)
        self.assertIn("AUCUNE rotation", detail)
        self.assertIn("1148", detail, "l'âge des Jobs des CronJobs reste affiché")

    def test_avec_l_option_une_copie_trop_ancienne_refusee_ou_absente_reste_NO_GO(self):
        for copie, fragment in ((self.copie(200.0), "trop ancienne"), (self.copie(5.0, ok=False, raison="marqueur absent"), "REFUSÉE"),
                                (None, "aucune trouvée")):
            f = facts("k8s-master")
            f.update(backup_age_min=1148.0, accept_host_copy=True, host_copy=copie)
            st, _, detail = self.ligne(f)
            self.assertEqual(st, n3.NOGO, fragment)
            self.assertIn(fragment, detail)

    def test_avec_l_option_un_job_de_cronjob_recent_suffit_sans_invoquer_la_copie(self):
        f = facts("k8s-master")
        f.update(backup_age_min=45.0, accept_host_copy=True, host_copy=None)
        st, label, _ = self.ligne(f)
        self.assertEqual(st, n3.GO)
        self.assertNotIn("COPIE HÔTE", label)


class PolitiqueTest(unittest.TestCase):
    def test_lecteur_ignore_les_commentaires_et_lit_les_entrees(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
            fh.write("# - {name: fantome, mode: AUTO}\nvms:\n"
                     "  # commentaire\n"
                     "  - {name: k8s-x, ip: 1.2.3.4, mode: AUTO, restart_route: CAMUNDA, notifications: EMAIL_AND_UI}\n"
                     "  - {name: k8s-y, ip: 1.2.3.5, mode: DENY_RESTART, restart_route: NONE, notifications: EMAIL_AND_UI}\n")
            path = fh.name
        try:
            p = n3.policy_entries(path)
        finally:
            os.unlink(path)
        self.assertEqual(sorted(p), ["k8s-x", "k8s-y"])
        self.assertEqual(p["k8s-x"]["mode"], "AUTO")
        self.assertEqual(p["k8s-y"]["restart_route"], "NONE")


class CoherenceTest(unittest.TestCase):
    """Garde-fous CONTRE la contradiction signalée le 6 nov. 2026 (pré-vol et politique en désaccord sur .160) : outils et politique
    doivent toujours dire la même chose. Consigne finale (17 nov. 2026) : QUATRE cibles AUTO (.153 comprise), .160 en détection seule."""

    @staticmethod
    def auto_dans_la_politique():
        return {n: e for n, e in n3.policy_entries().items() if e.get("mode") == "AUTO"}

    def test_les_cibles_du_pre_vol_sont_exactement_les_VM_AUTO_de_la_politique(self):
        auto = self.auto_dans_la_politique()
        self.assertEqual(set(n3.TARGETS), set(auto), "pré-vol et politique divergent")
        for nom, e in auto.items():
            self.assertEqual(n3.TARGETS[nom]["route"], e.get("restart_route"), nom)
            self.assertEqual(n3.TARGETS[nom]["ip"], e.get("ip"), nom)
            self.assertEqual("EMAIL_AND_UI", e.get("notifications"), nom)

    def test_le_script_d_activation_accepte_exactement_les_VM_AUTO_de_la_politique(self):
        with open(os.path.join(REPO, "infra", "helios-sot", "tools", "apply_vm4_auto.sh"), encoding="utf-8") as fh:
            script = fh.read()
        bloc = script.split("activate)", 1)[1].split("esac", 1)[0]
        m = re.search(r"^\s*((?:k8s-[a-z0-9]+\|)+k8s-[a-z0-9]+)\)\s*;;", bloc, re.M)
        self.assertIsNotNone(m, "liste de cibles introuvable dans le script d'activation")
        self.assertEqual(set(m.group(1).split("|")), set(self.auto_dans_la_politique()), "script et politique divergent")

    def test_quatre_cibles_monitoring_n_en_fait_pas_partie_et_master_est_auto(self):
        self.assertEqual(sorted(n3.TARGETS), ["k8s-lb2", "k8s-master", "k8s-worker1", "k8s-worker2"])
        self.assertNotIn("k8s-monitoring", n3.TARGETS, ".160 = VM de supervision : pas d'auto-rallumage")
        master = n3.policy_entries()["k8s-master"]
        self.assertEqual((master["mode"], master["restart_route"], master["notifications"]), ("AUTO", "CAMUNDA", "EMAIL_AND_UI"))
        self.assertEqual(n3.TARGETS["k8s-master"]["kind"], "control-plane")
        self.assertEqual(n3.TARGETS["k8s-master"]["ip"], "192.168.148.153")

    def test_monitoring_est_en_detection_seule_interface_seulement_dans_la_politique(self):
        e = n3.policy_entries()["k8s-monitoring"]
        self.assertEqual((e["mode"], e["restart_route"], e["notifications"]), ("DETECT_ONLY", "NONE", "UI_ONLY"))


class CiblesTest(unittest.TestCase):
    def test_main_refuse_monitoring_et_inconnue_sans_rien_collecter(self):
        original = n3.collect
        n3.collect = lambda *a, **k: self.fail("aucune collecte ne doit avoir lieu pour une VM non éligible")
        try:
            self.assertEqual(n3.main(["--vm", "k8s-monitoring"]), 2)   # .160 : détection seule, jamais une cible
            self.assertEqual(n3.main(["--vm", "lb2"]), 2)
            self.assertEqual(n3.main(["--vm", "192.168.148.153"]), 2)
        finally:
            n3.collect = original

    def test_perimetre_dincident_par_classe_de_vm(self):
        self.assertIn("NODE_NOT_READY", n3.scope_condition("k8s-worker1"))
        self.assertIn("namespace='k8s-nodes'", n3.scope_condition("k8s-worker2"))
        self.assertIn("VM_DOWN", n3.scope_condition("k8s-lb2"))
        self.assertIn("namespace='infra'", n3.scope_condition("k8s-lb2"))
        self.assertIn("app='k8s-lb2'", n3.scope_condition("k8s-lb2"))
        # .153 (plan de contrôle) : même incident qu'un worker (NODE_NOT_READY du détecteur Kubernetes)
        self.assertIn("NODE_NOT_READY", n3.scope_condition("k8s-master"))
        self.assertIn("namespace='k8s-nodes'", n3.scope_condition("k8s-master"))
        self.assertIn("app='k8s-master'", n3.scope_condition("k8s-master"))


if __name__ == "__main__":
    unittest.main()
