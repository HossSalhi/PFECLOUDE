#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch local et fail-closed du preflight DISK HELIOS (.158).

Usage depuis la racine du depot : python -B .\\patch_helios_disk_orange_v2.py --apply
Le script ne fait aucune connexion reseau, aucun kubectl, aucun SSH et aucun appel systemd.
Il modifie uniquement les sources/tests locaux et le SHA256SUMS de diskfill.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import sys


ROOT = Path.cwd()
FILES = {
    "e2e": Path("scripts/helios-ops/helios_disk_e2e.py"),
    "e2e_test": Path("scripts/helios-ops/test_helios_disk_e2e.py"),
    "agent": Path("scripts/helios-ops/helios_disk_host_agent.py"),
    "agent_test": Path("scripts/helios-ops/test_helios_disk_host_agent.py"),
    "diskfill": Path("infra/helios-sot/diskfill/diskfill.py"),
    "diskfill_test": Path("scripts/helios-ops/test_helios_diskfill_service.py"),
    "manifest": Path("infra/helios-sot/diskfill/SHA256SUMS"),
}


def fail(message: str) -> None:
    raise RuntimeError(message)


def read_sources() -> dict[str, str]:
    result: dict[str, str] = {}
    for key, rel in FILES.items():
        path = ROOT / rel
        if not path.is_file():
            fail(f"Fichier absent : {rel}")
        result[key] = path.read_text(encoding="utf-8-sig")
    return result


def replace_once(sources: dict[str, str], key: str, old: str, new: str, label: str) -> None:
    text = sources[key]
    count = text.count(old)
    if count != 1:
        fail(f"Ancrage non unique pour {label} dans {FILES[key]} (trouve {count}, attendu 1). Aucune ecriture effectuee.")
    sources[key] = text.replace(old, new, 1)


def insert_before(sources: dict[str, str], key: str, marker: str, insertion: str, label: str) -> None:
    text = sources[key]
    count = text.count(marker)
    if count != 1:
        fail(f"Point d'insertion non unique pour {label} dans {FILES[key]} (trouve {count}, attendu 1). Aucune ecriture effectuee.")
    sources[key] = text.replace(marker, insertion + marker, 1)


