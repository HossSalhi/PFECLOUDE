#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HELIOS — telemetrie decisionnelle persistante (AI-HOST-DECISION-TELEMETRY-01).

CE MODULE N EXECUTE AUCUNE COMMANDE ET NE DECIDE RIEN.
Il enregistre ce qui s est passe. Toute fonction publique est sans effet de bord
sur le chemin de production : elle attrape ses propres exceptions et rend None.
Si la telemetrie tombe en panne, HELIOS continue exactement comme avant.

CHOIX DU STOCKAGE (§15) — JSONL append-only sous /var/lib :

  * append-only : une panne en cours d ecriture perd au pire la derniere ligne
    et ne corrompt jamais l historique deja ecrit ;
  * /var/lib et non /var/log : la remediation _DISK de HELIOS supprime
    /var/log/*.gz et /var/log/*.[0-9]. Ecrire l historique decisionnel sous
    /var/log l exposerait a etre efface par HELIOS lui-meme ;
  * JSONL et non SQLite : le Bridge est multi-thread ; SQLite imposerait un
    verrou d ecriture et un format binaire difficile a auditer par SHA. Les
    registres de campagne HELIOS sont deja en JSONL, le format est coherent ;
  * un fichier par jour : borne la taille, simplifie l export et l archivage.

CE QUE CE MODULE NE FAIT PAS :
  * il ne reconstruit JAMAIS une proposition IA absente (§3, §21) ;
  * il ne deduit JAMAIS la cause du type d incident (§12) ;
  * il ne convertit JAMAIS un NaN (§13) : il stocke des REFERENCES aux features,
    pas leurs valeurs ;
  * il ne transforme JAMAIS un exit code 0 en resolution (§9).
"""
import json
import os
import threading
import time
import uuid

REP = "/var/lib/helios/telemetry"
_LOCK = threading.Lock()

# Correlation DETECT -> DECISION -> VERIFICATION.
# Camunda n emet aucun identifiant de cycle : la seule cle disponible est le
# quadruplet de l incident. Elle suffit car les tentatives sont sequentielles.
_OUVERTS = {}
_OUVERTS_LOCK = threading.Lock()

# §21 : les 2 543 appels /predict anterieurs a ce lot n ont laisse aucune trace.
# Cette constante le dit explicitement plutot que de laisser croire a un vide.
HISTORIQUE_INDISPONIBLE = "historical_unavailable"

SCHEMA_VERSION = "1.0"


def _fichier(prefixe="decisions"):
    return os.path.join(REP, "%s-%s.jsonl" % (prefixe, time.strftime("%Y-%m-%d")))


def _ecrire(ev, prefixe="decisions"):
    """Ajoute une ligne. Ne leve jamais."""
    try:
        os.makedirs(REP, exist_ok=True)
        ev.setdefault("schema_version", SCHEMA_VERSION)
        ev.setdefault("timestamp", int(time.time()))
        ev.setdefault("timestamp_iso", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        ligne = json.dumps(ev, ensure_ascii=False, default=str)
        with _LOCK:
            _chemin = _fichier(prefixe)
            with open(_chemin, "a", encoding="utf-8") as f:
                f.write(ligne + "\n")
                f.flush()
                os.fsync(f.fileno())
            # Le repertoire est setgid root:aiops : le Bridge ecrit en root,
            # le service IA en aiops. Sans droit d ecriture de GROUPE, le
            # second echouerait SILENCIEUSEMENT sur un fichier cree par le
            # premier — defaut constate en production lors de ce lot.
            try:
                os.chmod(_chemin, 0o664)
            except OSError:
                pass
        return ev
    except Exception:
        return None


def _cle(typ, ns, cible, noeud):
    return "%s|%s|%s|%s" % (typ or "", ns or "", cible or "", noeud or "")


def nouvel_id():
    return uuid.uuid4().hex


# ── 1. DETECTION ──────────────────────────────────────────────────────────────
def detection(inc):
    """Ouvre (ou rafraichit) un cycle pour l incident detecte."""
    try:
        typ = inc.get("type") or ""
        k = _cle(typ, inc.get("ns"), inc.get("cible"), inc.get("noeud"))
        with _OUVERTS_LOCK:
            etat = _OUVERTS.get(k)
            if etat is None or typ == "NO_INCIDENT":
                etat = {"cycle_id": nouvel_id(), "attempt": 0, "ouvert": int(time.time())}
                _OUVERTS[k] = etat
            cycle_id = etat["cycle_id"]
        return _ecrire({
            "stage": "DETECTION",
            "event_id": nouvel_id(),
            "cycle_id": cycle_id,
            "incident_detected": typ,
            "detector_output": typ,          # sortie du detecteur, JAMAIS un ground truth
            "detector_evidence": str(inc.get("preuve", ""))[:500],
            "ns": inc.get("ns"), "cible": inc.get("cible"), "noeud": inc.get("noeud"),
            "target": inc.get("noeud") or inc.get("cible"),
            "nb_anomalies": inc.get("nbAnomalies"),
            "monitoring_fiable": inc.get("monitoringFiable"),
            "ground_truth": None,            # jamais rempli depuis le detecteur (§3)
            "injection_id": None,            # NATURAL tant qu aucun episode ne le fournit
        })
    except Exception:
        return None


# ── 2. DECISION ───────────────────────────────────────────────────────────────
def decision(typ, ns, cible, noeud, action_recue, action_selectionnee,
             source, strategie, charge_utile=None):
    """Enregistre la decision AVANT execution. Rend un decision_id, ou None."""
    try:
        k = _cle(typ, ns, cible, noeud)
        with _OUVERTS_LOCK:
            etat = _OUVERTS.setdefault(
                k, {"cycle_id": nouvel_id(), "attempt": 0, "ouvert": int(time.time())})
            etat["attempt"] += 1
            attempt = etat["attempt"]
            cycle_id = etat["cycle_id"]
            did = nouvel_id()
            etat["decision_id"] = did

        p = charge_utile or {}
        # §7/§17 : les champs IA existent TOUJOURS, meme vides. Ils ne sont remplis
        # que si Camunda les transmet. Le Bridge ne recoit pas la prediction
        # structuree de l IA : predictor.py est protege (§29) et n est pas
        # instrumente par ce lot. Un champ absent reste None, jamais devine.
        ev = {
            "stage": "DECISION",
            "event_id": nouvel_id(),
            "cycle_id": cycle_id,
            "decision_id": did,
            "attempt_index": attempt,
            "incident_detected": typ,
            "detector_output": typ,
            "ns": ns, "cible": cible, "noeud": noeud,
            "target": noeud or cible,

            # --- ce que le Bridge a RECU (fait observe, pas une interpretation) ---
            "action_recue": action_recue if action_recue else None,

            # --- proposition IA : champs prets, remplis seulement si transmis ---
            "ai_called": p.get("ai_called"),
            "ai_model": p.get("ai_model"),
            "ai_proposal_incident": p.get("ai_incident"),
            "ai_proposal_severity": p.get("ai_severity"),
            "ai_proposal_cause": p.get("ai_cause"),
            "ai_proposal_action": p.get("ai_action"),
            "ai_confidence": p.get("ai_confidence"),
            "ai_source_note": (None if p.get("ai_action") else
                               "non transmis par l orchestrateur ; predictor.py non "
                               "instrumente (protege §29). Aucune reconstruction (§3)."),

            # --- decision reellement retenue ---
            "decision_selected": action_selectionnee if action_selectionnee else None,
            "decision_source": source,
            "decision_strategy": strategie,
            "policy_id": p.get("policy_id"),
            "policy_result": p.get("policy_result"),

            # --- caracterisation : jamais devinee (§12) ---
            "severity": p.get("severity"),
            "cause": p.get("cause"),

            # --- §13 : reference deterministe vers les features, pas une copie ---
            "features_ref": {
                "source": "/opt/pfe-ai-service/datasets/host_incidents_mvp25.csv",
                "node": noeud or cible,
                "ts_decision": int(time.time()),
                "regle": "derniere ligne du collecteur dont timestamp <= ts_decision "
                         "pour ce node ; NaN conserves tels quels",
            },
            # --- §14/§23 : rattachement a un episode experimental si connu ---
            "injection_id": p.get("injection_id"),
            "ground_truth": p.get("ground_truth"),
            "data_quality": None,
            "error": None,
        }
        return _ecrire(ev) and did
    except Exception:
        return None


# ── 3. EXECUTION ──────────────────────────────────────────────────────────────
def execution(decision_id, action_id, commande, hote, resultat, debut, fin):
    """Enregistre ce qui a REELLEMENT ete execute.

    §8 : action_id garde le nom du catalogue, action_command garde la commande
    reelle. Les deux sont conserves, jamais l un a la place de l autre.
    §9 : un exit code 0 ne vaut PAS resolution ; execution_status ne parle que de
    l execution.
    """
    try:
        r = resultat or {}
        execute = bool(r.get("execute"))
        code = r.get("exitCode")
        if not execute:
            statut = "NOT_EXECUTED"
        elif code == 0:
            statut = "SUCCESS"
        else:
            statut = "FAILED"
        # OUTCOME-CONSOLIDATION-01 : l instant de depart de l action est l origine
        # temporelle de resolution_latency_seconds (§8). On le memorise ici, seul
        # endroit ou il est connu de facon exacte.
        try:
            with _OUVERTS_LOCK:
                for _e in _OUVERTS.values():
                    if _e.get("decision_id") == decision_id:
                        _e["action_started_at"] = debut
                        break
        except Exception:
            pass
        return _ecrire({
            "stage": "EXECUTION",
            "event_id": nouvel_id(),
            "decision_id": decision_id,
            "action_id": action_id,                 # nom du catalogue
            "action_command": commande,             # commande REELLE (§8)
            "action_target": hote,
            "action_executed": execute,             # distinct de action_proposed (§25)
            "execution_started": debut,
            "execution_finished": fin,
            "execution_status": statut,
            "execution_exit_code": code,
            "execution_stdout": str(r.get("stdout", ""))[:2000],
            "execution_stderr": str(r.get("stderr", ""))[:2000],
            "escalade": bool(r.get("escalade")),
            "noop": bool(r.get("noop")),
        })
    except Exception:
        return None


# ── 4. VERIFICATION ───────────────────────────────────────────────────────────
def verification(typ, ns, cible, noeud, res):
    """Enregistre le constat de verification, distinct du resultat de commande."""
    try:
        k = _cle(typ, ns, cible, noeud)
        with _OUVERTS_LOCK:
            etat = _OUVERTS.get(k) or {}
            cycle_id = etat.get("cycle_id")
            did = etat.get("decision_id")
            attempt = etat.get("attempt")
        r = res or {}
        statut = r.get("verification_status")
        # OUTCOME-CONSOLIDATION-01 : etat consolide, distinct du verdict brut.
        maintenant = int(time.time())
        with _OUVERTS_LOCK:
            etat_c = _OUVERTS.get(k)
            if etat_c is not None:
                etat_c["n_verifs"] = (etat_c.get("n_verifs") or 0) + 1
                etat_c.setdefault("verifs_resume", []).append(statut)
                if statut == "NOT_RESOLVED" and not etat_c.get("first_not_resolved_at"):
                    etat_c["first_not_resolved_at"] = maintenant
                if statut == "RESOLVED":
                    etat_c["resolved_at"] = maintenant
                etat_consolide = _consolider(typ, etat_c, statut, maintenant)
                if etat_consolide == ETAT_ECHEC:
                    etat_c["final_failure_at"] = maintenant
                n_ver = etat_c.get("n_verifs")
            else:
                # aucune episode ouvert : verification orpheline (§7 du lot,
                # test d ordering). Elle n est rattachee a rien et ne peut pas
                # conclure un episode.
                etat_consolide = None
                n_ver = None
        ev = _ecrire({
            "stage": "VERIFICATION",
            "outcome_state": etat_consolide,
            "verification_attempt_index": n_ver,
            "event_id": nouvel_id(),
            "cycle_id": cycle_id,
            "decision_id": did,
            "attempt_index": attempt,
            "incident_detected": typ,
            "ns": ns, "cible": cible, "noeud": noeud,
            "target": noeud or cible,
            "verification_status": statut,
            "verification_reason": str(r.get("verification_reason", ""))[:500],
            "verification_before": r.get("avant"),
            "verification_after": r.get("apres"),
            # §10 : final_resolution est distinct du statut de commande. Il ne vaut
            # RESOLVED que si la verification HELIOS le dit.
            "final_resolution": statut if statut in ("RESOLVED", "NOT_RESOLVED") else "UNKNOWN",
            "resolution_latency_s": (int(time.time()) - etat["ouvert"]) if etat.get("ouvert") else None,
        })
        # OUTCOME-CONSOLIDATION-01 : l issue n est emise QUE lorsque l episode
        # conclut. Un TRANSIENT_NOT_RESOLVED n emet rien : il n est pas une issue.
        if etat_consolide in (ETAT_RESOLU, ETAT_ECHEC):
            with _OUVERTS_LOCK:
                e2 = _OUVERTS.get(k)
            if e2 is not None:
                _emettre_issue(typ, ns, cible, noeud, e2, etat_consolide, maintenant)
        # un incident resolu ferme le cycle : la tentative suivante repartira a 1
        if statut == "RESOLVED":
            with _OUVERTS_LOCK:
                _OUVERTS.pop(k, None)
        return ev
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# AI-HOST-AI-PROPOSAL-TELEMETRY-01 — proposition REELLE du service IA
#
# REGLE ABSOLUE (§3, §24) : une valeur issue du mapping deterministe, du
# catalogue, du detector_output ou de decision_selected N EST PAS une
# proposition IA. Elle ne doit JAMAIS etre ecrite dans ai_proposal_action.
#
# L audit du service IA a etabli (inference.py) :
#   * `recommended_action` du service vaut _catalog_action_for(type_detecteur)
#     des que la voie deterministe s applique (strategy DETERMINISTIC_MEASURED_TYPE,
#     chosen_plan_id prefixe "deterministic-"). Ce n est donc PAS une proposition IA.
#   * la seule proposition IA authentique est decision_context["ai_decision"],
#     produite par HeliosLLM (LLM + RAG + CRAG). Le code de production ne la suit
#     jamais : elle sert uniquement a enrichir l explication (inference.py:614-617).
#   * `confidence` vaut 0.3*rule_score + 0.7*model_score : elle est ML, pas LLM.
#   * la branche `llm_result` de inference.py:295 est du CODE MORT
#     (`llm_result = None` en dur ligne 293).
#
# Ce module enregistre donc la proposition LLM+RAG comme proposition IA, et
# conserve separement, a titre factuel, ce que le service a recommande.

def ai_proposal(namespace, app, reponse, ai_decision=None, appel_ok=True, erreur=None,
                contexte=None):
    """Enregistre la proposition REELLE du service IA. Ne leve jamais.

    `reponse`     : le corps de PredictResponse, tel que produit.
    `ai_decision` : decision_context["ai_decision"] si presente — c est la SEULE
                    source admissible pour ai_proposal_action.
    """
    try:
        r = reponse if isinstance(reponse, dict) else {}
        ad = ai_decision if isinstance(ai_decision, dict) else None

        plan_id = r.get("chosen_plan_id") or ""
        voie_deterministe = str(plan_id).startswith("deterministic-")

        # --- la proposition IA authentique, et rien d autre -------------------
        action_ia = (ad or {}).get("action") or None
        cause_ia = (ad or {}).get("diagnostic") or None
        modele_ia = (ad or {}).get("model") or None
        if action_ia:
            source = "LLM+RAG" if (ad or {}).get("rag_grounded") else "LLM"
        else:
            source = "UNKNOWN"

        # --- le type et la severite : d ou viennent-ils reellement ? ----------
        # Sur la voie deterministe, incident_type est REECRIT avec le type du
        # DETECTEUR (inference.py:702). Ce n est alors pas une classification IA.
        if voie_deterministe:
            incident_ia = None
            incident_note = ("incident_type reecrit par la voie deterministe avec le "
                             "type du detecteur : ce n est pas une classification IA")
        else:
            incident_ia = r.get("incident_type")
            incident_note = "classification du moteur (ML + regles)"

        # Fichier DISTINCT : le Bridge ecrit en root, le service IA en aiops.
        # Deux ecrivains, deux fichiers : aucun conflit de droits, aucune
        # ecriture entrelacee. La correlation se fait sur (ns, cible, temps).
        return _ecrire({
            "stage": "AI_PROPOSAL",
            "event_id": nouvel_id(),
            "ns": namespace, "cible": app, "target": app,

            "ai_called": bool(appel_ok),
            "ai_error": erreur,

            # proposition IA au sens strict
            "ai_proposal_incident": incident_ia,
            "ai_proposal_incident_note": incident_note,
            "ai_proposal_severity": r.get("severity"),
            "ai_proposal_cause": cause_ia,
            "ai_proposal_action": action_ia,
            "ai_confidence": r.get("confidence"),
            "ai_confidence_note": ("0.3*rule_score + 0.7*model_score — confiance ML "
                                   "du moteur, PAS une confiance LLM"),
            "ai_model": modele_ia,
            "ai_source": source,
            "ai_rag_grounded": (ad or {}).get("rag_grounded"),
            "ai_crag_grade": (ad or {}).get("crag_grade"),

            # factuel, et VOLONTAIREMENT hors du champ ai_proposal_action
            "service_recommended_action": r.get("recommended_action"),
            "service_recommended_action_source": ("CATALOGUE_VIA_DETERMINISTIC_POLICY"
                                                  if voie_deterministe else "ENGINE_PLAN"),
            "service_chosen_plan_id": plan_id or None,
            "service_execution_mode": r.get("execution_mode"),
            "service_final_executable_action": r.get("final_executable_action"),
            "deterministic_path": voie_deterministe,

            # CONTEXT-INTEGRATION-FIX-01 §9 : ce que le LLM a REELLEMENT recu.
            # Permet de verifier apres campagne, sans relire le prompt complet, que
            # pour NODE_HIGH_CPU le modele a bien eu host_cpu_pct et host_load15_per_core.
            "detector_incident_type": (contexte or {}).get("detector_incident_type"),
            "ai_context_scope": (contexte or {}).get("ai_context_scope"),
            "ai_context_features": (contexte or {}).get("ai_context_features"),
            "ai_rag_query_scope": (contexte or {}).get("ai_rag_query_scope"),
            "ai_context_thresholds_crossed": (contexte or {}).get(
                "ai_context_thresholds_crossed"),

            # RCA-CHARACTERIZATION-FIX-01 §9 : la caracterisation elle-meme,
            # enregistree separement du texte. Une phrase peut etre centree hote
            # sans nommer la bonne ressource — c est exactement ce qui se produisait
            # sur les inodes.
            "ai_resource": (ad or {}).get("resource"),
            "ai_mechanism": (ad or {}).get("mechanism"),
            "ai_impact": (ad or {}).get("impact"),
            "ai_parse_status": (ad or {}).get("parse_status"),
            "ai_parse_reason": (ad or {}).get("parse_reason"),

            # ACTION-DECISION-FIX-01 §9 : de quoi rejouer la decision d action.
            # Permet d etablir si la bonne action etait PARMI LES CANDIDATS, et
            # donc de distinguer un mauvais choix d un mauvais choix possible.
            "ai_rag_query": ((ad or {}).get("crag") or {}).get("rag_query"),
            "ai_rag_cards": ((ad or {}).get("crag") or {}).get("cards"),
            "ai_rag_card_scores": ((ad or {}).get("crag") or {}).get("card_scores"),
            "ai_rag_candidate_actions": ((ad or {}).get("crag") or {}).get(
                "candidate_actions"),
            "ai_rag_neighbors": ((ad or {}).get("crag") or {}).get("neighbors"),

            # §10 : qualification INDEPENDANTE du modele, rangee a cote de la
            # proposition et jamais a sa place. Les quatre champs restent distincts :
            # proposition IA / deterministe / executee / verdict.
            "ai_action_validation": (contexte or {}).get("ai_action_validation"),
            "ai_action_validation_reason": (contexte or {}).get(
                "ai_action_validation_reason"),

            # jamais derives du detecteur
            "ground_truth": None,
            "injection_id": None,
        }, prefixe="ai-proposals")
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# AI-HOST-VERIFICATION-OUTCOME-CONSOLIDATION-01 — issue consolidee par episode
#
# LE DEFAUT CORRIGE. `verification()` recopiait le verdict d UNE verification
# dans final_resolution. Or l audit AI-HOST-VERIFICATION-COMPLETENESS-AUDIT-01 a
# mesure que 82 a 100 % des NOT_RESOLVED sont TRANSITOIRES : la verification est
# evaluee avant que la fenetre de la metrique ne se soit vidée, et le cycle
# suivant constate la resolution. Utiliser ce champ tel quel pour D2 enseignerait
# a une IA que des remediations reussies ont echoue.
#
# FENETRES DE VIDANGE, DERIVEES DES CONTRATS DE VERIFICATION REELS (bridge.py).
# Ce sont des DUREES D OBSERVATION, pas des seuils : aucun seuil n est modifie.
#   NODE_HIGH_CPU    verification min_over_time((100-rate(...[3m])*100)[5m:30s]) > 90
#                    -> la metrique sous-jacente est un rate[3m] : il lui faut
#                       180 s pour refleter l arret de la charge.
#   NODE_HIGH_MEMORY verification max_over_time((...)[2m:30s]) > 92
#                    -> exige 2 min PLEINES sous le seuil : 120 s.
#   NODE_DISK_FULL   verification instantanee -> 0 s.
#   NODE_INODES_FULL verification instantanee -> 0 s.
# Marge : un cycle Camunda (60 s), car la verification n est echantillonnee qu a
# cette granularite. Une NOT_RESOLVED survenue dans fenetre + marge est donc
# TRANSITOIRE par construction, pas un echec.
FENETRES_VIDANGE = {
    "NODE_HIGH_CPU": 180,
    "NODE_HIGH_MEMORY": 120,
    "NODE_DISK_FULL": 0,
    "NODE_INODES_FULL": 0,
    "NODE_NOT_READY": 180,
    "VM_DOWN": 180,
}
FENETRE_DEFAUT = 180          # prudent : jamais moins que la plus longue connue
MARGE_CYCLE = 60              # un cycle Camunda (timer R/PT60S)
MAX_TENTATIVES = 3            # BPMN v19 : ctx.maxTentatives = 3

# Vocabulaire normalise (§6). Trois valeurs, pas davantage.
ETAT_RESOLU = "RESOLVED"
ETAT_TRANSITOIRE = "TRANSIENT_NOT_RESOLVED"
ETAT_ECHEC = "FINAL_FAILURE"
ETAT_SAFE_STOP = "SAFE_STOP"


def fenetre_vidange(typ):
    """Duree au-dela de laquelle un NOT_RESOLVED cesse d etre transitoire."""
    return FENETRES_VIDANGE.get(typ, FENETRE_DEFAUT) + MARGE_CYCLE


def _consolider(typ, etat, statut, maintenant):
    """REGLE DE CONSOLIDATION, deterministe et documentee (§7).

        1. une verification RESOLVED           -> RESOLVED (definitif)
        2. sinon, encore dans la fenetre de vidange OU tentatives restantes
                                               -> TRANSIENT_NOT_RESOLVED
        3. sinon, tentatives epuisees ET fenetre ecoulee
                                               -> FINAL_FAILURE

    Un NOT_RESOLVED immediat ne produit JAMAIS FINAL_FAILURE : la regle attend le
    moment defini par le contrat de verification du type.
    """
    if statut == "RESOLVED":
        return ETAT_RESOLU
    if statut != "NOT_RESOLVED":
        return None                      # UNKNOWN : aucune conclusion
    depuis = maintenant - (etat.get("action_started_at") or etat.get("ouvert") or maintenant)
    dans_fenetre = depuis <= fenetre_vidange(typ)
    tentatives_restantes = (etat.get("attempt") or 1) < MAX_TENTATIVES
    if dans_fenetre or tentatives_restantes:
        return ETAT_TRANSITOIRE
    return ETAT_ECHEC


def _emettre_issue(typ, ns, cible, noeud, etat, issue, maintenant):
    """Ecrit l evenement consolide. Idempotent : une seule issue par episode."""
    if etat.get("_issue_emise"):
        return None
    etat["_issue_emise"] = True
    debut = etat.get("action_started_at") or etat.get("ouvert")
    return _ecrire({
        "stage": "EPISODE_OUTCOME",
        "event_id": nouvel_id(),
        "cycle_id": etat.get("cycle_id"),
        "decision_id": etat.get("decision_id"),
        "attempt_index": etat.get("attempt"),
        "verification_attempt_index": etat.get("n_verifs"),
        "incident_type": typ,
        "ns": ns, "cible": cible, "noeud": noeud, "target": noeud or cible,
        "final_outcome": issue,
        "action_started_at": debut,
        "first_not_resolved_at": etat.get("first_not_resolved_at"),
        "resolved_at": etat.get("resolved_at"),
        "final_failure_at": etat.get("final_failure_at"),
        # §8 : origine temporelle = debut de l EXECUTION de l action, jamais la
        # detection. C est le seul instant ou l on sait que la remediation a
        # commence a agir.
        "resolution_latency_seconds": ((etat.get("resolved_at") or maintenant) - debut)
                                      if debut else None,
        "fenetre_vidange_s": fenetre_vidange(typ),
        "max_tentatives": MAX_TENTATIVES,
        "verifications": etat.get("verifs_resume"),
        "ground_truth": None, "injection_id": None,
    }, prefixe="outcomes")


# Idempotence (§8) : un SAFE_STOP identique repete sur le meme episode ne produit
# qu UNE issue. La cle est (ns, cible, type) et la signature du predicat : tant que
# rien ne change, rien n est reecrit. Une signature differente ouvre un nouvel
# episode de decision et emet une nouvelle issue.
_SAFESTOP_VU = {}
_SAFESTOP_LOCK = threading.Lock()


def predicat_auto_execute(execution_mode, safe_to_execute, action_executable,
                          requires_approval):
    """Reproduit A L IDENTIQUE la condition du flux f_exec de PolicyGate.

    Relevee dans les BPMN v17 (instance REELLEMENT active) et v19 (derniere
    deployee), identiques mot pour mot :

      =pol.executionMode = "AUTO_EXECUTE_CANDIDATE"
       and pol.safeToExecute = true
       and pol.actionExecutable != null and pol.actionExecutable != ""
       and pol.requiresApproval = false

    PolicyGate porte default="f_safestop" : si cette condition est fausse, la
    passerelle emprunte la branche par defaut, donc SAFE_STOP.

    Cette fonction ne DECIDE rien (§14) : elle evalue le meme predicat sur les
    memes donnees, pour enregistrer ce que Camunda va faire.
    """
    return (execution_mode == "AUTO_EXECUTE_CANDIDATE"
            and safe_to_execute is True
            and action_executable not in (None, "")
            and requires_approval is False)


def safe_stop(typ, ns, cible, noeud, motif, source="POLICY", operandes=None,
              incident=None, attempt_index=None):
    """§10 : une sortie AVANT verification est une issue explicite, pas un trou.

    Empeche qu un futur D2 confonde « la policy a refuse » et « la verification
    manque ». Ne change AUCUNE politique metier : n enregistre que le fait.
    """
    try:
        k = _cle(typ, ns, cible, noeud)
        op = operandes if isinstance(operandes, dict) else {}
        signature = "%s|%s|%s|%s|%s" % (typ, op.get("execution_mode"),
                                        op.get("safe_to_execute"),
                                        op.get("action_executable"),
                                        op.get("requires_approval"))
        with _SAFESTOP_LOCK:
            if _SAFESTOP_VU.get(k) == signature:
                return None                      # deja trace, rien de nouveau
            _SAFESTOP_VU[k] = signature
        with _OUVERTS_LOCK:
            etat = _OUVERTS.get(k) or {}
            cycle_id = etat.get("cycle_id")
            did = etat.get("decision_id")
            att = attempt_index if attempt_index is not None else etat.get("attempt")
        return _ecrire({
            "stage": "EPISODE_OUTCOME",
            "event_id": nouvel_id(),
            "cycle_id": cycle_id, "decision_id": did,
            "attempt_index": att,
            "incident_type": typ, "ns": ns, "cible": cible, "noeud": noeud,
            "target": noeud or cible,
            "final_outcome": ETAT_SAFE_STOP,
            "safe_stop_reason": str(motif)[:300],
            "safe_stop_source": source,
            "verification_attendue": False,
            "auto_execute_allowed": False,
            "policy_reason": op.get("policy_reason"),
            # non observable depuis le service IA : c est le detecteur qui le
            # produit et Camunda qui l evalue. On ne l invente pas (§5).
            "monitoring_reliable": op.get("monitoring_reliable"),
            "policy_operands": op or None,
            "predicat_source": "BPMN f_exec (v17 == v19), reevalue a l identique",
            "resolution_latency_seconds": None,
            "ground_truth": None, "injection_id": None,
        }, prefixe="outcomes")
    except Exception:
        return None


def _safestop_oublier(cle=None):
    """Reservee aux tests : purge la memoire d idempotence."""
    with _SAFESTOP_LOCK:
        if cle is None:
            _SAFESTOP_VU.clear()
        else:
            _SAFESTOP_VU.pop(cle, None)


def etat_interne():
    """Lecture seule, pour les tests."""
    with _OUVERTS_LOCK:
        return dict(_OUVERTS)


def reinitialiser():
    """Reservee aux tests. Ne touche a aucun fichier."""
    with _OUVERTS_LOCK:
        _OUVERTS.clear()
