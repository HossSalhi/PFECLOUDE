# -*- coding: utf-8 -*-
"""Tests de la reconstruction de réplique SANS suppression de PVC/PV (environnement simulé). Lancer : python -I scripts/helios-ops/test_rebuild_replica.py"""
import copy
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools", "rebuild_replica.py"))
spec = importlib.util.spec_from_file_location("rebuild_replica", TOOL)
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)

CLUSTER_OBJ = {"spec": {"instances": 2}, "status": {"currentPrimary": "postgres-7", "targetPrimary": "postgres-7", "readyInstances": 1,
                                                      "phase": "Failing over"}}
POD = {"metadata": {"name": "postgres-5", "labels": {"cnpg.io/instanceRole": "replica"}}, "spec": {"nodeName": "k8s-worker2"},
       "status": {"conditions": [{"type": "Ready", "status": "False"}]}}
PVC = {"metadata": {"name": "postgres-5", "annotations": {"cnpg.io/pvcStatus": "ready"},
                    "ownerReferences": [{"apiVersion": "postgresql.cnpg.io/v1", "kind": "Cluster", "name": "postgres", "uid": "u-1", "controller": True}]},
       "spec": {"volumeName": "pv-5"}}
PV = {"spec": {"persistentVolumeReclaimPolicy": "Retain"}}


def facts(**over):
    f = {"instance": "postgres-5", "cluster": copy.deepcopy(CLUSTER_OBJ), "pod": copy.deepcopy(POD), "pvc": copy.deepcopy(PVC), "pv": copy.deepcopy(PV),
         "primary_ready": True, "eligible_nodes": ["k8s-worker2"], "free_gb": 13, "no_unique_writes": True,
         "no_unique_writes_detail": "fourche 117@57/86018340 : {'XLOG': 2}", "backup_problems": [], "backup_lines": ["A: ok", "B: ok"],
         "vm_start_in_progress": False, "open_vm_incidents": ["k8s-nodes/k8s-worker2"], "tolerated_incidents": ["k8s-nodes/k8s-worker2"]}
    f.update(over)
    return f


class FakeEnv:
    def __init__(self, f=None, never_healthy=False, bad_replication=False, annotation_not_read=False, owner_not_removed=False):
        self.f = f or facts()
        self.events, self.t = [], 1_800_000_000.0
        self.never_healthy, self.bad_replication = never_healthy, bad_replication
        self.annotation_not_read, self.owner_not_removed = annotation_not_read, owner_not_removed
        self.status = "ready"
        self.owner = True
        self.polls = 0

    def now(self): return self.t
    def sleep(self, s): self.t += s
    def collect(self, inst): self.events.append("collect"); return self.f
    def save_backup(self, inst, pvc): self.events.append("backup")
    def annotate_detached(self, inst):
        self.events.append("annotate")
        self.status = "ready" if self.annotation_not_read else "detached"
    def pvc_status(self, inst): return self.status
    def remove_owner_reference(self, inst, patch):
        self.events.append("patch_owner")
        self.patch = patch
        self.owner = self.owner_not_removed
    def has_cluster_owner(self, inst): return self.owner
    def delete_pod(self, inst): self.events.append("delete_pod")
    def cluster_status(self):
        self.polls += 1
        ok = (not self.never_healthy) and self.polls >= 2
        return {"readyInstances": 2 if ok else 1, "phase": rr.HEALTHY if ok else "Failing over"}
    def new_instances(self, old): return ["postgres-8@k8s-worker2", "postgres-7@k8s-worker1"]
    def replication(self): return {"state": "catchup" if self.bad_replication else "streaming", "lag_bytes": 0}
    def pv_retained(self, inst): return True


def run(env, apply=True):
    out = []
    rc = rr.run_rebuild(env, "postgres-5", apply, say=lambda *a: out.append(" ".join(str(x) for x in a)))
    return rc, "\n".join(out)


