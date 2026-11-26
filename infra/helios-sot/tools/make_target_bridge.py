# -*- coding: utf-8 -*-
"""Genere le Bridge CIBLE (infra/helios-sot/bridge/bridge.py) a partir de la baseline de production.

Modifications (VMDOWN-POLICY-20261105), toutes TRACEES par le marqueur VMDOWN-POLICY-20261105 :
  1. le Bridge n'a plus AUCUN pouvoir de mise sous tension : suppression de JETON_WATCHDOG / WATCHDOG / rallumer() ;
  2. catalogue() : RESTART_VM / RESTART_NODE_VM / VM_DOWN -> sentinelle "__poweron_interdit__" (refus explicite) ;
  3. NODE_NOT_READY n'est plus mappe a RESTART_NODE_VM mais a ESCALATE_NODE_NOT_READY (escalade, aucune commande) ;
  4. /notifier : filtre MONITORING_NON_FIABLE et les SAFE_STOP des types VM (le backend porte ces notifications).

Modification (DISK-E2E-158-01), PUREMENT ADDITIVE, tracee par le marqueur DISK-E2E-158-01 :
  5. capacite HOST_RECLAIM_DISK du scenario DISK_E2E_158 : suppression, sur le seul noeud worker1 (.158), des seuls fichiers
     helios-diskfill-<jeton>-<NNN>.bin du repertoire exclusif /var/lib/helios-diskfill, puis MESURE de l'occupation reelle de /.
     ACTION_PAR_TYPE_MESURE, catalogue(), les capacites CPU et la politique VM_DOWN ne sont PAS modifiees.
"""
import os
import shutil
import sys

base_dir, out_dir = sys.argv[1], sys.argv[2]
src = open(os.path.join(base_dir, "bridge.py"), encoding="utf-8").read()
MARK = "VMDOWN-POLICY-20261105"


def replace_once(text, old, new, label):
    assert text.count(old) == 1, "%s : motif %d fois" % (label, text.count(old))
    return text.replace(old, new)


# 1) constantes
src = replace_once(src, '''# LOT 3BG : jeton partage avec l adaptateur hyperviseur. Sans lui, le watchdog
# refuse desormais tout POST /restart : le Bridge devient prouvablement le seul
# appelant autorise, et non plus seulement le seul appelant de fait.
JETON_WATCHDOG = os.environ.get("HELIOS_WATCHDOG_TOKEN", "")
''', '''# %s : le Bridge n'a plus AUCUNE capacite de mise sous tension. Le jeton du watchdog hote
# (HELIOS_WATCHDOG_TOKEN) n'est plus lu ici : le BACKEND est le seul appelant autorise de POST /restart.
''' % MARK, "jeton watchdog")
src = replace_once(src, '''WATCHDOG = os.environ.get("HELIOS_WATCHDOG", "http://192.168.148.1:9099")
''', '''# %s : plus d'URL de watchdog hote dans le Bridge (aucun appel sortant vers l'hyperviseur).
''' % MARK, "url watchdog")

# 2) mapping de type
src = replace_once(src, '''    "NODE_NOT_READY":           "RESTART_NODE_VM",
''', '''    # %s : un nœud NotReady n'est plus redemarre par le Bridge (escalade, aucune commande).
    "NODE_NOT_READY":           "ESCALATE_NODE_NOT_READY",
''' % MARK, "mapping NODE_NOT_READY")

# 3) catalogue
src = replace_once(src, '''    # rallumage d'une VM eteinte : hors SSH, seul l'hyperviseur peut le faire
    if action in ("RESTART_VM", "RESTART_NODE_VM", "VM_DOWN"):
        return "__watchdog__", noeud
''', '''    # %s : rallumage d'une VM = INTERDIT au Bridge (un seul acteur : le backend, via le watchdog hote).
    if action in ("RESTART_VM", "RESTART_NODE_VM", "VM_DOWN"):
        return "__poweron_interdit__", noeud
''' % MARK, "catalogue")

# 4) rallumer() supprimee
start = src.index("def rallumer(noeud):")
end = src.index("def envoyer_email(sujet, corps):")
src = src[:start] + '''# %s : rallumer() SUPPRIMEE. Le Bridge ne contacte jamais le watchdog hote (:9099).


''' % MARK + src[end:]