def apply_e2e(s: dict[str, str]) -> None:
    replace_once(
        s, "e2e",
        'CODE_INCIDENTS_ANCIENS = "E2E_INCIDENTS_DISQUE_ANCIENS"',
        'CODE_INCIDENTS_ANCIENS = "E2E_INCIDENTS_DISQUE_ANCIENS"\n'
        'AGE_INCIDENT_DISQUE_ANCIEN_S = 48 * 3600\n'
        'NOEUDS_DISQUE_E2E = (NOEUD_K8S, "k8s-worker2")',
        "constantes de classification des incidents anciens",
    )

    helper = '''def noeud_incident_disque(app):
    """Normalise uniquement les noms connus du detecteur DISK ; aucune heuristique large."""
    valeur = str(app or "")
    for noeud in NOEUDS_DISQUE_E2E:
        if valeur in (noeud, noeud + "-DiskPressure"):
            return noeud
    return None


'''
    insert_before(s, "e2e", "def evaluer_e2e(", helper, "normalisation stricte app->noeud")

    old_classification = '''    ouverts = fe.get("incidents_disque_ouverts")
    if not isinstance(ouverts, (list, tuple)):
        add("ROUGE", "incidents disque ouverts illisibles", "refus (echec sur)", "E2E_INCIDENTS_ILLISIBLES")
    else:
        for inc in ouverts:
            app = str((inc or {}).get("app"))
            if app == "monitoring":
                add("INFO", "incident disque de fond sur monitoring (.160) tolere : la regle DETECTOR-PRIORITY donne la priorite au worker", "")
            elif app == NOEUD_K8S:
                add("ROUGE", "incident disque deja ouvert (#%s sur %s) : un incident concurrent fausserait le scenario" % (
                    (inc or {}).get("id"), app), "attendre sa fin", "E2E_INCIDENT_DISQUE_CONCURRENT")
            else:
                add("ORANGE", "incident disque ouvert (#%s sur %s) : etranger au scenario, ni touche ni correle, mais a connaitre" % (
                    (inc or {}).get("id"), app), "le lire avant d'acquitter", CODE_INCIDENTS_ANCIENS)
'''
    new_classification = '''    ouverts = fe.get("incidents_disque_ouverts")
    if not isinstance(ouverts, (list, tuple)):
        add("ROUGE", "incidents disque ouverts illisibles", "refus (echec sur)", "E2E_INCIDENTS_ILLISIBLES")
    else:
        diskpressure = fe.get("diskpressure")
        for inc in ouverts:
            if not isinstance(inc, dict):
                add("ROUGE", "ligne d'incident disque mal formee", "refus (echec sur)", "E2E_INCIDENTS_ILLISIBLES")
                continue
            app = str(inc.get("app") or "")
            if app == "monitoring":
                add("INFO", "incident disque de fond sur monitoring (.160) tolere : la regle DETECTOR-PRIORITY donne la priorite au worker", "")
                continue
            noeud = noeud_incident_disque(app)
            age = inc.get("age_s")
            etat = diskpressure.get(noeud) if isinstance(diskpressure, dict) and noeud else None
            ancien_confirme = (noeud in NOEUDS_DISQUE_E2E
                               and isinstance(age, int) and not isinstance(age, bool)
                               and age >= AGE_INCIDENT_DISQUE_ANCIEN_S
                               and etat == "False")
            if ancien_confirme:
                add("ORANGE", "incident disque historique #%s sur %s (age %dh, DiskPressure=False confirme par Kubernetes) : laisse ouvert, non modifie" % (
                    inc.get("id"), noeud, age // 3600),
                    "incident historique conserve ; acquittement limite au preflight DISK", CODE_INCIDENTS_ANCIENS)
            elif noeud == NOEUD_K8S:
                add("ROUGE", "incident disque concurrent #%s sur %s (age ou DiskPressure non confirme)" % (
                    inc.get("id"), app), "refus ; ne pas injecter tant que la condition n'est pas resolue et lue", "E2E_INCIDENT_DISQUE_CONCURRENT")
            else:
                add("ROUGE", "incident disque non historique, non classe ou etat DiskPressure illisible #%s sur %s" % (
                    inc.get("id"), app), "refus ferme ; verifier le noeud et son etat Kubernetes", "E2E_INCIDENT_DISQUE_NON_CLASSE")
'''
    replace_once(s, "e2e", old_classification, new_classification, "classification des incidents ouverts")

    old_sql = '''    sql = ("select 'AGE', extract(epoch from ((now() at time zone 'Africa/Tunis') - a.created_at))::bigint from incident_action a "
           "where a.type = '%s' and a.target_namespace = '%s' and a.target_name = '%s' order by a.created_at desc limit 20;\\n"
           "select 'OUVERT', i.id, i.app, i.status from incident i where i.incident_type in (%s) and i.status in (%s) "
           "order by i.id desc limit 50;" % (ACTION, NS, NOEUD_K8S, types, actifs))
'''
    new_sql = '''    sql = ("select 'AGE', extract(epoch from ((now() at time zone 'Africa/Tunis') - a.created_at))::bigint from incident_action a "
           "where a.type = '%s' and a.target_namespace = '%s' and a.target_name = '%s' order by a.created_at desc limit 20;\\n"
           "select 'OUVERT', i.id, i.app, i.status, extract(epoch from ((now() at time zone 'Africa/Tunis') - i.created_at))::bigint "
           "from incident i where i.incident_type in (%s) and i.status in (%s) order by i.id desc limit 50;" % (ACTION, NS, NOEUD_K8S, types, actifs))
'''
    replace_once(s, "e2e", old_sql, new_sql, "SELECT age des incidents ouverts")

    replace_once(
        s, "e2e",
        '''        elif l[0] == "OUVERT" and len(l) >= 4 and l[1].isdigit():
            ouverts.append({"id": int(l[1]), "app": l[2], "statut": l[3]})''',
        '''        elif l[0] == "OUVERT" and len(l) >= 5 and l[1].isdigit() and l[4].isdigit():
            ouverts.append({"id": int(l[1]), "app": l[2], "statut": l[3], "age_s": int(l[4])})''',
        "parsing de l'age des incidents ouverts",
    )

    diskpressure_reader = '''def lire_diskpressure(exe):
    """Lit les conditions DiskPressure des deux workers ; None si une lecture est incomplete ou ambigue."""
    resultats = {}
    for noeud in NOEUDS_DISQUE_E2E:
        r = exe.local(["kubectl", "get", "node", noeud, "-o", "json"], 30)
        if r.rc != 0:
            return None
        objet = _json(r.out)
        if not isinstance(objet, dict) or not isinstance(objet.get("metadata"), dict):
            return None
        if objet["metadata"].get("name") != noeud:
            return None
        statut = objet.get("status")
        conditions = statut.get("conditions") if isinstance(statut, dict) else None
        if not isinstance(conditions, list):
            return None
        trouvees = [x for x in conditions if isinstance(x, dict) and x.get("type") == "DiskPressure"]
        if len(trouvees) != 1 or trouvees[0].get("status") not in ("True", "False"):
            return None
        resultats[noeud] = trouvees[0]["status"]
    return resultats


'''
    insert_before(s, "e2e", "def lire_pages_s(", diskpressure_reader, "lecteur Kubernetes DiskPressure RO")

    replace_once(
        s, "e2e",
        '''    ages, ouverts = _lire(lire_incidents_e2e, exe, primaire) or (None, None)
    bridge = _lire(lire_bridge, exe)''',
        '''    ages, ouverts = _lire(lire_incidents_e2e, exe, primaire) or (None, None)
    incident_worker = (ouverts is not None and any(
        isinstance(x, dict) and x.get("app") != "monitoring" for x in ouverts))
    diskpressure = _lire(lire_diskpressure, exe) if incident_worker else ({} if ouverts is not None else None)
    bridge = _lire(lire_bridge, exe)''',
        "collecte condition DiskPressure uniquement si incidents worker",
    )
    replace_once(
        s, "e2e",
        '''        "incidents_disque_ouverts": ouverts,
        "hote": {"pages_s": _lire(lire_pages_s, exe)},''',
        '''        "incidents_disque_ouverts": ouverts,
        "diskpressure": diskpressure,
        "hote": {"pages_s": _lire(lire_pages_s, exe)},''',
        "ajout du fait DiskPressure au rapport E2E",
    )


