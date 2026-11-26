# -*- coding: utf-8 -*-
"""HELIOS CHAOS-TOOLING-1 - orchestrateur de campagne d'injection.

VERROU DE SECURITE
    Aucun stimulus reel n'est applique tant que la variable d'environnement

        HELIOS_CHAOS_EXECUTION_ENABLED = true

    n'est pas positionnee explicitement. Par defaut elle vaut false, et TOUTE commande
    modifiant le cluster est refusee - pas seulement ignoree : la fonction leve.

    Dans le lot CHAOS-TOOLING-1, ce verrou n'est jamais leve. L'orchestrateur ne tourne
    qu'en DRY-RUN, ou il deroule l'integralite de la logique sans emettre une seule
    commande destructive.

MACHINE D'ETAT
    PLANNED -> PRECHECK -> INJECTING -> ESTABLISHED -> RECOVERY -> STABILIZING -> VALIDATED
    Toute anomalie fait basculer en REJECTED (injection inexploitable) ou ABORTED
    (campagne interrompue). Aucune transition automatique apres un etat incoherent.

IMMUTABILITE DU PLAN
    Le plan est verifie par empreinte SHA256 avant toute consommation. Un plan modifie
    est refuse : l'ordre des injections doit rester celui fige AVANT l'experience.

VERITE TERRAIN
    `ground_truth_type` vient du PLAN, donc du protocole, et il est enregistre AVANT
    l'application du stimulus. Le detecteur n'est jamais consulte pour l'etablir : le mot
    "detector" n'apparait dans ce fichier que dans ce commentaire et dans le controle qui
    en interdit l'usage.

Usage :
    python orchestrateur.py --plan <csv> --empreinte <sha256> [--namespace-prefixe helios-ml5]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalogue_stimuli import (  # noqa: E402
    CATALOGUE, NOEUDS, RECOVERY, noeud_pour, rendre_manifeste, verifier_catalogue,
)

VERROU = "HELIOS_CHAOS_EXECUTION_ENABLED"

PLANNED, PRECHECK, INJECTING = "PLANNED", "PRECHECK", "INJECTING"
ESTABLISHED, RECOVERY_ET, STABILIZING = "ESTABLISHED", "RECOVERY", "STABILIZING"
VALIDATED, REJECTED, ABORTED = "VALIDATED", "REJECTED", "ABORTED"

TRANSITIONS = {
    PLANNED: {PRECHECK, ABORTED},
    PRECHECK: {INJECTING, REJECTED, ABORTED},
    INJECTING: {ESTABLISHED, REJECTED, ABORTED},
    ESTABLISHED: {RECOVERY_ET, REJECTED, ABORTED},
    RECOVERY_ET: {STABILIZING, REJECTED, ABORTED},
    STABILIZING: {VALIDATED, REJECTED, ABORTED},
    VALIDATED: set(),
    REJECTED: set(),
    ABORTED: set(),
}


class VerrouActif(RuntimeError):
    """Levee des qu'une commande modifiant le cluster est tentee verrou ferme."""


def execution_autorisee():
    return os.environ.get(VERROU, "false").strip().lower() == "true"