class FlowTest(unittest.TestCase):
    def test_nominal_ordre_strict_annotation_puis_proprietaire_puis_pod_et_aucune_suppression_de_volume(self):
        env = FakeEnv()
        rc, out = run(env)
        self.assertEqual(rc, 0, out)
        self.assertEqual(env.events, ["collect", "backup", "annotate", "patch_owner", "delete_pod"])
        self.assertIn("réplique reconstruite, ancien volume postgres-5 conservé", out)
        self.assertIn("ne se ferme PAS à la main", out)

    def test_dry_run_n_ecrit_rien_et_montre_le_diff_et_le_retour_arriere(self):
        env = FakeEnv()
        rc, out = run(env, apply=False)
        self.assertEqual(rc, 0)
        self.assertEqual(env.events, ["collect"])
        self.assertIn("cnpg.io/pvcStatus: detached", out)
        self.assertIn("aucun PVC, aucun PV, aucune donnée supprimé", out)
        self.assertIn("kubectl -n pfe annotate pvc postgres-5 cnpg.io/pvcStatus=ready --overwrite", out)
        self.assertIn("uid u-1", out)

    def test_le_pod_n_est_jamais_supprime_si_l_annotation_ou_le_proprietaire_ne_sont_pas_relus(self):
        for kw in ({"annotation_not_read": True}, {"owner_not_removed": True}):
            env = FakeEnv(**kw)
            rc, out = run(env)
            self.assertEqual(rc, 1, kw)
            self.assertNotIn("delete_pod", env.events)

    def test_base_qui_ne_redevient_pas_saine_arrete_avec_le_volume_intact(self):
        env = FakeEnv(never_healthy=True)
        rc, out = run(env)
        self.assertEqual(rc, 1)
        self.assertIn("INTACT", out)

    def test_replication_non_streaming_interdit_de_poursuivre_les_essais(self):
        rc, out = run(FakeEnv(bad_replication=True))
        self.assertEqual(rc, 1)
        self.assertIn("NE PAS poursuivre les essais réels", out)

    def test_aucune_suppression_de_pvc_ou_pv_dans_le_code(self):
        with open(TOOL, encoding="utf-8") as fh:
            code = fh.read().split('"""', 2)[2]
        self.assertNotIn('"delete", "pvc"', code)
        self.assertNotIn('"delete", "pv"', code)
        self.assertEqual(code.count('"delete", "pod"'), 1)