def apply_e2e_tests(s: dict[str, str]) -> None:
    replace_once(
        s, "e2e_test",
        'ctx.fe.update(incidents_disque_ouverts=[{"app": "k8s-worker2", "id": 7}])               # ORANGE E2E_INCIDENTS_DISQUE_ANCIENS',
        'ctx.fe.update(incidents_disque_ouverts=[{"app": "k8s-worker2", "id": 7, "age_s": 50 * 3600}],\n'
        '                      diskpressure={"k8s-worker1": "False", "k8s-worker2": "False"})  # ORANGE historique verifie',
        "fixture incident historique avec DiskPressure confirme",
    )
    replace_once(
        s, "e2e_test",
        'ok = Psql(H.Res(0, "AGE|250\\nAGE|9000\\nOUVERT|7|k8s-worker2|OPEN\\nOUVERT|3858|monitoring|IN_PROGRESS\\n", ""))',
        'ok = Psql(H.Res(0, "AGE|250\\nAGE|9000\\nOUVERT|7|k8s-worker2|OPEN|180000\\nOUVERT|3858|monitoring|IN_PROGRESS|200000\\n", ""))',
        "fixture SQL avec age des incidents ouverts",
    )
    replace_once(
        s, "e2e_test",
        '''        self.assertEqual(([250, 9000], [{"id": 7, "app": "k8s-worker2", "statut": "OPEN"}, {"id": 3858, "app": "monitoring", "statut": "IN_PROGRESS"}]),
                         E.lire_incidents_e2e(ok, "postgres-8"))''',
        '''        self.assertEqual(([250, 9000], [{"id": 7, "app": "k8s-worker2", "statut": "OPEN", "age_s": 180000},
                                               {"id": 3858, "app": "monitoring", "statut": "IN_PROGRESS", "age_s": 200000}]),
                         E.lire_incidents_e2e(ok, "postgres-8"))''',
        "assertion parser age_s",
    )
    replace_once(
        s, "e2e_test",
        '''        self.assertIn("a.target_name = 'k8s-worker1'", sql)
        self.assertIn("Africa/Tunis", sql)''',
        '''        self.assertIn("a.target_name = 'k8s-worker1'", sql)
        self.assertIn("i.created_at", sql)
        self.assertIn("Africa/Tunis", sql)''',
        "assertion SQL age incident",
    )
    # L'assertion a varie legerement en indentation selon les versions du fichier.
    # On remplace seulement le fragment de dictionnaire stable, sans toucher au reste.
    motif = re.compile(r'("incidents_disque_ouverts"\s*:\s*None)(\s*,\s*"hote"\s*:\s*\{\s*"pages_s"\s*:\s*None\s*\})')
    texte = s["e2e_test"]
    texte_nouveau, nombre = motif.subn(r'\1, "diskpressure": None\2', texte)
    if nombre != 1:
        fail(f"Ancrage non unique pour assertion collecte illisible avec diskpressure dans {FILES['e2e_test']} (trouve {nombre}, attendu 1). Aucune ecriture effectuee.")
    s["e2e_test"] = texte_nouveau
    replace_once(
        s, "e2e_test",
        'noms = {"lire_configmap", "lire_backend", "lire_bridge", "lire_systemd", "lire_incidents_e2e", "lire_pages_s", "lire_sonde_bridge"}',
        'noms = {"lire_configmap", "lire_backend", "lire_bridge", "lire_systemd", "lire_incidents_e2e", "lire_diskpressure", "lire_pages_s", "lire_sonde_bridge"}',
        "audit statique du nouveau lecteur RO",
    )

    monitoring_anchor = '''    def test_un_incident_de_fond_sur_monitoring_est_tolere(self):
        r = self.evaluer(self._avec(incidents_disque_ouverts=[{"app": "monitoring", "id": 3858}]))
        self.assertTrue(r["go"], r["raisons"])
        self.assertTrue(any(x[0] == "INFO" and "monitoring" in x[1] for x in r["raisons"]))

'''
    new_test = '''    def test_incident_historique_worker1_suffixe_exige_age_et_diskpressure_false(self):
        incident = {"app": "k8s-worker1-DiskPressure", "id": 1344, "age_s": 50 * 3600}
        fe = {"incidents_disque_ouverts": [incident],
              "diskpressure": {"k8s-worker1": "False", "k8s-worker2": "False"}}
        r = self.evaluer(self._avec(**fe))
        self.assertEqual("ORANGE", r["verdict"], r["raisons"])
        self.assertTrue(any(x[0] == "ORANGE" and x[3] == "E2E_INCIDENTS_DISQUE_ANCIENS" for x in r["raisons"]))

        fe["diskpressure"]["k8s-worker1"] = "True"
        r = self.evaluer(self._avec(**fe))
        self.assertEqual("ROUGE", r["verdict"], r["raisons"])

'''
    replace_once(s, "e2e_test", monitoring_anchor, monitoring_anchor + new_test, "test incident historique worker1-DiskPressure")


