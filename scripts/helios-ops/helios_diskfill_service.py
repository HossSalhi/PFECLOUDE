#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HELIOS - diagnostic manuel et statut du service local DISK de .158.

    python helios_diskfill_service.py --preflight --image-backend TAG [--accepte-orange CODE[,CODE...]]  # lecture seule, diagnostic
    python helios_diskfill_service.py --statut                                                         # lecture seule
    python helios_diskfill_service.py --sommes                                                         # regenere SHA256SUMS local

DEMARRAGE DIRECT : `sudo systemctl start helios-diskfill.service` suffit, directement dans la VM, COMME `helios-cpuload.service` : aucun
armement manuel et AUCUNE dependance de l'hote Windows (decision de l'exploitant, 17 nov. 2026). ExecStartPre execute automatiquement tous les
controles du noeud. L'attestation de l'agent Windows DISK authentifie (192.168.148.1:19158 ; collecteurs reels de helios_disk_test.py et porte
DISK_E2E_158) est OPTIONNELLE : desactivee par defaut (ATTESTATION_HOTE_REQUISE = False dans diskfill.py, lue ici pour adapter les messages) ;
requise, une attestation absente, invalide, perimee, ROUGE ou ORANGE non autorise refuse le demarrage avant toute allocation.

Le diagnostic manuel `--preflight` est facultatif et en lecture seule ; il ne depose rien. `--armer` est obsolete et ne depose rien.
Le watchdog VM_DOWN 192.168.148.1:9099 reste independant et inchange.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DIR_SVC = os.path.join(REPO, "infra", "helios-sot", "diskfill")
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import helios_disk_test as H  # noqa: E402

CLE = "158"
NOM_K8S, IP = H.CIBLES[CLE]
NOMS_SOT = ("diskfill.py", "helios-diskfill.service", "helios-diskguard.service")
NIVEAUX_SERVICE = ("ROUGE", "INFO")                 # le service n'ajoute JAMAIS d'ORANGE (aucune condition acquittable nouvelle)
_NOEUD = None