class PreconditionsTest(unittest.TestCase):
    def refus(self, **over):
        return rr.preconditions(facts(**over))

    def test_nominal_aucun_refus(self):
        self.assertEqual(self.refus(), [])

    def test_chaque_precondition_refuse(self):
        cases = {
            "primaire": dict(cluster={"spec": {"instances": 2}, "status": {"currentPrimary": "postgres-5", "targetPrimary": "postgres-5", "readyInstances": 1}}),
            "bascule": dict(cluster={"spec": {"instances": 2}, "status": {"currentPrimary": "postgres-7", "targetPrimary": "postgres-5", "readyInstances": 1}}),
            "2/2 déjà": dict(cluster={"spec": {"instances": 2}, "status": {"currentPrimary": "postgres-7", "targetPrimary": "postgres-7", "readyInstances": 2}}),
            "primaire pas prêt": dict(primary_ready=False),
            "pod absent": dict(pod=None),
            "pvc absent": dict(pvc=None),
            "PV Delete": dict(pv={"spec": {"persistentVolumeReclaimPolicy": "Delete"}}),
            "aucun nœud": dict(eligible_nodes=[]),
            "espace": dict(free_gb=1),
            "espace inconnu": dict(free_gb=None),
            "écritures non prouvées": dict(no_unique_writes=None, no_unique_writes_detail="segment absent"),
            "écritures présentes": dict(no_unique_writes=False),
            "sauvegarde": dict(backup_problems=["sauvegarde A : trop vieille"]),
            "démarrage VM": dict(vm_start_in_progress=True),
            "autre incident": dict(open_vm_incidents=["k8s-nodes/k8s-worker2", "infra/k8s-lb2"]),
        }
        for name, over in cases.items():
            self.assertTrue(self.refus(**over), name)

    def test_pvc_deja_modifie_ou_pod_pret_ou_primaire_refuse(self):
        pvc = copy.deepcopy(PVC)
        pvc["metadata"]["annotations"]["cnpg.io/pvcStatus"] = "detached"
        self.assertTrue(self.refus(pvc=pvc))
        pvc = copy.deepcopy(PVC)
        pvc["metadata"]["ownerReferences"] = []
        self.assertTrue(self.refus(pvc=pvc))
        pod = copy.deepcopy(POD)
        pod["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
        self.assertTrue(self.refus(pod=pod))
        pod = copy.deepcopy(POD)
        pod["metadata"]["labels"]["cnpg.io/instanceRole"] = "primary"
        self.assertTrue(self.refus(pod=pod))

    def test_cluster_absent(self):
        self.assertEqual(rr.preconditions({"instance": "postgres-5", "cluster": None}), ["Cluster introuvable"])


class PuresTest(unittest.TestCase):
    def test_nom_du_segment_de_la_fourche(self):
        self.assertEqual(rr.wal_segment_name(117, "57/86018340"), "000000750000005700000086")
        self.assertEqual(rr.wal_segment_name(118, "57/87000028"), "000000760000005700000087")
        self.assertEqual(rr.wal_segment_name(1, "0/36001190"), "000000010000000000000036")

    def test_fourche_lue_dans_le_fichier_history(self):
        txt = "115\t55/640000A0\tno recovery target specified\n\n116\t55/840C3EB8\tno recovery target specified\n\n117\t57/86018340\tno recovery target specified\n"
        self.assertEqual(rr.fork_info(txt), (117, "57/86018340"))
        self.assertIsNone(rr.fork_info("rien\n"))

    def test_le_fichier_history_est_cherche_avec_la_timeline_en_hexadecimal(self):
        # Timeline 118 -> 00000076.history. Défaut du 6 nov. 2026 : la version décimale cherchait 00000118.history (inexistant) -> refus à tort.
        self.assertIn("to_hex(timeline_id)", rr.HISTORY_SQL)
        self.assertIn("upper(lpad(", rr.HISTORY_SQL)
        self.assertIn("pg_wal/", rr.HISTORY_SQL)
        self.assertEqual("%08X" % 118, "00000076")
        self.assertNotIn("to_char", rr.HISTORY_SQL)

    def test_comptage_des_enregistrements_par_gestionnaire(self):
        out = ("rmgr: XLOG        len (rec/tot):    114/   114, tx:          0, lsn: 57/86018340, prev 57/86018310, desc: CHECKPOINT_SHUTDOWN\n"
               "rmgr: XLOG        len (rec/tot):     24/    24, tx:          0, lsn: 57/860183B8, prev 57/86018340, desc: SWITCH\n")
        self.assertEqual(dict(rr.rmgr_counts(out)), {"XLOG": 2})
        self.assertEqual(dict(rr.rmgr_counts(out + "rmgr: Transaction len: 34, desc: COMMIT 2026-11-06\nrmgr: Heap len: 10, desc: INSERT\n")),
                         {"XLOG": 2, "Transaction": 1, "Heap": 1})

    def test_noeuds_eligibles_taint_anti_affinite_et_cordon(self):
        def node(n, ready=True, taints=None, cordon=False):
            return {"metadata": {"name": n}, "spec": {"taints": taints or [], "unschedulable": cordon},
                    "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}

        def pod(n, node_name, cluster="postgres"):
            return {"metadata": {"name": n, "labels": {"cnpg.io/cluster": cluster}}, "spec": {"nodeName": node_name}}
        nodes = [node("k8s-master", taints=[{"key": "node-role.kubernetes.io/control-plane", "effect": "NoSchedule"}]), node("k8s-worker1"), node("k8s-worker2")]
        pods = [pod("postgres-7", "k8s-worker1"), pod("postgres-5", "k8s-worker2"), pod("autre", "k8s-master", cluster="x")]
        self.assertEqual(rr.eligible_nodes(nodes, pods, "postgres-5"), ["k8s-worker2"])        # .158 occupé, .153 tainté
        self.assertEqual(rr.eligible_nodes(nodes, pods, "postgres-9"), [])                      # postgres-5 occupe .155 : plus aucun nœud
        nodes[2] = node("k8s-worker2", cordon=True)
        self.assertEqual(rr.eligible_nodes(nodes, pods, "postgres-5"), [])

    def test_correctif_json_ne_retire_que_l_owner_reference_du_cluster_avec_test_prealable(self):
        pvc = copy.deepcopy(PVC)
        pvc["metadata"]["ownerReferences"].insert(0, {"kind": "Autre", "name": "x"})
        patch = rr.owner_ref_removal_patch(pvc)
        self.assertEqual(patch[-1], {"op": "remove", "path": "/metadata/ownerReferences/1"})
        self.assertEqual([p["op"] for p in patch], ["test", "test", "remove"])

    def test_sauvegardes_il_faut_un_dump_recent_ET_une_preuve_de_restauration_de_CE_fichier(self):
        now = 1_800_000_000
        dumps = {"A": {"name": "a1", "mtime": now - 600, "sha256": "s1"}, "B": {"name": "b1", "mtime": now - 900, "sha256": "s2"}}
        rep = [{"source": "A", "file": "a1", "sha256": "s1", "verdict": "RESTAURABLE"}, {"source": "B", "file": "b1", "sha256": "s2", "verdict": "RESTAURABLE"}]
        self.assertEqual(rr.reports_prove_dumps(rep, dumps, now, 60), [])
        self.assertTrue(rr.reports_prove_dumps(rep[:1], dumps, now, 60))                       # B sans preuve
        self.assertTrue(rr.reports_prove_dumps([dict(rep[0], sha256="autre"), rep[1]], dumps, now, 60))   # preuve d'un autre fichier
        self.assertTrue(rr.reports_prove_dumps([dict(rep[0], verdict="ÉCHEC"), rep[1]], dumps, now, 60))
        self.assertTrue(rr.reports_prove_dumps(rep, {"A": dumps["A"], "B": dict(dumps["B"], mtime=now - 7200)}, now, 60))   # B trop ancienne
        self.assertTrue(rr.reports_prove_dumps(rep, {"A": dumps["A"]}, now, 60))               # B absente


if __name__ == "__main__":
    unittest.main()
