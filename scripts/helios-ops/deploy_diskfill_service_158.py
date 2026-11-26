#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HELIOS - INSTALLATION, VERIFICATION et RETRAIT du service local DISK sur .158 (helios-diskfill.service + helios-diskguard.service).

    python deploy_diskfill_service_158.py --verifier                                          # lecture seule
    python deploy_diskfill_service_158.py --installer                                         # PLAN seulement (rien n'est execute)
    python deploy_diskfill_service_158.py --installer --confirm INSTALLER-DISKFILL-158        # ECRIT sur .158 : autorisation NOMINATIVE requise
    python deploy_diskfill_service_158.py --retirer --confirm RETIRER-DISKFILL-158            # retire les 3 fichiers installes

CE PROGRAMME N'A JAMAIS ETE EXECUTE CONTRE L'INFRASTRUCTURE au moment de sa creation : il est teste sur un noeud fictif (test_deploy_diskfill_service_158.py).

Garanties (verrouillees par test) :
  * JAMAIS `systemctl enable`, `start`, `restart`, `mask` : le service n'est ni active ni demarre ; un `daemon-reload` seulement.
  * Seuls TROIS fichiers sont ecrits : /opt/helios-diskfill/diskfill.py, /etc/systemd/system/helios-diskfill.service, /etc/systemd/system/helios-diskguard.service
    (plus le dossier /opt/helios-diskfill et un repertoire temporaire /tmp/helios-diskfill-install.* retire a la fin). Aucun joker, aucun -r.
  * Les octets envoyes sont les octets LF du depot (empreintes SHA256SUMS verifiees AVANT l'envoi, puis sur le noeud APRES l'envoi et apres l'installation).
  * Pre-controles de securite (ROUGE = refus) : identite de .158, depot et SHA256SUMS coherents, aucune unite du lanceur residuelle, repertoire de test
    absent ou vide, aucune unite du service active, outils presents (python3, fallocate, findmnt, systemd-analyze).
  * Apres installation : `systemd-analyze verify`, `daemon-reload`, puis verification des proprietes CHARGEES (FragmentPath, aucun drop-in, UnitFileState=static,
    StateDirectoryMode=0700 pour les deux unites, Restart=no) et `diskfill.py verifier --a-blanc` (aucun effet). Au moindre ecart : RETOUR ARRIERE (retrait des
    fichiers installes, restauration des copies de sauvegarde) et echec.
  * Les fichiers existants sont sauvegardes a cote (`<chemin>.AVANT-DISKFILL-<UTC>`) avant d'etre remplaces ; rien n'est jamais supprime d'autre.
Tout passe par l'executeur du lanceur (helios_disk_test.ExecReel) : ce module n'ouvre AUCUN sous-processus lui-meme.
"""
import argparse
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import helios_disk_test as H          # noqa: E402
import helios_diskfill_service as S   # noqa: E402

D = S.noeud_module()
IP = S.IP
NOM_K8S = S.NOM_K8S
UTILISATEUR = "kube"
RACINE_SCRIPT = os.path.dirname(D.CHEMIN_SCRIPT)                       # /opt/helios-diskfill
PREFIXE_TMP = "/tmp/helios-diskfill-install."
CONFIRM_INSTALLER = "INSTALLER-DISKFILL-158"
CONFIRM_RETIRER = "RETIRER-DISKFILL-158"
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no"]
OUTILS = ("/usr/bin/python3", "/usr/bin/fallocate", "/usr/bin/findmnt", "/usr/bin/systemd-analyze", "/usr/bin/systemctl")
PYTHON_MIN, SYSTEMD_MIN = (3, 8), 245
MODES = {"diskfill.py": "0644", "helios-diskfill.service": "0644", "helios-diskguard.service": "0644"}


def horodatage_utc(maintenant=None):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() if maintenant is None else maintenant))


def sudo(*argv):
    return ["sudo", "-n"] + list(argv)


def ssh_ok(exe, argv, delai=45):
    r = exe.ssh(IP, argv, delai)
    return r.rc == 0, (r.out or "").strip(), (r.err or "").strip()


# ------------------------------------------------------------------------------------------------ pre-controles (LECTURE SEULE)
def lire_prerequis(exe):
    """Faits du noeud avant toute ecriture. Une lecture impossible = None (jamais une valeur devinee)."""
    chemins = S.chemins_noeud()
    f = {}
    ok, out, _ = ssh_ok(exe, ["hostname"])
    f["hostname"] = H.nom_court(out) if ok and out else None
    ok, out, _ = ssh_ok(exe, ["python3", "--version"])
    m = re.match(r"Python (\d+)\.(\d+)", out) if ok else None
    f["python"] = (int(m.group(1)), int(m.group(2))) if m else None
    ok, out, _ = ssh_ok(exe, ["systemctl", "--version"])
    m = re.match(r"systemd (\d+)", out) if ok else None
    f["systemd"] = int(m.group(1)) if m else None
    f["outils"] = dict((o, exe.ssh(IP, ["test", "-x", o], 20).rc == 0) for o in OUTILS)
    ok, out, _ = ssh_ok(exe, ["systemctl", "list-units", "--all", "--no-legend", "--plain", "helios-diskfill-*"], 30)
    f["unites_transitoires"] = [l.split()[0] for l in out.splitlines() if l.strip()] if ok else None
    r = exe.ssh(IP, sudo("ls", "-A", "--", D.DOSSIER), 30)
    f["dossier"] = ([l.strip() for l in (r.out or "").splitlines() if l.strip()] if r.rc == 0
                    else (None if "No such file" in ((r.out or "") + (r.err or "")) else "illisible"))
    r = exe.ssh(IP, sudo("sha256sum", "--", *[chemins[n] for n in S.NOMS_SOT]), 30)
    lus = {}
    for ligne in (r.out or "").splitlines():
        m = re.fullmatch(r"([0-9a-f]{64})\s+(\S+)", ligne.strip())
        if m:
            lus[m.group(2)] = m.group(1)
    f["installes"] = dict((n, lus.get(chemins[n])) for n in S.NOMS_SOT)
    f["actives"] = {}
    for u in (D.UNITE_PRINCIPALE, D.UNITE_GARDE):
        ok, out, _ = ssh_ok(exe, ["systemctl", "is-active", u], 20)
        f["actives"][u] = out in ("active", "activating", "deactivating", "reloading") if out else None
    return f


def evaluer_prerequis(faits, sommes):
    """Pre-controles -> raisons (niveau, constat, code) ROUGE ou INFO. PURE."""
    r = []

    def add(niveau, constat, code=None):
        r.append((niveau, constat, code))
    if sommes.get("fichier") != sommes.get("calculees"):
        add("ROUGE", "SHA256SUMS du depot absent ou perime (python helios_diskfill_service.py --sommes apres relecture du diff)", "DEP_SOMMES")
    if faits.get("hostname") != NOM_K8S:
        add("ROUGE", "nom d'hote %r (attendu %r) : MAUVAISE MACHINE" % (faits.get("hostname"), NOM_K8S), "DEP_IDENTITE")
    else:
        add("INFO", "identite : nom d'hote %s, cible ssh %s" % (NOM_K8S, IP))
    py = faits.get("python")
    if py is None or py < PYTHON_MIN:
        add("ROUGE", "python3 %r < %r" % (py, PYTHON_MIN), "DEP_PYTHON")
    sd = faits.get("systemd")
    if sd is None or sd < SYSTEMD_MIN:
        add("ROUGE", "systemd %r < %d (StateDirectoryMode, RuntimeDirectoryPreserve)" % (sd, SYSTEMD_MIN), "DEP_SYSTEMD")
    for o, present in sorted((faits.get("outils") or {}).items()):
        if not present:
            add("ROUGE", "%s absent" % o, "DEP_OUTIL")
    if faits.get("unites_transitoires") != []:
        add("ROUGE", "unites transitoires du lanceur (helios-diskfill-*) presentes ou illisibles : %r" % (faits.get("unites_transitoires"),), "DEP_RESIDUS")
    d = faits.get("dossier")
    if d == "illisible":
        add("ROUGE", "%s illisible" % D.DOSSIER, "DEP_DOSSIER")
    elif d:
        add("ROUGE", "%s contient %d entree(s) : orphelins ; l'exploitant les traite d'abord" % (D.DOSSIER, len(d)), "DEP_ORPHELINS")
    for u, active in sorted((faits.get("actives") or {}).items()):
        if active is not False:
            add("ROUGE", "%s active ou etat illisible : aucune installation pendant un lancement" % u, "DEP_ACTIVE")
    deja = [n for n in S.NOMS_SOT if (faits.get("installes") or {}).get(n)]
    if deja:
        add("INFO", "deja installes : %s (seront sauvegardes puis remplaces si l'empreinte differe)" % ", ".join(deja))
    return r


# ------------------------------------------------------------------------------------------------ plan (pur)
def plan_installation(horodatage, repertoire_tmp="/tmp/helios-diskfill-install.XXXXXX"):
    """Commandes DISTANTES prevues, dans l'ordre (PURE : sert au plan a blanc et aux tests). Aucune ne contient enable / start / restart / mask."""
    chemins = S.chemins_noeud()
    plan = [("repertoire temporaire", ["mktemp", "-d", PREFIXE_TMP + "XXXXXX"]),
            ("repertoire du programme", sudo("install", "-d", "-m", "0755", "-o", "root", "-g", "root", "--", RACINE_SCRIPT))]
    for n in S.NOMS_SOT:
        plan.append(("installer %s" % n, sudo("install", "-m", MODES[n], "-o", "root", "-g", "root", "--", repertoire_tmp + "/" + n, chemins[n])))
    plan.append(("verification systemd", ["systemd-analyze", "verify", chemins["helios-diskfill.service"], chemins["helios-diskguard.service"]]))
    plan.append(("rechargement de systemd", sudo("systemctl", "daemon-reload")))
    return plan


# ------------------------------------------------------------------------------------------------ verification d'une installation
def verifier_installation(exe, sommes, sortie, a_blanc_du_noeud=True):
    """Verifie (LECTURE SEULE) l'installation : empreintes, proprietes CHARGEES des deux unites, `diskfill.py verifier --a-blanc`. Retourne (ok, raisons)."""
    service = S.lire_service(exe)
    raisons = S.evaluer_service(sommes, service, None)
    # ces controles de service portent sur l'etat de MARCHE (cooldown, lancement) : seuls les defauts d'INSTALLATION comptent ici
    codes_installation = ("SVC_SOMMES_DEPOT", "SVC_ILLISIBLE", "SVC_NON_INSTALLE", "SVC_ETAT_ILLISIBLE", "SVC_IDENTITE", "SVC_UNITE_ILLISIBLE", "SVC_UNITE",
                          "SVC_DOSSIER", "SVC_ORPHELINS", "SVC_RESIDUS_LANCEUR", "SVC_UNITE_ACTIVE") + tuple("SVC_SHA_" + re.sub(r"[^A-Z]", "_", n.upper()) for n in S.NOMS_SOT)
    defauts = [x for x in raisons if x[0] == "ROUGE" and x[3] in codes_installation]
    for niveau, constat, remede, code in raisons:
        if niveau == "INFO" or code in codes_installation:
            sortie("  [%s] %s" % (niveau, constat))
    ok = not defauts
    if a_blanc_du_noeud and ok:
        r = exe.ssh(IP, sudo("/usr/bin/python3", "-I", "-B", D.CHEMIN_SCRIPT, "verifier", "--a-blanc"), 90)
        sortie("  [%s] diskfill.py verifier --a-blanc (aucun effet) : rc=%s" % ("INFO" if r.rc == 0 else "ROUGE", r.rc))
        for l in (r.out or "").splitlines()[-4:]:
            sortie("       " + l)
        ok = ok and r.rc == 0
    return ok, defauts


# ------------------------------------------------------------------------------------------------ installation
def retour_arriere(exe, sauvegardes, installes, sortie):
    """Restaure les copies de sauvegarde ; retire UNIQUEMENT les fichiers que CETTE installation vient d'ecrire (noms exacts)."""
    chemins = S.chemins_noeud()
    for n in installes:
        if sauvegardes.get(n):
            r = exe.ssh(IP, sudo("cp", "-p", "--", sauvegardes[n], chemins[n]), 30)
            sortie("   retour arriere : restauration de %s depuis %s (rc=%s)" % (chemins[n], sauvegardes[n], r.rc))
        else:
            r = exe.ssh(IP, sudo("rm", "-f", "--", chemins[n]), 30)
            sortie("   retour arriere : retrait de %s (rc=%s)" % (chemins[n], r.rc))
    exe.ssh(IP, sudo("systemctl", "daemon-reload"), 60)


def nettoyer_tmp(exe, tmp):
    """Retire le repertoire temporaire : les 3 fichiers par nom exact puis rmdir (jamais -r)."""
    if tmp:
        exe.ssh(IP, sudo("rm", "-f", "--", *[tmp + "/" + n for n in S.NOMS_SOT]), 30)
        exe.ssh(IP, sudo("rmdir", "--", tmp), 30)


def installer(exe, confirmer, sortie=print, maintenant=None):
    """Installe le service sur .158 (ECRITURE). Sans --confirm exact : PLAN seulement. Retourne le code de sortie."""
    sommes = {"calculees": S.sommes_calculees(), "fichier": S.lire_sommes_fichier()}
    stamp = horodatage_utc(maintenant)
    sortie("PLAN D'INSTALLATION du service local DISK sur .%s (jamais enable, jamais start) :" % S.CLE)
    for titre, argv in plan_installation(stamp):
        sortie("   %-26s %s" % (titre, " ".join(argv)))
    sortie("   (puis : verification des empreintes, des proprietes chargees et de `diskfill.py verifier --a-blanc` ; retour arriere au moindre ecart)")
    if confirmer != CONFIRM_INSTALLER:
        sortie("\nPLAN SEULEMENT : rien n'a ete execute. Pour installer (autorisation nominative) : --installer --confirm %s" % CONFIRM_INSTALLER)
        return 0 if confirmer is None else 2
    faits = lire_prerequis(exe)
    raisons = evaluer_prerequis(faits, sommes)
    sortie("\nPRE-CONTROLES (lecture seule) :")
    for niveau, constat, code in raisons:
        sortie("  [%s] %s%s" % (niveau, constat, "  <%s>" % code if niveau == "ROUGE" else ""))
    if any(x[0] == "ROUGE" for x in raisons):
        sortie("\nREFUS : pre-controle ROUGE, rien n'a ete ecrit.")
        return 1
    chemins = S.chemins_noeud()
    ok, tmp, _ = ssh_ok(exe, ["mktemp", "-d", PREFIXE_TMP + "XXXXXX"])
    if not ok or not re.fullmatch(re.escape(PREFIXE_TMP) + r"[A-Za-z0-9]{6}", tmp):
        sortie("REFUS : repertoire temporaire impossible (%r) ; rien n'a ete ecrit hors de /tmp." % (tmp,))
        return 1
    sauvegardes, installes = {}, []
    try:
        with tempfile.TemporaryDirectory() as local:
            for n in S.NOMS_SOT:
                with open(os.path.join(local, n), "wb") as f:
                    f.write(S.lire_source(n))                              # octets LF du depot
            for n in S.NOMS_SOT:
                r = exe.local(["scp", "-q"] + SSH_OPTIONS + [os.path.join(local, n), "%s@%s:%s/%s" % (UTILISATEUR, IP, tmp, n)], 60)
                if r.rc != 0:
                    sortie("ECHEC : envoi de %s impossible (rc=%s) ; rien n'est installe." % (n, r.rc))
                    return 1
        r = exe.ssh(IP, sudo("sha256sum", "--", *[tmp + "/" + n for n in S.NOMS_SOT]), 30)
        envoyes = dict((os.path.basename(m.group(2)), m.group(1)) for m in re.finditer(r"([0-9a-f]{64})\s+(\S+)", r.out or ""))
        for n in S.NOMS_SOT:
            if envoyes.get(n) != sommes["calculees"][n]:
                sortie("ECHEC : %s envoye a l'empreinte %s... != %s... : rien n'est installe." % (n, str(envoyes.get(n))[:12], sommes["calculees"][n][:12]))
                return 1
        sortie("\nEmpreintes des fichiers envoyes : identiques au depot.")
        r = exe.ssh(IP, sudo("install", "-d", "-m", "0755", "-o", "root", "-g", "root", "--", RACINE_SCRIPT), 30)
        if r.rc != 0:
            sortie("ECHEC : creation de %s impossible (rc=%s)." % (RACINE_SCRIPT, r.rc))
            return 1
        for n in S.NOMS_SOT:
            if faits["installes"].get(n):
                sauvegardes[n] = "%s.AVANT-DISKFILL-%s" % (chemins[n], stamp)
                r = exe.ssh(IP, sudo("cp", "-p", "--", chemins[n], sauvegardes[n]), 30)
                if r.rc != 0:
                    sortie("ECHEC : sauvegarde de %s impossible (rc=%s) ; rien n'est remplace." % (chemins[n], r.rc))
                    return 1
            r = exe.ssh(IP, sudo("install", "-m", MODES[n], "-o", "root", "-g", "root", "--", tmp + "/" + n, chemins[n]), 30)
            installes.append(n)
            sortie("   installe %s (rc=%s)" % (chemins[n], r.rc))
            if r.rc != 0:
                sortie("ECHEC : installation de %s (rc=%s) : retour arriere." % (n, r.rc))
                retour_arriere(exe, sauvegardes, installes, sortie)
                return 1
        r = exe.ssh(IP, ["systemd-analyze", "verify", chemins["helios-diskfill.service"], chemins["helios-diskguard.service"]], 60)
        sortie("   systemd-analyze verify : rc=%s%s" % (r.rc, (" : " + (r.out or r.err or "").strip()[:300]) if (r.out or r.err) else ""))
        if r.rc != 0:
            sortie("ECHEC : systemd-analyze verify : retour arriere.")
            retour_arriere(exe, sauvegardes, installes, sortie)
            return 1
        r = exe.ssh(IP, sudo("systemctl", "daemon-reload"), 60)
        sortie("   systemctl daemon-reload : rc=%s" % r.rc)
        if r.rc != 0:
            retour_arriere(exe, sauvegardes, installes, sortie)
            return 1
        sortie("\nVERIFICATION de l'installation :")
        ok, defauts = verifier_installation(exe, sommes, sortie)
        if not ok:
            motifs = ", ".join(sorted(set(x[3] for x in defauts))) or "diskfill.py verifier --a-blanc"
            sortie("ECHEC : verification de l'installation (%s) : retour arriere." % motifs)
            retour_arriere(exe, sauvegardes, installes, sortie)
            return 1
    finally:
        nettoyer_tmp(exe, tmp)
    sortie("\nINSTALLATION VERIFIEE. Le service n'est NI active NI demarre.")
    if D.ATTESTATION_HOTE_REQUISE:
        sortie("PRE-REQUIS AVANT TOUT DEMARRAGE : installer et demarrer l'agent Windows DISK (install_helios_disk_host_agent.ps1).")
        sortie("ExecStartPre consulte cet agent authentifie et exige VERT strict, puis execute les controles locaux ; agent absent/ORANGE/ROUGE = REFUS sans allocation.")
        sortie("Quand les deux niveaux sont prets, UNE SEULE commande sur .158, sans armement :")
    else:
        sortie("Demarrage (decision distincte, autorisation nominative) : UNE SEULE commande directement sur .158, SANS armement et SANS dependance de l'hote")
        sortie("Windows, comme helios-cpuload.service ; ExecStartPre execute lui-meme les controles du noeud et refuse sans rien allouer si l'un echoue :")
    sortie("  sudo systemctl start helios-diskfill.service")
    sortie("Diagnostic manuel Windows facultatif (lecture seule) : python helios_diskfill_service.py --preflight --image-backend pfe-backend:0.2.165-disk-e2e")
    return 0


# ------------------------------------------------------------------------------------------------ retrait
def retirer(exe, confirmer, sortie=print):
    """Retire les trois fichiers installes (noms exacts). Refuse si une unite est active ou si le repertoire de test n'est pas vide."""
    sommes = {"calculees": S.sommes_calculees(), "fichier": S.lire_sommes_fichier()}
    faits = lire_prerequis(exe)
    refus = []
    if faits.get("hostname") != NOM_K8S:
        refus.append("identite : nom d'hote %r (attendu %r)" % (faits.get("hostname"), NOM_K8S))
    for u, active in sorted((faits.get("actives") or {}).items()):
        if active is not False:
            refus.append("%s active ou etat illisible" % u)
    if faits.get("dossier") == "illisible" or faits.get("dossier"):
        refus.append("%s illisible ou non vide (orphelins : nettoyage manuel d'abord)" % D.DOSSIER)
    presents = [S.chemins_noeud()[n] for n in S.NOMS_SOT if (faits.get("installes") or {}).get(n)]
    sortie("RETRAIT du service local DISK de .%s : fichiers presents : %s" % (S.CLE, ", ".join(presents) or "aucun"))
    for m in refus:
        sortie("  [ROUGE] %s" % m)
    if refus:
        sortie("REFUS : rien n'a ete retire.")
        return 1
    if confirmer != CONFIRM_RETIRER:
        sortie("PLAN SEULEMENT : rien n'a ete retire. Pour retirer (autorisation nominative) : --retirer --confirm %s" % CONFIRM_RETIRER)
        return 0 if confirmer is None else 2
    if presents:
        r = exe.ssh(IP, sudo("rm", "-f", "--", *presents), 30)
        sortie("  retrait des fichiers installes (noms exacts) : rc=%s" % r.rc)
    exe.ssh(IP, sudo("rmdir", "--", RACINE_SCRIPT), 30)                       # seulement s'il est vide
    r = exe.ssh(IP, sudo("systemctl", "daemon-reload"), 60)
    sortie("  systemctl daemon-reload : rc=%s" % r.rc)
    sortie("RETRAIT TERMINE. Les repertoires /var/lib/helios-diskfill* ne sont pas touches (donnees : l'exploitant decide).")
    return 0


# ------------------------------------------------------------------------------------------------ ligne de commande
def construire_parseur():
    ap = argparse.ArgumentParser(description="Installation du service local DISK sur .158. Rien n'est ecrit sans --confirm exact.", allow_abbrev=False)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--verifier", action="store_true", help="verifie l'installation (lecture seule)")
    g.add_argument("--installer", action="store_true", help="plan ; avec --confirm exact : installation")
    g.add_argument("--retirer", action="store_true", help="plan ; avec --confirm exact : retrait")
    ap.add_argument("--confirm", default=None)
    return ap


def principal(argv=None, deps=None, sortie=print, maintenant=None):
    a = construire_parseur().parse_args(argv)
    deps = deps or H.Dependances()
    exe = deps.executeur()
    if a.verifier:
        sommes = {"calculees": S.sommes_calculees(), "fichier": S.lire_sommes_fichier()}
        sortie("VERIFICATION de l'installation du service local DISK sur .%s (lecture seule)" % S.CLE)
        ok, defauts = verifier_installation(exe, sommes, sortie)
        sortie("RESULTAT : %s" % ("INSTALLATION CONFORME" if ok else "NON CONFORME (%s)" % ", ".join(sorted(set(x[3] for x in defauts)))))
        return 0 if ok else 1
    if a.installer:
        return installer(exe, a.confirm, sortie, maintenant)
    return retirer(exe, a.confirm, sortie)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                                # noqa: BLE001
        pass
    sys.exit(principal())