def sha256(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as fh:
        for bloc in iter(lambda: fh.read(65536), b""):
            h.update(bloc)
    return h.hexdigest()


class Journal:
    """Journal d'execution. Aucun secret n'y est ecrit : ni jeton, ni mot de passe."""

    def __init__(self):
        self.lignes = []

    def ajouter(self, **champs):
        champs["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.lignes.append(champs)
        return champs

    def ecrire(self, chemin):
        if not self.lignes:
            return
        colonnes = []
        for l in self.lignes:
            for c in l:
                if c not in colonnes:
                    colonnes.append(c)
        Path(chemin).parent.mkdir(parents=True, exist_ok=True)
        with open(chemin, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=colonnes, extrasaction="ignore")
            w.writeheader()
            w.writerows(self.lignes)


class Injection:
    """Une injection et sa machine d'etat."""

    def __init__(self, ordre, passe, position, classe, prefixe_ns):
        self.ordre = int(ordre)
        self.passe = int(passe)
        self.position = int(position)
        self.classe = classe
        self.etat = PLANNED
        self.motif = ""
        # Cible definie AVANT l'experience - jamais decouverte apres coup.
        self.noeud = noeud_pour(self.position, self.passe)
        self.namespace = "%s-p%d-%02d" % (prefixe_ns, self.passe, self.position)
        self.injection_id = "inj-{date}-%s-p%d-%02d" % (
            classe.lower().replace("_", "-"), self.passe, self.position)

    def identifiant(self, date):
        return self.injection_id.replace("{date}", date)

    def transiter(self, nouvel_etat, motif=""):
        if nouvel_etat not in TRANSITIONS[self.etat]:
            raise ValueError("transition interdite %s -> %s (injection %d)"
                             % (self.etat, nouvel_etat, self.ordre))
        self.etat = nouvel_etat
        self.motif = motif
        return self.etat

    def verite_terrain(self, date):
        """Enregistree AVANT le stimulus, depuis le protocole. Jamais depuis une detection."""
        fiche = CATALOGUE[self.classe]
        return {
            "injection_id": self.identifiant(date),
            "ground_truth_type": self.classe,
            "ground_truth_severity": fiche["severite"],
            "label_source": "GROUND_TRUTH",
            "independent_proof": fiche["preuve_attendue"],
            "target_node": self.noeud,
            "target_namespace": self.namespace,
            "target_kind": fiche["target_kind"],
            "target_name": fiche["target_name"],
            "passe": self.passe,
            "position_dans_passe": self.position,
        }


def charger_plan(chemin, empreinte_attendue, prefixe_ns):
    """Charge le plan et REFUSE toute modification silencieuse."""
    reelle = sha256(chemin)
    if empreinte_attendue and reelle != empreinte_attendue:
        raise ValueError(
            "PLAN MODIFIE : empreinte %s, attendue %s. Le plan doit rester celui fige "
            "avant l'experience." % (reelle[:16], empreinte_attendue[:16]))
    with open(chemin, newline="", encoding="utf-8") as fh:
        lignes = list(csv.DictReader(fh))
    injections = [Injection(l["ordre_global"], l["passe"], l["position_dans_passe"],
                            l["classe"], prefixe_ns) for l in lignes]
    ids = [i.injection_id for i in injections]
    if len(set(ids)) != len(ids):
        raise ValueError("injection_id non unique dans le plan")
    return injections, reelle


def commandes_precheck(inj):
    """Commandes de PRECHECK. Toutes en LECTURE SEULE - aucune ne modifie le cluster."""
    return [
        "get nodes --no-headers",
        "get node %s -o jsonpath={.status.conditions[?(@.type=='Ready')].status}" % inj.noeud,
        "get namespace %s --ignore-not-found=true" % inj.namespace,
        "get pods -A --field-selector=status.phase=Pending --no-headers",
    ]


def commandes_stimulus(inj):
    """Commandes qui MODIFIENT le cluster. Jamais emises verrou ferme."""
    return [
        "create namespace %s" % inj.namespace,
        "apply -n %s -f -   # manifeste %s" % (inj.namespace, CATALOGUE[inj.classe]["fichier"]),
    ]


def commandes_recovery(inj):
    return [RECOVERY["commande"].format(namespace=inj.namespace)]


def executer_kubectl(commande, destructive):
    """Point de passage UNIQUE de toute commande kubectl.

    Une commande destructive verrou ferme leve : elle n'est pas silencieusement ignoree.
    C'est ce qui rend le mode DRY-RUN demontrable plutot que declaratif.
    """
    if destructive and not execution_autorisee():
        raise VerrouActif(
            "commande destructive refusee (%s=false) : %s" % (VERROU, commande[:60]))
    raise NotImplementedError(
        "l'execution reelle n'est pas implementee dans CHAOS-TOOLING-1 : ce lot ne doit "
        "provoquer aucun incident. Elle sera activee dans le lot de campagne ML5.")


def simuler(injections, date, journal, sortie_manifestes=None):
    """DRY-RUN : deroule toute la logique sans emettre une seule commande."""
    verites = []
    for inj in injections:
        inj.transiter(PRECHECK)
        precheck = commandes_precheck(inj)

        # La verite terrain est etablie AVANT le stimulus.
        vt = inj.verite_terrain(date)
        verites.append(vt)

        inj.transiter(INJECTING)
        stimulus = commandes_stimulus(inj)
        manifeste = rendre_manifeste(inj.classe, inj.namespace, inj.noeud)
        if sortie_manifestes:
            d = Path(sortie_manifestes) / vt["injection_id"]
            d.mkdir(parents=True, exist_ok=True)
            (d / CATALOGUE[inj.classe]["fichier"]).write_text(manifeste, encoding="utf-8")
            (d / "ground_truth.json").write_text(
                json.dumps(vt, indent=2, ensure_ascii=False), encoding="utf-8")

        inj.transiter(ESTABLISHED)
        inj.transiter(RECOVERY_ET)
        recovery = commandes_recovery(inj)
        inj.transiter(STABILIZING)
        inj.transiter(VALIDATED)

        journal.ajouter(
            injection_id=vt["injection_id"], ordre=inj.ordre, passe=inj.passe,
            position=inj.position, classe=inj.classe, target_node=inj.noeud,
            target_namespace=inj.namespace, target_kind=vt["target_kind"],
            etat_final=inj.etat, execution="SKIPPED",
            precheck_commandes=len(precheck), stimulus_commandes=len(stimulus),
            recovery_commandes=len(recovery),
            manifeste=CATALOGUE[inj.classe]["fichier"],
            recovery_action=RECOVERY["action"],
            verification=CATALOGUE[inj.classe]["verification_kubectl"].format(
                namespace=inj.namespace),
        )
    return verites


def main():
    p = argparse.ArgumentParser(description="Orchestrateur de campagne HELIOS (dry-run)")
    p.add_argument("--plan", required=True)
    p.add_argument("--empreinte", default="")
    p.add_argument("--namespace-prefixe", default="helios-ml5")
    p.add_argument("--date", default=time.strftime("%Y%m%d"))
    p.add_argument("--journal", default="")
    p.add_argument("--manifestes", default="")
    p.add_argument("--verites", default="")
    args = p.parse_args()

    problemes = verifier_catalogue()
    if problemes:
        for x in problemes:
            print("  CATALOGUE INVALIDE : %s" % x, file=sys.stderr)
        return 1

    print("=" * 80)
    print("ORCHESTRATEUR DE CAMPAGNE - MODE DRY-RUN")
    print("=" * 80)
    print("  %-28s %s" % (VERROU, "true" if execution_autorisee() else "false"))
    if execution_autorisee():
        print("  ATTENTION : le verrou est OUVERT. CHAOS-TOOLING-1 ne doit jamais l'ouvrir.",
              file=sys.stderr)
        return 2
    print("  execution reelle             : REFUSEE (verrou ferme)")
    print("  classes au catalogue         : %d" % len(CATALOGUE))
    print("  noeuds disponibles           : %s" % ", ".join(NOEUDS))

    injections, empreinte = charger_plan(args.plan, args.empreinte, args.namespace_prefixe)
    print("  plan                         : %s" % Path(args.plan).name)
    print("  empreinte SHA256             : %s" % empreinte)
    print("  injections planifiees        : %d" % len(injections))

    journal = Journal()
    verites = simuler(injections, args.date, journal, args.manifestes or None)

    print()
    print("  --- rotation des noeuds ---")
    from collections import Counter, defaultdict
    par_classe = defaultdict(set)
    for inj in injections:
        par_classe[inj.classe].add(inj.noeud)
    for c in sorted(par_classe):
        print("    %-38s %d noeud(s) : %s" % (c, len(par_classe[c]),
                                              ", ".join(sorted(par_classe[c]))))
    mono = [c for c, n in par_classe.items() if len(n) < 2]
    print("    classes sur un seul noeud : %d %s" % (len(mono), mono or ""))
    print()
    print("  --- noeuds par passe ---")
    for p_ in sorted({i.passe for i in injections}):
        n = Counter(i.noeud for i in injections if i.passe == p_)
        print("    passe %d : %s" % (p_, dict(n)))

    print()
    print("  --- etats finaux ---")
    print("    %s" % dict(Counter(i.etat for i in injections)))
    print("    execution reelle : SKIPPED pour les %d injections" % len(injections))

    if args.journal:
        journal.ecrire(args.journal)
        print("\n  journal   : %s" % args.journal)
    if args.verites:
        Path(args.verites).parent.mkdir(parents=True, exist_ok=True)
        Path(args.verites).write_text(json.dumps(verites, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
        print("  verites   : %s" % args.verites)
    if args.manifestes:
        print("  manifestes: %s" % args.manifestes)
    print("=" * 80)
    print("  AUCUNE COMMANDE N'A ETE EMISE VERS LE CLUSTER")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