def noeud_module():
    """Charge diskfill.py (bibliotheque standard seule, sans effet de bord a l'import) : source UNIQUE des parametres figes et des regles du noeud.
    Le meme fichier, verifie par SHA-256, est celui qui s'execute sur .158."""
    global _NOEUD
    if _NOEUD is None:
        spec = importlib.util.spec_from_file_location("diskfill_noeud", os.path.join(DIR_SVC, "diskfill.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["diskfill_noeud"] = mod
        spec.loader.exec_module(mod)
        _NOEUD = mod
    return _NOEUD


# ------------------------------------------------------------------------------------------------ empreintes du depot (SHA256SUMS)
def lire_source(nom):
    """Octets du fichier du depot, fins de ligne normalisees en LF (core.autocrlf=true sur ce poste : les empreintes et l'installation portent
    sur le contenu LF, celui qui s'execute sur Linux)."""
    if nom not in NOMS_SOT:
        raise ValueError("fichier hors de la source de verite : %r" % (nom,))
    with open(os.path.join(DIR_SVC, nom), "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def sommes_calculees():
    return dict((n, hashlib.sha256(lire_source(n)).hexdigest()) for n in NOMS_SOT)


def contenu_sommes(sommes):
    return "".join("%s  %s\n" % (sommes[n], n) for n in sorted(sommes))


def lire_sommes_fichier():
    """SHA256SUMS du depot -> dict nom -> empreinte ; None si absent ou illisible."""
    try:
        with open(os.path.join(DIR_SVC, "SHA256SUMS"), "rb") as f:
            texte = f.read().decode("ascii")
    except (OSError, UnicodeError):
        return None
    sommes = {}
    for ligne in texte.split("\n"):
        if not ligne.strip():
            continue
        m = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", ligne)
        if not m:
            return None
        sommes[m.group(2)] = m.group(1)
    return sommes


def ecrire_sommes():
    sommes = sommes_calculees()
    with open(os.path.join(DIR_SVC, "SHA256SUMS"), "wb") as f:
        f.write(contenu_sommes(sommes).encode("ascii"))
    return sommes


# ------------------------------------------------------------------------------------------------ lecture du noeud (LECTURE SEULE)
def chemins_noeud():
    D = noeud_module()
    return {"diskfill.py": D.CHEMIN_SCRIPT, "helios-diskfill.service": D.CHEMIN_UNITE[D.UNITE_PRINCIPALE],
            "helios-diskguard.service": D.CHEMIN_UNITE[D.UNITE_GARDE]}


def argv_script(*mots):
    D = noeud_module()
    return ["sudo", "-n", "/usr/bin/python3", "-I", "-B", D.CHEMIN_SCRIPT] + list(mots)


def lire_service(exe, ip=IP):
    """Installation du service sur .158 : empreintes SHA-256 mesurees PAR `sha256sum` (jamais l'auto-declaration du programme) et faits du noeud
    (`diskfill.py etat --json`, lecture seule). Une lecture impossible = None (jamais une valeur devinee)."""
    chemins = chemins_noeud()
    r = exe.ssh(ip, ["sudo", "-n", "sha256sum", "--"] + [chemins[n] for n in NOMS_SOT], 30)
    lus = {}
    for ligne in (r.out or "").splitlines():
        m = re.fullmatch(r"([0-9a-f]{64})\s+(\S+)", ligne.strip())
        if m:
            lus[m.group(2)] = m.group(1)
    sha = dict((n, lus.get(chemins[n])) for n in NOMS_SOT)
    e = exe.ssh(ip, argv_script("etat", "--json"), 45)
    etat = None
    if e.rc == 0:
        lignes = [l for l in (e.out or "").splitlines() if l.strip()]
        try:
            obj = json.loads(lignes[-1]) if lignes else None
        except ValueError:
            obj = None
        etat = obj if isinstance(obj, dict) and obj.get("version") == noeud_module().VERSION else None
    return {"sha256": sha, "etat": etat}


def evaluer_service(sommes, service, cooldown_backend_min=None):
    """Controles propres au service local -> raisons (niveau, constat, remede, code). PURE. Niveaux : ROUGE ou INFO SEULEMENT (jamais ORANGE).
    `sommes` = {'calculees': dict, 'fichier': dict | None} ; `service` = lire_service(...) ou None."""
    D = noeud_module()
    r = []

    def add(niveau, constat, remede="", code=None):
        assert niveau in NIVEAUX_SERVICE, "le service n'ajoute jamais d'ORANGE"
        r.append((niveau, constat, remede, code))
    if sommes.get("fichier") != sommes.get("calculees"):
        add("ROUGE", "SHA256SUMS du depot absent ou perime : il ne correspond pas aux fichiers de infra/helios-sot/diskfill",
            "python helios_diskfill_service.py --sommes (apres relecture du diff)", "SVC_SOMMES_DEPOT")
    if not isinstance(service, dict):
        add("ROUGE", "installation du service sur .%s illisible (ssh)" % CLE, "refus (echec sur)", "SVC_ILLISIBLE")
        return r
    attendu = sommes.get("calculees") or {}
    manquants = [n for n in NOMS_SOT if not (service.get("sha256") or {}).get(n)]
    if manquants:
        add("ROUGE", "service NON INSTALLE (ou illisible) sur .%s : %s" % (CLE, ", ".join(manquants)),
            "installation par deploy_diskfill_service_158.py, decision distincte et autorisee", "SVC_NON_INSTALLE")
    else:
        for n in NOMS_SOT:
            lu = service["sha256"][n]
            if lu != attendu.get(n):
                add("ROUGE", "%s installe sur .%s : empreinte %s... != source de verite %s..." % (n, CLE, lu[:12], str(attendu.get(n))[:12]),
                    "reinstaller la source de verite (decision distincte)", "SVC_SHA_" + re.sub(r"[^A-Z]", "_", n.upper()))
            else:
                add("INFO", "%s : empreinte SHA-256 identique a la source de verite (%s...)" % (n, lu[:12]), "")
    etat = service.get("etat")
    if not isinstance(etat, dict):
        add("ROUGE", "faits du noeud illisibles (`diskfill.py etat --json`) : le service n'est pas utilisable ou pas installe", "refus (echec sur)",
            "SVC_ETAT_ILLISIBLE")
        return r
    if etat.get("hostname") != D.NOM_CIBLE or etat.get("ip_presente") is not True:
        add("ROUGE", "identite de la machine : nom d'hote %r (attendu %r), IP %s %s" % (
            etat.get("hostname"), D.NOM_CIBLE, D.IP_CIBLE, "presente" if etat.get("ip_presente") else "ABSENTE ou illisible"),
            "ne rien lancer ; verifier la correspondance IP / nom", "SVC_IDENTITE")
    else:
        add("INFO", "identite verifiee par le noeud : nom d'hote %s, IP %s presente" % (etat["hostname"], D.IP_CIBLE), "")
    unites = etat.get("unites") or {}
    for unite in (D.UNITE_PRINCIPALE, D.UNITE_GARDE):
        p = unites.get(unite)
        if not isinstance(p, dict):
            add("ROUGE", "%s : proprietes systemd illisibles" % unite, "refus (echec sur)", "SVC_UNITE_ILLISIBLE")
            continue
        defauts = []
        if p.get("LoadState") != "loaded":
            defauts.append("LoadState=%s" % p.get("LoadState"))
        if p.get("FragmentPath") != D.CHEMIN_UNITE[unite]:
            defauts.append("FragmentPath=%s" % p.get("FragmentPath"))
        if (p.get("DropInPaths") or "") != "":
            defauts.append("drop-in present (%s)" % p.get("DropInPaths"))
        if p.get("UnitFileState") != "static":
            defauts.append("UnitFileState=%s (static exige : jamais enable)" % p.get("UnitFileState"))
        if (p.get("StateDirectoryMode") or "").lstrip("0") != "700":
            defauts.append("StateDirectoryMode=%s (0700 EXIGE : contrat du Bridge directory|root|700)" % p.get("StateDirectoryMode"))
        if p.get("Restart") != "no":
            defauts.append("Restart=%s" % p.get("Restart"))
        if p.get("NeedDaemonReload") != "no":
            defauts.append("NeedDaemonReload=%s (la configuration chargee n'est pas celle du fichier : daemon-reload requis)" % p.get("NeedDaemonReload"))
        if defauts:
            add("ROUGE", "%s non conforme : %s" % (unite, " ; ".join(defauts)), "reinstaller la source de verite (decision distincte)", "SVC_UNITE")
        else:
            add("INFO", "%s : chargee (= fichier), aucun drop-in, static (non enable), StateDirectoryMode=0700, Restart=no" % unite, "")
    occupees = etat.get("unites_occupees") or {}
    for unite in (D.UNITE_PRINCIPALE, D.UNITE_GARDE):
        if occupees.get(unite) is not False:
            add("ROUGE", "%s active ou etat illisible (%r) : un lancement est en cours ou une garde est encore armee" % (unite, occupees.get(unite)),
                "attendre la fin du lancement (ou sudo systemctl stop %s)" % D.UNITE_PRINCIPALE, "SVC_UNITE_ACTIVE")
    dossier = etat.get("dossier")
    contenu = etat.get("contenu_dossier")
    if not isinstance(dossier, dict):
        add("ROUGE", "repertoire %s illisible" % D.DOSSIER, "refus (echec sur)", "SVC_DOSSIER")
    elif dossier.get("existe") and dossier.get("anomalies") != []:
        add("ROUGE", "repertoire %s non conforme au contrat du Bridge : %s" % (D.DOSSIER, "illisible" if dossier.get("anomalies") is None
                                                                               else " ; ".join(dossier["anomalies"])),
            "l'exploitant corrige lui-meme (le Bridge refuserait toute suppression)", "SVC_DOSSIER")
    elif contenu:
        add("ROUGE", "%s contient %d entree(s) : ORPHELINS d'un lancement precedent (aucun nettoyage automatique)" % (D.DOSSIER, len(contenu)),
            "l'exploitant les examine puis : sudo /usr/bin/python3 -I -B %s nettoyer-orphelins" % D.CHEMIN_SCRIPT, "SVC_ORPHELINS")
    elif contenu is None and dossier.get("existe"):
        add("ROUGE", "contenu de %s illisible" % D.DOSSIER, "refus (echec sur)", "SVC_DOSSIER")
    else:
        add("INFO", "repertoire %s : %s" % (D.DOSSIER, "vide, conforme (directory, root:root, 0700, volume de /)" if dossier.get("existe")
                                            else "absent (cree par systemd au demarrage, mode 0700)"), "")
    if etat.get("unites_transitoires") != []:
        add("ROUGE", "unites transitoires du lanceur (helios-diskfill-*) presentes ou illisibles : %r" % (etat.get("unites_transitoires"),),
            "l'exploitant les examine lui-meme", "SVC_RESIDUS_LANCEUR")
    cd = etat.get("cooldown") or {}
    if cd.get("erreur"):
        add("ROUGE", "cooldown local illisible : %s" % cd["erreur"], "refus (echec sur)", "SVC_COOLDOWN_LOCAL")
    elif cd.get("restant_s"):
        add("ROUGE", "cooldown local de %d min non ecoule : encore %d min %d s" % (cd.get("local_min", D.COOLDOWN_LOCAL_S // 60),
                                                                                    cd["restant_s"] // 60, cd["restant_s"] % 60),
            "attendre la fin du cooldown (un 2e lancement rapproche creerait un incident non remedie)", "SVC_COOLDOWN_LOCAL")
    else:
        add("INFO", "cooldown local de %d min : ecoule ou aucun lancement precedent" % cd.get("local_min", D.COOLDOWN_LOCAL_S // 60), "")
    if isinstance(cooldown_backend_min, int) and not isinstance(cooldown_backend_min, bool) and cooldown_backend_min * 60 > D.COOLDOWN_LOCAL_S:
        add("ROUGE", "cooldown du Backend (%d min) PLUS LONG que le cooldown local (%d min) : le filet local ne couvre plus le Backend" % (
            cooldown_backend_min, D.COOLDOWN_LOCAL_S // 60), "aligner les deux (decision distincte)", "SVC_COOLDOWN_INFERIEUR_BACKEND")
    elif isinstance(cooldown_backend_min, int) and not isinstance(cooldown_backend_min, bool):
        add("INFO", "cooldown du Backend REELLEMENT applique : %d min (<= cooldown local de %d min)" % (cooldown_backend_min, D.COOLDOWN_LOCAL_S // 60), "")
    e_etat = etat.get("etat")
    if isinstance(e_etat, dict) and e_etat.get("phase") in D.PHASES_ACTIVES:
        add("ROUGE", "un lancement du service est en cours ou incomplet (phase %s, jeton %s...)" % (e_etat.get("phase"), e_etat.get("jeton8")),
            "attendre sa fin ; liberation incomplete : nettoyage manuel des orphelins", "SVC_LANCEMENT_EN_COURS")
    return r


# ------------------------------------------------------------------------------------------------ rapport fusionne
def fusionner(rapport, raisons_service, acquittes):
    """Rapport de la porte (identique a celui de --go) + raisons du service. Meme regle : `go` = aucun ROUGE ET aucun ORANGE non acquitte."""
    raisons = list(rapport["raisons"]) + list(raisons_service)
    verdict = "ROUGE" if any(x[0] == "ROUGE" for x in raisons) else ("ORANGE" if any(x[0] == "ORANGE" for x in raisons) else "VERT")
    non_acquittes = [x for x in raisons if x[0] == "ORANGE" and x[3] != H.CODE_CNPG_ACCEPTE and x[3] not in acquittes]
    fusion = dict(rapport)
    fusion.update(raisons=raisons, verdict=verdict, go=verdict != "ROUGE" and not non_acquittes, orange_non_acquittes=non_acquittes,
                  acquittes=sorted(acquittes))
    return fusion


# ------------------------------------------------------------------------------------------------ orchestration
def collecter_defaut(deps, a):
    """Faits REELS en lecture seule : EXACTEMENT ce que `helios_disk_test.py --target 158 --preflight/--go` collecte, puis la porte du scenario."""
    faits, ctx = H.collecter_reel(deps, CLE, a.vm_base)
    porte = H.module_porte_e2e()
    faits_porte = porte.collecter_faits_e2e(deps, ctx["exe"], ctx["primaire"])
    return faits, faits_porte, ctx


def preflight_complet(a, acquittes, deps, collecteur, sortie):
    """Pre-vol du lanceur (inchange) + controles du service. Retourne (rapport fusionne, faits, faits_porte, ctx, service)."""
    porte = H.module_porte_e2e()
    faits, faits_porte, ctx = collecteur(deps, a)
    rapport = porte.evaluer_e2e(faits, faits_porte, porte.parametres(), CLE, image_backend_attendue=a.image_backend, acquittes=acquittes)
    service = lire_service(ctx["exe"])
    sommes = {"calculees": sommes_calculees(), "fichier": lire_sommes_fichier()}
    raisons = evaluer_service(sommes, service, (faits_porte or {}).get("cooldown_minutes"))
    fusion = fusionner(rapport, raisons, acquittes)
    sortie("PRE-VOL DU SERVICE LOCAL DISK DE .%s : pre-vol du lanceur DISK_E2E_158 (inchange) + controles du service" % CLE)
    porte.afficher_rapport_e2e(fusion, sortie)
    return fusion, faits, faits_porte, ctx, service


MESSAGE_ARMER_OBSOLETE = (
    "OBSOLETE : --armer n'existe plus. Le service local DISK de .158 ne demande AUCUN armement : sur .158, UNE SEULE commande suffit,\n"
    "  sudo systemctl start helios-diskfill.service\n"
    "ExecStartPre execute automatiquement les controles du noeud et REFUSE, sans rien allouer, si l'un echoue ; aucune dependance de l'hote Windows\n"
    "(comme helios-cpuload.service). --preflight reste un diagnostic manuel facultatif, en lecture seule.")


def construire_parseur():
    ap = argparse.ArgumentParser(description="Pre-vol diagnostic et statut du service local DISK de .158 (lecture seule, sauf --sommes qui ecrit SHA256SUMS "
                                             "en local). Le demarrage ne demande plus d'armement.", allow_abbrev=False)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--preflight", action="store_true", help="pre-vol REEL en lecture seule : rien n'est ecrit, rien n'est depose, rien n'est lance")
    g.add_argument("--statut", action="store_true", help="etat du service sur .158 (lecture seule)")
    g.add_argument("--sommes", action="store_true", help="regenere SHA256SUMS du depot (seule ecriture locale)")
    ap.add_argument("--accepte-orange", action="append", default=None, dest="accepte_orange", metavar="CODE[,CODE...]",
                    help="classe comme ACQUITTES, code par code, les ORANGE du pre-vol (comme --go du lanceur) : affichage seulement, rien n'est enregistre "
                    "sur .158. Codes : " + " ; ".join("%s = %s" % (c, H.CODES_ORANGE[c]) for c in H.CODES_ORANGE_ACQUITTABLES
                                                      if c != H.CODE_REMEDIATION_NON_ACTIVEE))
    ap.add_argument("--image-backend", default=None, dest="image_backend", metavar="TAG",
                    help="image du Backend qui DOIT tourner (ex. pfe-backend:0.2.165-disk-e2e) ; obligatoire pour --preflight")
    ap.add_argument("--vm-base", default=H.VM_BASE_DEFAUT, help="dossier des VM VMware sur l'hote")
    return ap


def afficher_statut(service, sortie):
    if not isinstance(service, dict):
        sortie("STATUT : installation illisible (ssh)")
        return
    manquants = [n for n in NOMS_SOT if not (service.get("sha256") or {}).get(n)]
    attendu = sommes_calculees()
    sortie("STATUT du service local DISK sur .%s (lecture seule)" % CLE)
    if manquants:
        sortie("  installation : NON INSTALLE (%s)" % ", ".join(manquants))
    else:
        for n in NOMS_SOT:
            lu = service["sha256"][n]
            sortie("  %-26s SHA-256 %s... : %s" % (n, lu[:12], "identique a la source de verite" if lu == attendu[n] else "DIFFERENT de la source de verite"))
    etat = service.get("etat")
    if not isinstance(etat, dict):
        sortie("  faits du noeud : illisibles")
        return
    sortie("  demarrage : DIRECT (aucun armement) ; attestation de l'hote Windows : %s ; dernier lancement : %s" % (
        "NON requise (autonome, comme helios-cpuload.service)" if etat.get("attestation_hote_requise") is False
        else "REQUISE (echec ferme si l'agent est absent)" if etat.get("attestation_hote_requise") is True else "inconnue (programme installe plus ancien)",
        etat.get("etat") or "aucun"))
    cd = etat.get("cooldown") or {}
    sortie("  cooldown local : %s" % ("illisible : %s" % cd.get("erreur") if cd.get("erreur") else "ecoule" if not cd.get("restant_s")
                                      else "encore %d min %d s" % (cd["restant_s"] // 60, cd["restant_s"] % 60)))
    sortie("  unites actives : %s ; orphelins : %s ; occupation de / : %s %% ; inodes de / : %s %%" % (
        etat.get("unites_occupees"), len(etat.get("contenu_dossier") or []), etat.get("usage_pct"), etat.get("inodes_pct")))


def afficher_lancement_direct(sortie, verdict):
    """Mode d'emploi. Le diagnostic manuel est facultatif ; la verification Windows par ExecStartPre n'existe que si ATTESTATION_HOTE_REQUISE
    (DESACTIVEE par defaut : le demarrage est alors autonome, comme helios-cpuload.service)."""
    requise = bool(noeud_module().ATTESTATION_HOTE_REQUISE)
    if requise:
        if verdict == "ROUGE":
            sortie("ATTENTION : le diagnostic manuel est ROUGE ; le demarrage direct sera refuse par ExecStartPre tant que Windows ne sera pas VERT.")
        elif verdict == "ORANGE":
            sortie("ATTENTION : le diagnostic manuel est ORANGE ; ExecStartPre exige VERT strict et refusera le demarrage tant que les ORANGE persistent.")
    elif verdict == "ROUGE":
        sortie("ATTENTION : le diagnostic manuel est ROUGE. Le demarrage direct NE le consulte PAS (attestation Windows non requise : seul le noeud est controle) ;")
        sortie("            corriger ces conditions de l'hote AVANT de lancer est fortement recommande.")
    elif verdict == "ORANGE":
        sortie("INFO : le diagnostic manuel est ORANGE : risques connus de l'hote, non bloquants pour le demarrage direct (lancer la commande les accepte).")
    sortie("Sur .%s (VM) - UNE SEULE commande, SANS armement :" % CLE)
    sortie("  sudo systemctl start helios-diskfill.service")
    if requise:
        sortie("  (ExecStartPre verifie localement ET consulte l'agent Windows authentifie ; il exige VERT strict et refuse tout ROUGE/ORANGE/agent absent")
        sortie("   sans rien allouer. La garde independante est demarree AVANT par la dependance de l'unite : ne jamais la lancer a la main.)")
    else:
        sortie("  (ExecStartPre execute les controles du noeud et REFUSE sans rien allouer si l'un echoue ; AUCUNE dependance de l'hote Windows, comme")
        sortie("   helios-cpuload.service. La garde independante est demarree AVANT par la dependance de l'unite : ne jamais la lancer a la main.)")
    sortie("Suivi temps reel (autre terminal sur .%s) :" % CLE)
    sortie("  journalctl -fu helios-diskfill -u helios-diskguard")
    sortie("Resultat final dans les journaux systemd (une ligne RESULTAT, apres la fin du lancement) :")
    sortie("  journalctl -u helios-diskfill -u helios-diskguard --since \"-30 min\" --no-pager | grep -E \"RESULTAT|BILAN|REFUS\"")
    sortie("  systemctl show helios-diskfill.service -p ActiveState -p Result -p ExecMainStatus")
    sortie("Abandon : sudo systemctl stop helios-diskfill.service   (libere aussitot, par noms exacts, les fichiers de CE lancement)")
    sortie("Preuve complete (hote Windows, lecture seule), apres le lancement :")
    sortie("  python C:\\Users\\houss\\helios-state\\disk-deploy-20261114\\outils\\preuves_injection_reelle.py --depuis \"AAAA-MM-JJ HH:MM:SS\"   "
           "(heure LOCALE du demarrage, UTC + 1 h)")


def principal(argv=None, deps=None, sortie=print, collecteur=None):
    mots = list(sys.argv[1:] if argv is None else argv)
    if "--armer" in mots:                                                           # obsolete : AUCUNE connexion, aucun depot, aucune lecture du noeud
        sortie(MESSAGE_ARMER_OBSOLETE)
        return 2
    ap = construire_parseur()
    a = ap.parse_args(argv)
    if a.sommes:
        sommes = ecrire_sommes()
        sortie("SHA256SUMS regenere (%d fichiers) :" % len(sommes))
        sortie(contenu_sommes(sommes).rstrip("\n"))
        return 0
    deps = deps or H.Dependances()
    collecteur = collecteur or collecter_defaut
    if a.statut:
        exe = deps.executeur()
        afficher_statut(lire_service(exe), sortie)
        return 0
    acquittes, erreur = H.analyser_acquittements(a.accepte_orange)
    if erreur:
        sortie("REFUS : %s" % erreur)
        return 2
    if H.CODE_REMEDIATION_NON_ACTIVEE in acquittes:
        sortie("REFUS : --accepte-orange %s n'est pas admis (la remediation par le Bridge est obligatoire : aucun lancement en detection seule)"
               % H.CODE_REMEDIATION_NON_ACTIVEE)
        return 2
    if not a.image_backend:
        sortie("REFUS : --image-backend TAG est obligatoire (image du Backend capable de DISK_E2E_158, comparee a l'image reellement deployee)")
        return 2
    fusion, faits, faits_porte, ctx, service = preflight_complet(a, acquittes, deps, collecteur, sortie)
    sortie("\nPre-vol manuel FACULTATIF (diagnostic seulement) termine : RIEN n'a ete ecrit ni depose sur .%s. Ce rapport manuel n'est pas reutilise ; ExecStartPre relance automatiquement le controle Windows authentifie a chaque demarrage." % CLE)
    sortie("Un pre-vol reussi N'EST PAS une preuve de remediation : seule l'execution du scenario reel la produit.")
    afficher_lancement_direct(sortie, fusion["verdict"])
    return 0 if fusion["verdict"] != "ROUGE" else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                                # noqa: BLE001
        pass
    sys.exit(principal())