# 5) handler /action
src = replace_once(src, '''            if h == "__watchdog__":
                r = rallumer(cmd)
            elif cmd.startswith("kubectl"):''', '''            if h == "__poweron_interdit__":
                # %s : refus explicite, aucune commande, aucun appel reseau.
                tracer("POWERON INTERDIT", action, "-> refuse (seul le backend redemarre une VM)")
                return self.repondre(200, {"action": action, "execute": False, "ok": False,
                                           "refuse": "POWER_ON_INTERDIT", "exitCode": -1, "stdout": "",
                                           "stderr": "mise sous tension interdite au Bridge : "
                                                     "le redemarrage d'une VM releve du backend (watchdog hote)"})
            if cmd.startswith("kubectl"):''' % MARK, "handler action")

# 6) /notifier : filtre
src = replace_once(src, '''            if not corps:
                return self.repondre(400, {"erreur": "corps vide"})
            res = envoyer_notification(sujet, corps)''', '''            if not corps:
                return self.repondre(400, {"erreur": "corps vide"})
            motif_supprime = notification_supprimee(sujet, corps)
            if motif_supprime:
                # %s : detection seule / notification portee par le backend. On repond envoye=true
                # (voie explicite) pour ne declencher AUCUNE reprise ni escalade cote Camunda.
                tracer("NOTIF", sujet[:60], "-> SUPPRIMEE (%%s)" %% motif_supprime)
                return self.repondre(200, {"envoye": True, "voie": "SUPPRIMEE_DETECTION_SEULE",
                                           "supprimee": True, "motif": motif_supprime})
            res = envoyer_notification(sujet, corps)''' % MARK, "notifier")

src = replace_once(src, "def envoyer_email(sujet, corps):", '''# %s : notifications VM supprimees au Bridge. Le backend (incidents VM_DOWN, historique, e-mail pour les VM AUTO,
# interface seule pour .160) est desormais le SEUL porteur des notifications de la famille VM.
_RE_SAFE_STOP_VM = re.compile(
    r"SAFE_STOP\\s+(NODE_NOT_READY|TARGET_DOWN|VM_DOWN|VM_UNREACHABLE|VM_POWERED_OFF)\\s+sur\\s+(\\S+)", re.IGNORECASE)
_CIBLES_VM_POLITIQUE = frozenset({
    "k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-monitoring", "k8s-master",
    "worker1", "worker2", "lb2", "monitoring", "master"})
_RE_MONITORING_NON_FIABLE = re.compile(r"MONITORING_NON_FIABLE|monitoringFiable\\s*[=:]\\s*false", re.IGNORECASE)


def notification_supprimee(sujet, corps):
    """Motif de suppression d'un e-mail deja redige par Camunda, ou None (e-mail a envoyer)."""
    if _RE_MONITORING_NON_FIABLE.search("%%s\\n%%s" %% (sujet, corps)):
        return "MONITORING_NON_FIABLE"
    m = _RE_SAFE_STOP_VM.search(sujet or "")
    if m and m.group(2).strip().lower() in _CIBLES_VM_POLITIQUE:
        return "SAFE_STOP_VM:%%s" %% m.group(1).upper()
    return None


def envoyer_email(sujet, corps):''' % MARK, "fonction notification_supprimee")

# =====================================================================================================================
# 5) DISK-E2E-158-01 : capacite HOST_RECLAIM_DISK (scenario DISK_E2E_158). AJOUT PUR : aucune ligne existante n'est supprimee,
#    sauf l'extension d'une condition (`if _quota_demande or _cpuload_demande:` -> `... or _disque_demande:`).
#    Les blocs ci-dessous sont des chaines BRUTES inserees telles quelles (aucun formatage %).
# =====================================================================================================================
MARK_DISK = "DISK-E2E-158-01"