def apply_agent(s: dict[str, str]) -> None:
    helper = '''CODES_ORANGE_AUTORISES = frozenset(("GEL_HOTE_48H", E.CODE_INCIDENTS_ANCIENS))


def _preflight_autorisable(verdict, go, raisons):
    if go is not True or not isinstance(raisons, list) or not all(isinstance(x, dict) for x in raisons):
        return False
    niveaux = [x.get("level") for x in raisons]
    if any(niveau not in ("INFO", "ORANGE") for niveau in niveaux):
        return False
    oranges = [x for x in raisons if x.get("level") == "ORANGE"]
    if verdict == "VERT":
        return not oranges and all(niveau == "INFO" for niveau in niveaux)
    if verdict == "ORANGE":
        return bool(oranges) and all(x.get("code") in CODES_ORANGE_AUTORISES for x in oranges)
    return False


'''
    insert_before(s, "agent", "def calculer_preflight():", helper, "allowlist orange agent + validation")
    replace_once(
        s, "agent",
        '        acquittes=(),  # aucun ORANGE n\'est accepte automatiquement',
        '        acquittes=("GEL_HOTE_48H", E.CODE_INCIDENTS_ANCIENS),  # deux ORANGE historiques strictement autorises',
        "acquittement limite aux deux codes historiques",
    )
    old = '''                # Une raison ROUGE ou ORANGE interdit le depart. Aucun acquittement distant n'est accepte.
                raisons = resultat.get("reasons")
                if not isinstance(raisons, list) or not all(isinstance(x, dict) for x in raisons):
                    raise ValueError("raisons de pre-vol illisibles")
                verdict = resultat.get("verdict")
                go = resultat.get("go") is True and verdict == "VERT" and all(x.get("level") == "INFO" for x in raisons)
                if not go and verdict == "VERT":
                    verdict = "ROUGE"'''
    new = '''                # Seuls deux ORANGE connus peuvent etre autorises localement ; aucun acquittement HTTP distant.
                raisons = resultat.get("reasons")
                if not isinstance(raisons, list) or not all(isinstance(x, dict) for x in raisons):
                    raise ValueError("raisons de pre-vol illisibles")
                verdict = resultat.get("verdict")
                go = _preflight_autorisable(verdict, resultat.get("go"), raisons)
                if any(x.get("level") == "ROUGE" for x in raisons):
                    verdict, go = "ROUGE", False
                elif verdict == "VERT" and not go:
                    verdict = "ROUGE"
                elif verdict == "ORANGE" and resultat.get("go") is True and not go:
                    verdict = "ROUGE"
                elif verdict not in ("VERT", "ORANGE", "ROUGE"):
                    verdict, go = "ROUGE", False'''
    replace_once(s, "agent", old, new, "politique HTTP de l'agent Windows")


def apply_agent_tests(s: dict[str, str]) -> None:
    marker = '''    def test_signature_invalide_est_rejetee(self):'''
    new_tests = '''    def test_orange_autorise_est_retourne_signe_et_autorisant(self):
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

'''
    insert_before(s, "agent_test", marker, new_tests, "tests HTTP ORANGE autorise et refuse")


def apply_diskfill(s: dict[str, str]) -> None:
    helper = '''AGENT_HOTE_CODES_ORANGE_AUTORISES = frozenset(("GEL_HOTE_48H", "E2E_INCIDENTS_DISQUE_ANCIENS"))


def agent_hote_preflight_autorisable(verdict, go, raisons):
    """Politique locale identique a l'agent : ORANGE uniquement sur allowlist et sans aucun ROUGE."""
    if go is not True or not isinstance(raisons, list) or not all(isinstance(x, dict) for x in raisons):
        return False
    niveaux = [x.get("level") for x in raisons]
    if any(niveau not in ("INFO", "ORANGE") for niveau in niveaux):
        return False
    oranges = [x for x in raisons if x.get("level") == "ORANGE"]
    if verdict == "VERT":
        return not oranges and all(niveau == "INFO" for niveau in niveaux)
    if verdict == "ORANGE":
        return bool(oranges) and all(x.get("code") in AGENT_HOTE_CODES_ORANGE_AUTORISES for x in oranges)
    return False


'''
    insert_before(s, "diskfill", "CONFIG_KUBELET =", helper, "allowlist orange defensive Linux")

    old_preflight = '''        if objet.get("verdict") != "VERT" or objet.get("go") is not True:
            details = ["%s:%s" % (str(x.get("level", "?")), str(x.get("code", "?"))) for x in raisons
                       if x.get("level") in ("ROUGE", "ORANGE")]
            raise RefusSecurite("pre-vol Windows refuse (verdict=%s, go=%r%s)" % (
                objet.get("verdict"), objet.get("go"), "; " + ", ".join(details) if details else ""))
        if any(x.get("level") != "INFO" for x in raisons):
            raise RefusSecurite("pre-vol Windows VERT incoherent : niveau non-INFO dans les raisons")
        return objet'''
    new_preflight = '''        verdict = objet.get("verdict")
        go = objet.get("go")
        if not agent_hote_preflight_autorisable(verdict, go, raisons):
            details = ["%s:%s" % (str(x.get("level", "?")), str(x.get("code", "?"))) for x in raisons
                       if x.get("level") in ("ROUGE", "ORANGE")]
            raise RefusSecurite("pre-vol Windows refuse (verdict=%s, go=%r%s)" % (
                verdict, go, "; " + ", ".join(details) if details else ""))
        return objet'''
    replace_once(s, "diskfill", old_preflight, new_preflight, "verification attestation HMAC Windows")

    old_strict = '''        strict = (att.get("verdict") == "VERT" and att.get("go") is True and isinstance(raisons, list)
                  and all(isinstance(x, dict) and x.get("level") == "INFO" for x in raisons))
        return [(strict, "agent Windows DISK authentifie, attestation fraiche : VERT strict (aucun ORANGE/ROUGE ; RAM, VMware/VMDK, C:, cluster, CNPG, Backend, Bridge, incidents)")]'''
    new_strict = '''        autorise = agent_hote_preflight_autorisable(att.get("verdict"), att.get("go"), raisons)
        return [(autorise, "agent Windows DISK authentifie, attestation fraiche : VERT ou ORANGE explicitement autorise (aucun ROUGE ; ORANGE limites a GEL_HOTE_48H et E2E_INCIDENTS_DISQUE_ANCIENS)")]'''
    replace_once(s, "diskfill", old_strict, new_strict, "double controle du preflight dans verifier")