BLOC_DISQUE = r'''
# ============================================================================
# DISK-E2E-158-01 - CAPACITE D EXECUTION : HOST_RECLAIM_DISK (scenario DISK_E2E_158)
# ----------------------------------------------------------------------------
# CE QUE CE BLOC AJOUTE : la capacite de LIBERER, sur UN SEUL noeud (.158 = worker1), les
# fichiers de remplissage d UN scenario de test identifie par son JETON, puis de MESURER
# l occupation REELLE de / avant de rendre un verdict.
#
# CE QU IL N AJOUTE PAS : aucune regle « NODE_DISK_FULL implique cette action ».
# ACTION_PAR_TYPE_MESURE est INCHANGEE ; catalogue() est INCHANGE ; le Bridge n execute cette
# action que si l amont (le backend) la demande NOMMEMENT, avec le nom du scenario.
#
# GARANTIES (chacune est verrouillee par test_bridge_disk_reclaim.py) :
#   - une seule cible (worker1), un seul type (NODE_DISK_FULL), un seul scenario (DISK_E2E_158) ;
#   - les SEULS fichiers supprimables sont ceux du repertoire exclusif /var/lib/helios-diskfill
#     (racine, 0700) dont le nom est EXACTEMENT helios-diskfill-<jeton 32 hex>-<NNN>.bin, fichier
#     regulier, proprietaire root, un seul lien, UN SEUL jeton present ;
#   - suppression par `rm -f --` sur des chemins EXACTS : aucun joker, aucun -r, aucun find -delete ;
#   - toute anomalie (repertoire non conforme, deux jetons, homonyme non regulier, liste suspecte)
#     = REFUS : rien n est touche ;
#   - IDEMPOTENT : sans fichier a supprimer, rien n est supprime ; ce n est un succes que si
#     l occupation mesuree est DEJA <= 77 % ;
#   - JAMAIS de succes sans mesure : ok = (aucun fichier du jeton restant) ET (occupation reelle
#     de / <= 77 % apres liberation) ; le code retour de `rm` n est PAS une preuve ;
#   - un incident dont un premier jeton a ete lie ne peut pas faire supprimer un AUTRE jeton ;
#   - le minuteur d expiration et la garde du scenario ne sont PAS touches (ils appartiennent a
#     l outil de lancement, qui les desarme lui-meme apres verification).
# ============================================================================

ACTION_DISQUE = "HOST_RECLAIM_DISK"
ACTIONS_DISQUE = (ACTION_DISQUE,)
DISQUE_SCENARIO = "DISK_E2E_158"
DISQUE_NOEUD = "worker1"                      # .158 : l UNIQUE noeud autorise
DISQUE_CIBLE_K8S = "k8s-worker1"
DISQUE_TYPE = "NODE_DISK_FULL"
DISQUE_NS = "k8s-nodes"
DISQUE_DOSSIER = "/var/lib/helios-diskfill"   # repertoire EXCLUSIF du scenario (racine, 0700)
# Motifs SANS ancres : ils ne servent QU AVEC fullmatch() (un `$` accepterait un saut de ligne final).
DISQUE_RE_FICHIER = re.compile(r"helios-diskfill-([0-9a-f]{32})-([0-9]{3})\.bin")
DISQUE_RE_JETON = re.compile(r"[0-9a-f]{32}")
DISQUE_RE_INCIDENT = re.compile(r"[0-9]{1,12}")
DISQUE_MAX_FICHIERS = 16
DISQUE_MAX_TOTAL = 6 * 1024 * 1024 * 1024     # jamais plus de 6 Gio a liberer
DISQUE_RECUPERATION_PCT = 77.0                # recupere <=> occupation de / <= 77 %
DISQUE_LISTE_MAX = 3900                       # executer() tronque stdout a 4000 : au-dela, la liste est suspecte
DISQUE_LIAISONS_MAX = 64
_DISQUE_LIAISONS = {}                         # incident_id -> jeton lie au premier passage
_DISQUE_VERROU = threading.RLock()


def disque_usage_pct(total_blocs, dispo_blocs):
    """Meme formule que la detection : (1 - disponible / taille) * 100, sur f_bavail / f_blocks."""
    if total_blocs <= 0 or dispo_blocs < 0 or dispo_blocs > total_blocs:
        return None
    return (1.0 - float(dispo_blocs) / float(total_blocs)) * 100.0


def disque_valider_requete(d, noeud):
    """(ok, rejet, motif). AUCUNE commande n est lancee ici."""
    action = str(d.get("action", "")).strip().upper()
    typ = str(d.get("type", "") or d.get("incident_type", "")).strip().upper()
    ns = str(d.get("ns", "")).strip()
    cible = str(d.get("cible", "")).strip()
    scenario = str(d.get("scenario", "")).strip()
    incident = str(d.get("incident_id", "") or "").strip()
    if action != ACTION_DISQUE:
        return False, "REJECTED_ACTION", "action %r non prise en charge" % action[:40]
    if noeud != DISQUE_NOEUD or cible != DISQUE_CIBLE_K8S:
        return False, "REJECTED_TARGET_NOT_ALLOWLISTED", "cible %r / noeud %r hors allowlist (%s)" % (
            cible[:40], str(noeud)[:40], DISQUE_NOEUD)
    if typ != DISQUE_TYPE:
        return False, "REJECTED_TYPE", "type d incident %r non autorise (%s)" % (typ[:40], DISQUE_TYPE)
    if ns != DISQUE_NS:
        return False, "REJECTED_NAMESPACE", "namespace %r non autorise (%s)" % (ns[:40], DISQUE_NS)
    if scenario != DISQUE_SCENARIO:
        return False, "REJECTED_SCENARIO", "scenario %r inconnu (%s exige)" % (scenario[:40], DISQUE_SCENARIO)
    if incident and not DISQUE_RE_INCIDENT.fullmatch(incident):
        return False, "REJECTED_INCIDENT_ID", "identifiant d incident invalide"
    return True, None, ""


def disque_parse_find(sortie):
    """Sortie de `find -printf '%y|%u|%m|%n|%s|%f\n'` -> (entrees, illisibles). Le NOM est le DERNIER champ."""
    entrees, illisibles = [], 0
    for ligne in str(sortie or "").split("\n"):
        if not ligne:
            continue
        p = ligne.split("|", 5)
        if len(p) != 6 or len(p[0]) != 1 or not p[3].isdigit() or not p[4].isdigit() or not p[5]:
            illisibles += 1
            continue
        entrees.append({"type": p[0], "proprio": p[1], "mode": p[2], "liens": int(p[3]), "taille": int(p[4]), "nom": p[5]})
    return entrees, illisibles


def disque_plan(entrees, illisibles=0):
    """Plan PUR (aucune E/S) de la liberation : {refus, rejet, jeton, fichiers, taille, etrangers}.
    `refus` n est non vide que si RIEN ne doit etre supprime. Une entree dont le nom n est PAS exactement celui
    d un fichier du scenario est ETRANGERE : comptee, JAMAIS touchee."""
    plan = {"refus": "", "rejet": "", "jeton": None, "fichiers": [], "taille": 0, "etrangers": 0}

    def refuser(rejet, motif):
        plan["rejet"], plan["refus"] = rejet, motif
        plan["jeton"], plan["fichiers"], plan["taille"] = None, [], 0
        return plan

    if illisibles:
        return refuser("UNREADABLE_LISTING", "%d ligne(s) du repertoire illisible(s) : aucune suppression" % illisibles)
    jetons, fichiers, taille = set(), [], 0
    for e in entrees:
        m = DISQUE_RE_FICHIER.fullmatch(e["nom"])
        if not m:
            plan["etrangers"] += 1
            continue
        index = int(m.group(2))
        if (e["type"] != "f" or e["proprio"] != "root" or e["liens"] != 1 or e["taille"] <= 0
                or not 1 <= index <= DISQUE_MAX_FICHIERS):
            return refuser("ANOMALOUS_ENTRY", "entree homonyme non conforme : %s (type=%s proprietaire=%s liens=%s taille=%s)" % (
                e["nom"][:60], e["type"], e["proprio"], e["liens"], e["taille"]))
        jetons.add(m.group(1))
        fichiers.append(e["nom"])
        taille += e["taille"]
    if len(jetons) > 1:
        return refuser("AMBIGUOUS_TOKENS", "%d jetons presents : le Bridge ne devine pas lequel est le bon" % len(jetons))
    if len(fichiers) > DISQUE_MAX_FICHIERS:
        return refuser("TOO_MANY_FILES", "%d fichiers > %d" % (len(fichiers), DISQUE_MAX_FICHIERS))
    if taille > DISQUE_MAX_TOTAL:
        return refuser("TOO_LARGE", "%d Mo a liberer > limite de %d Mo" % (taille // 1048576, DISQUE_MAX_TOTAL // 1048576))
    plan["jeton"] = next(iter(jetons)) if jetons else None
    plan["fichiers"] = sorted(fichiers)
    plan["taille"] = taille
    return plan


def disque_lire_dossier(noeud):
    """Etat du repertoire exclusif : {present, entrees, illisibles} ou {erreur, rejet}. LECTURE SEULE."""
    r = executer(noeud, "sudo -n /usr/bin/stat -c '%F|%U|%a' " + DISQUE_DOSSIER)
    if not r.get("ok"):
        texte = ((r.get("stderr") or "") + " " + (r.get("stdout") or "")).lower()
        if "no such file" in texte or "aucun fichier" in texte:
            return {"present": False, "entrees": [], "illisibles": 0}
        return {"erreur": "stat du repertoire impossible : %s" % str(r.get("resume") or "echec")[:120],
                "rejet": "DIRECTORY_UNREADABLE"}
    lu = (r.get("stdout") or "").strip()
    if lu != "directory|root|700":
        return {"erreur": "repertoire %s non conforme (type|proprietaire|mode attendu directory|root|700, lu %s)" % (
            DISQUE_DOSSIER, lu[:60]), "rejet": "UNSAFE_DIRECTORY"}
    liste = executer(noeud, "sudo -n /usr/bin/find " + DISQUE_DOSSIER
                     + " -maxdepth 1 -mindepth 1 -printf '%y|%u|%m|%n|%s|%f\\n'")
    if not liste.get("ok"):
        return {"erreur": "liste du repertoire impossible : %s" % str(liste.get("resume") or "echec")[:120],
                "rejet": "DIRECTORY_UNREADABLE"}
    sortie = liste.get("stdout") or ""
    if len(sortie) >= DISQUE_LISTE_MAX:
        return {"erreur": "liste du repertoire trop longue (%d caracteres) : suspecte" % len(sortie),
                "rejet": "LISTING_TRUNCATED"}
    entrees, illisibles = disque_parse_find(sortie)
    return {"present": True, "entrees": entrees, "illisibles": illisibles}


def disque_mesurer(noeud):
    """Occupation REELLE de / : {ok, usage, taille, dispo} ou {ok: False, erreur}. LECTURE SEULE (stat -f)."""
    r = executer(noeud, "sudo -n /usr/bin/stat -f -c '%S|%b|%a' /")
    if not r.get("ok"):
        return {"ok": False, "erreur": "mesure impossible : %s" % str(r.get("resume") or "echec")[:120]}
    p = (r.get("stdout") or "").strip().split("|")
    try:
        taille_bloc, total, dispo = int(p[0]), int(p[1]), int(p[2])
    except (ValueError, IndexError):
        return {"ok": False, "erreur": "mesure illisible : %r" % (r.get("stdout") or "")[:60]}
    usage = disque_usage_pct(total, dispo)
    if usage is None or taille_bloc <= 0:
        return {"ok": False, "erreur": "mesure incoherente (%s)" % "|".join(p)[:60]}
    return {"ok": True, "usage": usage, "taille": total * taille_bloc, "dispo": dispo * taille_bloc}


def _disque_lier(incident, jeton):
    with _DISQUE_VERROU:
        if incident not in _DISQUE_LIAISONS and len(_DISQUE_LIAISONS) >= DISQUE_LIAISONS_MAX:
            _DISQUE_LIAISONS.pop(next(iter(_DISQUE_LIAISONS)))
        _DISQUE_LIAISONS.setdefault(incident, jeton)


def _disque_reponse(noeud, ok, execute, stdout="", stderr="", **plus):
    r = {"action": ACTION_DISQUE, "scenario": DISQUE_SCENARIO, "noeud": noeud, "execute": bool(execute), "ok": bool(ok),
         "exitCode": 0 if ok else -1, "stdout": stdout, "stderr": stderr,
         "seuil_recuperation_pct": DISQUE_RECUPERATION_PCT}
    r.update(plus)
    return r


def disque_reclamer(noeud, d):
    """Valide, lit, supprime les SEULS fichiers du jeton, relit, MESURE, conclut. Rend le corps de la reponse /action.
    `recommend_only` rend la validation et le plan SANS rien supprimer."""
    valide, rejet, motif = disque_valider_requete(d, noeud)
    if not valide:
        return _disque_reponse(noeud, False, False, stderr="requete refusee : " + motif, rejet=rejet)
    incident = str(d.get("incident_id", "") or "").strip()
    simulation = str(d.get("recommend_only", "")).lower() in ("1", "true", "yes", "oui")
    with _DISQUE_VERROU:
        lec = disque_lire_dossier(noeud)
        if "erreur" in lec:
            return _disque_reponse(noeud, False, False, stderr=lec["erreur"], rejet=lec["rejet"])
        avant = disque_mesurer(noeud)
        if not avant["ok"]:
            return _disque_reponse(noeud, False, False, stderr=avant["erreur"], rejet="MEASURE_FAILED")
        plan = disque_plan(lec["entrees"], lec["illisibles"])
        usage_avant = round(avant["usage"], 2)
        if plan["refus"]:
            return _disque_reponse(noeud, False, False, stderr=plan["refus"], rejet=plan["rejet"],
                                   etrangers=plan["etrangers"], usage_avant_pct=usage_avant)
        lie = _DISQUE_LIAISONS.get(incident) if incident else None
        if plan["jeton"] and lie and lie != plan["jeton"]:
            return _disque_reponse(noeud, False, False, rejet="TOKEN_CHANGED", etrangers=plan["etrangers"],
                                   stderr="l incident %s est lie au jeton %s : un autre jeton est present, rien n est supprime" % (
                                       incident, lie[:8]), usage_avant_pct=usage_avant)
        if simulation:
            return _disque_reponse(noeud, True, False, validation="ACCEPTED", execution_mode="RECOMMEND_ONLY",
                                   stdout="validation seule : %d fichier(s) a supprimer (%d Mo), %d entree(s) etrangere(s) intacte(s)" % (
                                       len(plan["fichiers"]), plan["taille"] // 1048576, plan["etrangers"]),
                                   jeton=plan["jeton"], fichiers=len(plan["fichiers"]), etrangers=plan["etrangers"],
                                   usage_avant_pct=usage_avant)
        tailles = dict((e["nom"], e["taille"]) for e in lec["entrees"])
        restants = []
        if plan["fichiers"]:
            if incident:
                _disque_lier(incident, plan["jeton"])
            commande = "sudo -n /usr/bin/rm -f -- " + " ".join(shlex.quote(DISQUE_DOSSIER + "/" + n) for n in plan["fichiers"])
            tracer("DISQUE SUPPRESSION", noeud, "jeton", plan["jeton"][:8], "|", len(plan["fichiers"]), "fichier(s) exact(s)")
            executer(noeud, commande)          # le code retour n est PAS une preuve : la relecture tranche
            apres = disque_lire_dossier(noeud)
            if "erreur" in apres:
                return _disque_reponse(noeud, False, True, rejet=apres["rejet"], jeton=plan["jeton"],
                                       stderr="suppression non verifiee : " + apres["erreur"], usage_avant_pct=usage_avant)
            presents = set(e["nom"] for e in apres["entrees"])
            restants = [n for n in plan["fichiers"] if n in presents]
        mesure = disque_mesurer(noeud)
        supprimes = len(plan["fichiers"]) - len(restants)
        octets = sum(tailles.get(n, 0) for n in plan["fichiers"] if n not in restants)
        commun = {"jeton": plan["jeton"], "supprimes": supprimes, "restants": restants, "octets_liberes": octets,
                  "etrangers": plan["etrangers"], "usage_avant_pct": usage_avant, "noop": not plan["fichiers"]}
        if not mesure["ok"]:
            return _disque_reponse(noeud, False, bool(plan["fichiers"]), stderr=mesure["erreur"], rejet="MEASURE_FAILED",
                                   recupere=False, **commun)
        usage_apres = round(mesure["usage"], 2)
        recupere = (not restants) and mesure["usage"] <= DISQUE_RECUPERATION_PCT
        commun["usage_apres_pct"], commun["recupere"] = usage_apres, recupere
        if recupere:
            if not plan["fichiers"]:
                texte = "aucun fichier du scenario a supprimer ; occupation %.2f %% deja <= %.0f %% (idempotent)" % (
                    usage_apres, DISQUE_RECUPERATION_PCT)
            else:
                texte = "%d fichier(s) du jeton %s supprime(s) (%d Mo) ; occupation %.2f %% -> %.2f %% (<= %.0f %%)" % (
                    supprimes, plan["jeton"][:8], octets // 1048576, usage_avant, usage_apres, DISQUE_RECUPERATION_PCT)
            return _disque_reponse(noeud, True, True, stdout=texte, **commun)
        if restants:
            return _disque_reponse(noeud, False, True, rejet="DELETION_INCOMPLETE", **commun,
                                   stderr="suppression incomplete : %d fichier(s) du jeton %s toujours presents" % (
                                       len(restants), plan["jeton"][:8]))
        if not plan["fichiers"]:
            return _disque_reponse(noeud, False, False, rejet="NO_SCENARIO_FILES", **commun,
                                   stderr="aucun fichier du scenario %s ; occupation %.2f %% > %.0f %% : incident etranger, "
                                          "RIEN n a ete touche" % (DISQUE_SCENARIO, usage_apres, DISQUE_RECUPERATION_PCT))
        return _disque_reponse(noeud, False, True, rejet="NOT_RECOVERED", **commun,
                               stderr="fichiers supprimes mais occupation %.2f %% > %.0f %% : recuperation NON confirmee" % (
                                   usage_apres, DISQUE_RECUPERATION_PCT))
'''