def apply_diskfill_tests(s: dict[str, str]) -> None:
    marker = '''    def test_agent_windows_non_vert_refuse_avant_creation_etat_ou_cooldown(self):'''
    tests = '''    def test_agent_windows_orange_autorise_est_accepte_sans_armement(self):
        m = monde_pret()
        m.preflight_hote_resultat = {"verdict": "ORANGE", "go": True,
                                     "reasons": [{"level": "ORANGE", "code": "GEL_HOTE_48H"}]}
        out = []
        self.assertEqual(D.verifier(m, False, out.append), 0, "\\n".join(out))
        self.assertIn("HOTE_WINDOWS", "\\n".join(out))
        self.assertNotIn(D.F_ETAT, m.textes, "la verification ne cree aucun etat")
        self.assertEqual(m.appels_de("allouer"), [], "la verification n'alloue jamais")
        self.assertEqual(m.appels_de("liberer"), [], "la verification ne supprime jamais")

    def test_agent_windows_orange_non_autorise_est_refuse(self):
        m = monde_pret()
        m.preflight_hote_resultat = {"verdict": "ORANGE", "go": True,
                                     "reasons": [{"level": "ORANGE", "code": "NOTREADY_2H"}]}
        out = []
        self.assertEqual(D.verifier(m, False, out.append), 2, "\\n".join(out))
        self.assertIn("HOTE_WINDOWS", "\\n".join(out))
        self.assertNotIn(D.F_ETAT, m.textes)

'''
    insert_before(s, "diskfill_test", marker, tests, "tests service ORANGE autorise/refuse")


def update_manifest(s: dict[str, str]) -> None:
    digest = hashlib.sha256(s["diskfill"].encode("utf-8")).hexdigest()
    lines = s["manifest"].splitlines()
    matches = [i for i, line in enumerate(lines) if re.fullmatch(r"[0-9a-f]{64}\s+diskfill\.py", line.strip())]
    if len(matches) != 1:
        fail(f"SHA256SUMS : ligne diskfill.py introuvable ou non unique ({len(matches)}). Aucune ecriture effectuee.")
    lines[matches[0]] = f"{digest}  diskfill.py"
    s["manifest"] = "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="appliquer les modifications locales apres validation de tous les ancrages")
    args = parser.parse_args()

    if not (ROOT / ".git").exists() and not (ROOT / "scripts/helios-ops").is_dir():
        fail("Lance ce script depuis la racine du depot PFE-Orange.")

    sources = read_sources()
    original_bytes = {key: (ROOT / rel).read_bytes() for key, rel in FILES.items()}
    original_text = {
        key: data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        for key, data in original_bytes.items()
    }
    apply_e2e(sources)
    apply_e2e_tests(sources)
    apply_agent(sources)
    apply_agent_tests(sources)
    apply_diskfill(sources)
    apply_diskfill_tests(sources)
    update_manifest(sources)

    # Validation syntaxique en memoire avant la moindre ecriture.
    for key in ("e2e", "e2e_test", "agent", "agent_test", "diskfill", "diskfill_test"):
        try:
            compile(sources[key], str(FILES[key]), "exec")
        except SyntaxError as exc:
            fail(f"Erreur syntaxique avant ecriture dans {FILES[key]} : {exc}")

    if not args.apply:
        print("PRE-CHECK OK : tous les ancrages sont uniques, les sources modifiees sont syntaxiquement valides et le manifeste est calculable.")
        print("AUCUNE MODIFICATION effectuee.")
        print("Pour appliquer : python -B .\\patch_helios_disk_orange_v2.py --apply")
        return 0

    changed = [key for key in FILES if sources[key] != original_text[key]]
    if not changed:
        print("AUCUNE MODIFICATION NECESSAIRE : les fichiers correspondent deja au patch.")
        return 0

    backup_dir = Path(tempfile.mkdtemp(prefix="helios-disk-orange-backup-"))
    for key in changed:
        rel = FILES[key]
        p = ROOT / rel
        dest = backup_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)

    written: list[str] = []
    try:
        for key in changed:
            rel = FILES[key]
            p = ROOT / rel
            raw = original_bytes[key]
            newline = "\r\n" if b"\r\n" in raw else "\n"
            rendered = sources[key].replace("\n", newline)
            if raw.startswith(b"\xef\xbb\xbf"):
                rendered = "\ufeff" + rendered
            tmp = p.with_suffix(p.suffix + ".helios-tmp")
            tmp.write_bytes(rendered.encode("utf-8"))
            os.replace(tmp, p)
            written.append(key)
    except Exception:
        for key in written:
            (ROOT / FILES[key]).write_bytes(original_bytes[key])
        raise

    print("PATCH LOCAL APPLIQUE.")
    print(f"Sauvegardes originales : {backup_dir}")
    print("Fichiers modifies :")
    for key in changed:
        print(f"  - {FILES[key]}")
    print("Aucune VM, aucun service, aucun fichier de configuration systemd et aucun deploiement n'a ete touche.")
    print("Etape suivante : lancer les tests unitaires cibles ; ne pas demarrer helios-diskfill.service avant leur validation et le deploiement controle de diskfill.py sur .158.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        raise SystemExit(2)