METHODE_DISQUE = r'''    def _traiter_disque(self, action, d, noeud):
        """DISK-E2E-158-01 : executeur de HOST_RECLAIM_DISK (scenario DISK_E2E_158).

        Il ne DECIDE rien : il valide, liste, supprime les SEULS fichiers du jeton, relit, puis MESURE.
        Un exitCode 0 de `rm` ne vaut pas preuve : seule l occupation reelle de / conclut.
        """
        res = disque_reclamer(noeud, d)
        tracer("DISQUE", noeud, "ok=%s" % res.get("ok"), "rejet=%s" % res.get("rejet"), "|",
               str(res.get("stdout") or res.get("stderr"))[:150])
        if getattr(self, "_tele_ctx", None):
            self._tele_ctx["commande"] = "HOST_RECLAIM_DISK : suppression des fichiers exacts du jeton, puis mesure"
            self._tele_ctx["hote"] = noeud
        return self.repondre(200, res)

'''

src = replace_once(src, 'UNITE_SYSTEMD = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,62}\\.service$")\n',
                   'UNITE_SYSTEMD = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,62}\\.service$")\n' + BLOC_DISQUE,
                   "disque : bloc de constantes et de fonctions")
src = replace_once(src, "            _cpuload_demande = action.upper() in ACTIONS_CPULOAD\n",
                   "            _cpuload_demande = action.upper() in ACTIONS_CPULOAD\n"
                   "            # " + MARK_DISK + " : meme exemption, pour la meme raison, pour HOST_RECLAIM_DISK. Sans elle, le type\n"
                   "            # NODE_DISK_FULL ecraserait la demande par HOST_IMAGE_GC (ACTION_PAR_TYPE_MESURE, INCHANGEE).\n"
                   "            _disque_demande = action.upper() in ACTIONS_DISQUE\n",
                   "disque : drapeau de demande explicite")
src = replace_once(src, "            if _quota_demande or _cpuload_demande:\n",
                   "            if _quota_demande or _cpuload_demande or _disque_demande:\n", "disque : exemption de substitution")
src = replace_once(src, "            if _quota_demande:\n                quota_balayer()",
                   "            # " + MARK_DISK + " : le nettoyage par jeton est traite AVANT le catalogue (il porte un scenario et exige\n"
                   "            # des preuves : validation, liste exacte, relecture, mesure).\n"
                   "            if _disque_demande:\n"
                   "                return self._traiter_disque(action.upper(), d, noeud)\n"
                   "            if _quota_demande:\n                quota_balayer()", "disque : aiguillage")
src = replace_once(src, "    def _traiter_quota(self, action, d, noeud):\n", METHODE_DISQUE + "    def _traiter_quota(self, action, d, noeud):\n",
                   "disque : methode du handler")
src = replace_once(src, "    _NOMS += list(ACTIONS_CPULOAD)\n",
                   "    _NOMS += list(ACTIONS_CPULOAD)\n    # " + MARK_DISK + " : capacite HOST_RECLAIM_DISK annoncee par /actions\n    _NOMS += list(ACTIONS_DISQUE)\n",
                   "disque : annonce /actions")

os.makedirs(out_dir, exist_ok=True)
open(os.path.join(out_dir, "bridge.py"), "w", encoding="utf-8", newline="").write(src)
shutil.copyfile(os.path.join(base_dir, "telemetrie.py"), os.path.join(out_dir, "telemetrie.py"))
print("bridge cible ecrit:", len(src.splitlines()), "lignes")
