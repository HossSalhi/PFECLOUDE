#!/usr/bin/env python3
"""
HA-01 — génère results/2026-11-03/GATE-4-162.md : rapport DISTINCT du 4e scénario réel (perte brutale de .162, LB actif du DNS).

  python gate4_162_report.py

- SANS essai exécuté (aucun dossier p4-vmstop162-*/summary.json) : le rapport est un rapport de PRÉPARATION (statut « AUCUNE EXTINCTION EFFECTUÉE ») : préflight PASS/FAIL,
  prévisions du modèle DNS, risques, seuils d'abandon, commandes exactes, conséquences.
- AVEC un essai exécuté : il y ajoute les RÉSULTATS BRUTS (chaque critère PASS/FAIL avec sa mesure, mesures de bascule DNS par type de client, sondes par point de vue, canaris, etcd,
  retour, stabilisation) et liste les fichiers bruts. Rien n'est arrondi ni recalculé : les valeurs viennent de summary.json / dnswatch-analysis.json.
Le fichier est RÉÉCRIT à chaque exécution (les preuves brutes restent dans les dossiers p4-vmstop162-*).
"""
import glob
import json
import os
import sys
import time

import ha01_ops as O
import dns_flip_model as M

OUT = os.path.join(O.RES, "GATE-4-162.md")
VMX = r"C:\Users\houss\Documents\Virtual Machines\k8s-lb2\k8s-lb2.vmx"
# Ajout du 4 nov. 2026 (reprise après la coupure de courant du PC) : arrêt système de l'hôte Windows. TOUT préflight antérieur est PÉRIMÉ pour toute autorisation d'arrêt.
COUPURE_HOTE_UTC = "2026-11-04T17:57:51Z"

RISQUES = [
    ("R1 — Coupure DNS côté clients (conception actif/passif : UNE adresse publiée, TTL 30 s)",
     "Le contrôleur survivant (.160) ne bascule qu'après FALL=3 échecs de sonde (expiration 2 s, cycle 3 s) : 8 à 11 s. Un client à cache TTL (Windows pour `api.helios.orange.tn`, CoreDNS, resolved) garde la réponse périmée "
     "jusqu'à 30 s de plus (jusqu'à ~41 s au pire). Le fichier `hosts` de Windows liste .160 AVANT .162 : `web` et `api_app` de Windows ne sont PAS touchés.",
     "Critères du plan probablement NON tenus pour les clients DNS : `kube_api` par nom (série <= 15 s) côté Windows ; `web`/`api_app` côté .161 sans cache (série <= 10 s : médiane modélisée 10,1 s) et avec cache (jusqu'à ~41 s).",
     "Mesure honnête (ce que fait le test tel quel) OU, AVANT l'essai et sur décision de l'utilisateur, amélioration chiffrée par le modèle : TTL 5 s + sondes 1 s/1 s + FALL 3 -> coupure max 9,1 s "
     "(`dns_flip_model.py --what-if`). Non appliquée : elle exige de recharger/redémarrer dnsmasq et de modifier le contrôleur."),
    ("R2 — Connexions API persistantes passant par .162 (kubelet de .153, kube-proxy x3)",
     "4 sessions HTTP/2 établies sur .162:6443 (2 depuis .153, 1 depuis .155, 1 depuis .158) seront gelées SANS RST à l'arrêt brutal. Détection par PING HTTP/2 (~30 + 15 s) puis reconnexion par nom (-> .160 après la bascule). "
     "Le bail de nœud de .153 (40 s) peut être dépassé : `.153` peut passer NotReady quelques secondes. JAMAIS mesuré avec le LB ACTIF mort (l'isolation de .160 en GATE-2b portait sur le LB non actif).",
     "Critère « aucune transition NotReady de .153 » probablement ❌ ; pods de .153 (opérateur CNPG) NotReady quelques secondes ; aucune éviction avant 300 s ; abandon seulement si NotReady > 2 min.",
     "Option (hors consigne actuelle : `.153` interdite) : pointer le kubelet de .153 vers son apiserver local comme .155/.158, ou raccourcir HTTP2_READ_IDLE/PING. À décider par l'utilisateur."),
    ("R3 — .160 devient l'UNIQUE LB pendant ~11 min et héberge aussi Prometheus/Grafana/Loki/détecteur HELIOS",
     "Capacité vérifiée aujourd'hui : 20 req/s dirigés vers .160 seul = 100 % (p95 75 ms), API Kubernetes authentifiée 30/30 via .160, CPU de .160 <= 38 %, MemAvailable stable ~620 Mo.",
     "Un gel d'E/S de l'hôte (7 en ~30 h) pendant l'essai = coupure totale du web, sans second LB.", "Le préflight exige l'absence de gel dans les 10 dernières minutes ; abandon si .160 en direct échoue > 25 s."),
    ("R4 — etcd : leader transféré sur .162 puis tué",
     "Ré-élection attendue en 1 à 3 s, quorum 4/5. .162 n'a qu'1 vCPU : le transfert n'est exécuté que si .162 est votante, synchrone, fsync p99 < 50 ms (aujourd'hui 3 ms).",
     "Pic de latence d'API de quelques secondes (déjà vécu et mesuré 100 % lors de l'essai .155 n°3, leader tué).", "Transfert réalisé par `p5_move_leader.py --execute` juste avant, avec vérification."),
    ("R5 — Arrêt brutal de .162 : risque faible de corruption du disque invité",
     "Les services de .162 (HAProxy, dnsmasq, contrôleur DNS, membre etcd) sont reconstructibles par les scripts du dépôt ; le membre etcd peut être retiré/ré-ajouté (`member remove` puis `member add --learner`, jamais d'ajout direct votant).",
     "Retour lent si le disque doit être réparé (fsck).", "Sauvegardes indépendantes fraîches sur .161 avant l'arrêt ; relance automatique ; aucune donnée applicative ne vit sur .162."),
    ("R6 — Relance de .162 et RAM/E-S de l'hôte",
     "2 relances de VM sur 5 ont été suivies d'un gel d'E/S de l'hôte dans la minute ; RAM hôte tendue (1,7 à 1,9 Go sans libération). VM de 1,5 Go.",
     "Gel de 30 à 45 s pendant le retour (l'opérateur CNPG redémarre alors, cf. E17).", "Libération non destructive de RAM à t-9 et t-4 min ; relance seulement si RAM >= 2000 Mo ; stabilisation de 30 min."),
    ("R7 — Fail-back du DNS vers .162 au retour",
     "Le DNS republie .162 après RISE=5 succès ET HOLD_DOWN=60 s (~75 s après la santé de .162) ; HAProxy qui reprend accepte ~4 s avant de revalider ses backends (« SSLEOF » déjà vu).",
     "Nouvelle micro-coupure possible au fail-back.", "Mesurée (champ `failback`) ; la stabilisation de 30 min la couvre."),
    ("R8 — Réactions HELIOS",
     "Le détecteur n'a pas de règle « VM injoignable » ; seule `ETCD_LEADER_FLAPPING` (> 5 changements/h) pourrait réagir (0 changement sur 1 h aujourd'hui ; l'essai en ajoute <= 3). Agent hôte HELIOS suspendu pendant l'essai (D7).",
     "Notification éventuelle, aucune action automatique.", "Journal du Bridge archivé dans le dossier d'essai."),
]

SEUILS = [
    "quorum etcd menacé : moins de 3 membres sains sur les 4 attendus, sur 2 cycles de 5 s consécutifs",
    "etcd à moins de 4 membres sains pendant plus de 90 s (après T+40 s)",
    "MemAvailable de .153 < 250 Mo, d'un worker survivant (.155/.158) < 250 Mo, **du LB survivant .160 < 250 Mo (nouveau)**",
    "RAM hôte < 600 Mo",
    "sonde par nom vue de Windows en échec depuis plus de 60 s (`kube_api` inclus, attendu <= ~41 s)",
    "**`web` ou `api_app` de Windows (fichier hosts, .160 d'abord) en échec depuis plus de 25 s (nouveau) : ils ne doivent pas être touchés**",
    "**LB survivant .160 en direct (web ou API) en échec depuis plus de 25 s (nouveau)**",
    "nœud inattendu (.153, .155, .158) NotReady depuis plus de 2 min",
    "canari survivant sans accusé récent depuis plus de 180 s",
    "AVANT l'arrêt (aucune extinction) : préflight générique KO · préflight spécifique `p5_162_preflight.py` non tout-PASS ou de plus de 30 min · filet `vm_revive.ps1` non armé · pause de l'agent HELIOS non effective · "
    "confirmation absente, de plus de 6 h ou invalide au dernier moment",
    "Action en cas d'abandon : relance IMMÉDIATE de .162 (`vmrun start ... nogui`), puis mesures de retour et stabilisation ; `finally` : relance d'urgence si la VM est restée éteinte ; filet Windows indépendant à observe+900 s",
]

COMMANDES = [
    ("0. Confirmation (SEULEMENT après la réponse écrite de l'utilisateur)", "Écrire `results\\2026-11-03\\CONFIRMATION-162.txt` (ligne `VM: 162`, date, action, conséquences, réponse VERBATIM). Sans ce fichier de moins de 6 h ET `--confirmed`, `p4_vmstop.py` refuse (code 3)."),
    ("1. RAM hôte (t-9 min et t-4 min)", "`powershell -File infra\\ha01\\ops\\trim_host_ram.ps1`  (EmptyWorkingSet des processus non-VMware ; non destructif)"),
    ("2. Sauvegarde indépendante fraîche", "`ssh kube@192.168.148.161 \"sudo -n systemctl start helios-offsite-backup.service\"` puis vérifier : `gzip -t` du dump, `sha256sum -c` du snapshot etcd, journal « terminé rc=0 »"),
    ("3. Leader etcd vers .162 (pire cas)", "`python infra\\ha01\\ops\\p5_move_leader.py --to 162 --execute`  (équivaut à `etcdctl --endpoints=https://192.168.148.<leader>:2379 ... move-leader 24c7bfc179503c82`, exécuté sur .158)"),
    ("4. Préflight", "`python infra\\ha01\\ops\\p5_162_preflight.py` (doit être tout PASS) puis `python infra\\ha01\\ops\\p4_vmstop.py --vm 162 --label stop162 --dry-run`"),
    ("5. Lancement détaché", "`powershell -File infra\\ha01\\ops\\start_detached.ps1 -Script p4_vmstop.py -Label stop162 --vm 162 --label stop162 --confirmed --observe 600 --stabilize 1800`"),
    ("6. Séquence automatisée par p4_vmstop.py", "pause de l'agent HELIOS -> filet `vm_revive.ps1` (1500 s) -> sondes (.161 avec cache, bascule DNS : Windows, dnsmasq direct 5 Hz, nœud .158) -> référence 40 s -> "
     "`vmrun -T ws stop \"" + VMX + "\" hard` -> observation 600 s -> `vmrun -T ws start \"" + VMX + "\" nogui` -> mesures de retour -> stabilisation 1800 s -> rapport"),
    ("7. Rapport", "`python infra\\ha01\\ops\\gate4_162_report.py`  ->  `results\\2026-11-03\\GATE-4-162.md`"),
    ("8. Reprise manuelle (si besoin)", "`vmrun -T ws start \"" + VMX + "\" nogui`  puis `python infra\\ha01\\ops\\p5_162_preflight.py --no-capacity --no-revive-test`"),
]

CONSEQUENCES = [
    "**.162 (k8s-lb2) est éteinte brutalement** ~11 min : HAProxy, dnsmasq, contrôleur DNS et membre etcd `helios-etcd-162` indisponibles ; aucune donnée applicative n'y réside.",
    "**.160 devient l'unique répartiteur de charge** (web, API Kubernetes) et l'unique serveur DNS vivant ; etcd à 4/5 (quorum tenu) ; leader etcd tué (ré-élection 1-3 s).",
    "**Clients par fichier hosts (Windows : web, api_app)** : aucun effet attendu. **Clients par DNS** : coupure de 8 à ~41 s selon le cache (R1).",
    "**Connexions API persistantes via .162** (kubelet et kube-proxy de .153, kube-proxy de .155 et .158) gelées ~45 s ; `.153` peut passer NotReady quelques secondes (R2).",
    "**PostgreSQL, workers, Traefik, applications** : non concernés (aucun pod sur .162) ; aucune perte de données attendue.",
    "**HELIOS** : agent hôte suspendu (D7) ; notification possible ; aucune action automatique ; supervision de .162 perdue puis revenue.",
    "**Retour** : relance automatique après 600 s ; SSH ~50 s ; etcd 5/5 ~1 min ; le DNS redevient nominal (.162 publiée) ~75 s après la santé du LB ; stabilisation 30 min.",
]


def latest(pattern):
    fs = sorted(glob.glob(os.path.join(O.RES, pattern)))
    return fs[-1] if fs else None


def historique_preflights(t_stop=None):
    """Encart « Historique des préflights » (ajout du 4 nov. 2026, reprise après la coupure de courant du PC). LECTURE SEULE : ne modifie aucun JSON.
    Liste TOUS les p5-162-preflight-*.json (conservés), recalcule leurs décomptes depuis les `checks` et les compare à l'en-tête, marque PÉRIMÉS pour toute
    autorisation d'arrêt ceux qui précèdent la coupure Windows, et classe les WARN du préflight courant (liste fermée acceptée pendant la préparation).
    `t_stop` (ajout du 5 nov. 2026, après l'essai réel de .162) : instant de l'arrêt ; le préflight de référence est alors le dernier antérieur à l'arrêt (celui qui a
    autorisé le lancement), son âge est calculé À L'ARRÊT et non à l'instant de la génération du rapport ; les préflights postérieurs sont marqués informatifs."""
    coupure = O.epoch_of(COUPURE_HOTE_UTC)
    files = sorted(glob.glob(os.path.join(O.RES, "p5-162-preflight-*.json")))
    last = files[-1] if files else None
    if t_stop:
        avant = []
        for f in files:
            try:
                if json.load(open(f, encoding="utf-8")).get("ts", 0) <= t_stop:
                    avant.append(f)
            except (OSError, ValueError):
                continue
        last = avant[-1] if avant else None
    L = ["## 1bis. Historique des préflights et règle WARN / bloquant", ""]
    L.append("Coupure de l'hôte Windows (arrêt système) : **%s**. **Tout préflight antérieur à cet instant est PÉRIMÉ pour toute autorisation d'arrêt de `.162`** ; les fichiers sont conservés. "
             "Un préflight n'est utilisable que s'il est **postérieur à la coupure, sans aucun FAIL et de moins de 30 minutes** au moment du lancement (`recent_p5_preflight`, logique inchangée)." % COUPURE_HOTE_UTC)
    L.append("")
    L.append("| Fichier | Horodatage (UTC) | PASS / WARN / FAIL recalculés | En-tête du JSON | Statut pour une autorisation d'arrêt |")
    L.append("|---|---|---|---|---|")
    cur = None
    for f in files:
        try:
            j = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            L.append("| `%s` | illisible | - | - | **ILLISIBLE** |" % os.path.basename(f))
            continue
        cnt = {"PASS": 0, "WARN": 0, "FAIL": 0}
        for r in j.get("checks", []):
            cnt[r.get("status")] = cnt.get(r.get("status"), 0) + 1
        hdr = (j.get("pass"), j.get("warn"), j.get("fail"))
        coh = hdr == (cnt["PASS"], cnt["WARN"], cnt["FAIL"])
        age = (time.time() - j.get("ts", 0)) / 60
        if j.get("ts", 0) < coupure:
            st = "**PÉRIMÉ — antérieur à la coupure Windows de %s** (conservé)" % COUPURE_HOTE_UTC
        elif t_stop and j.get("ts", 0) > t_stop:
            st = "postérieur à l'arrêt réel (informatif)"
        elif f == last and t_stop:
            a_stop = (t_stop - j.get("ts", 0)) / 60
            st = "**UTILISÉ pour l'arrêt réel du %s UTC** (âge à l'arrêt : %.0f min%s)" % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t_stop)), a_stop, " < 30 min ✓" if a_stop <= 30 else " > 30 min ✗")
            cur = j
        elif f == last:
            st = "**COURANT** (âge %.0f min%s)" % (age, "" if age <= 30 else " : plus de 30 min, à refaire avant tout lancement")
            cur = j
        else:
            st = "ancien (postérieur à la coupure, remplacé par un plus récent), conservé"
        L.append("| `%s` | %s | %d / %d / %d | %s / %s / %s %s | %s |" % (os.path.basename(f), j.get("iso", "?"), cnt["PASS"], cnt["WARN"], cnt["FAIL"],
                                                                    hdr[0], hdr[1], hdr[2], "✓ cohérent" if coh else "✗ INCOHÉRENT", st))
    L.append("")
    L.append("**Règle WARN temporairement accepté / critère bloquant** (la logique de validation n'est pas modifiée : `all_ok` = aucun FAIL ; aucun statut n'est forcé) :")
    L.append("- **FAIL = bloquant** : ni test de charge ni autorisation d'arrêt.")
    L.append("- **WARN accepté pendant cette préparation (liste fermée)** : uniquement « dump PostgreSQL sur `.161` <= 120 min » et « snapshot etcd sur `.161` <= 120 min », quand seul l'âge est en cause "
             "(intégrité, rétention et espace libre en PASS). Leur renouvellement est réservé à la préparation immédiate de l'arrêt réel.")
    L.append("- **Tout autre WARN = bloquant.**")
    L.append("- **Obligatoire juste avant un arrêt réel** : renouveler les sauvegardes (`ssh kube@192.168.148.161 \"sudo -n systemctl start helios-offsite-backup.service\"`) puis vérifier leur intégrité "
             "(`gzip -t` du dump, `sha256sum -c` du snapshot, journal « terminé rc=0 ») ; les deux lignes d'âge doivent alors être PASS. Aucune sauvegarde n'est supprimée (6 fichiers par type, rotation seulement au-delà de 30).")
    if cur is not None:
        chk = cur.get("checks", [])
        warns = [r for r in chk if r.get("status") == "WARN"]
        integ = [r for r in chk if r.get("check", "").startswith(("intégrité des derniers fichiers", "rétention :", "espace libre sur .161"))]
        integ_ok = bool(integ) and all(r.get("status") == "PASS" for r in integ)
        acc = [r for r in warns if integ_ok and r.get("check", "").startswith(("dump PostgreSQL sur .161", "snapshot etcd sur .161"))]
        autres = [r for r in warns if r not in acc]
        L.append("")
        L.append("Classement des WARN du préflight courant : **%d accepté(s)** (liste fermée) · **%d non prévu(s) = bloquant(s)**." % (len(acc), len(autres)))
        for r in acc:
            L.append("- accepté : %s — détail : %s" % (r["check"], str(r.get("detail", "")).replace("|", "/")))
        for r in autres:
            L.append("- **BLOQUANT (WARN non prévu)** : %s — détail : %s" % (r["check"], str(r.get("detail", "")).replace("|", "/")))
    L.append("")
    L.append("Actions d'écriture de la reprise du 4 nov. (de mesure ou d'évidence uniquement ; aucune sur le cluster) : relance du harnais de mesure Windows (`start_harness.ps1 -Label baseline-p0`, journaux en append), "
             "recréation de l'unité transitoire de mesure `ha01-probe-161` sur `.161` (journal en append), dossier de preuves `results/2026-11-03/reprise-post-coupure-*` (copies horodatées, instantanés, journaux de relevé et de supervision). "
             "Aucun arrêt ni redémarrage de service, aucune extinction, aucun déplacement du leader etcd, aucun changement DNS/HAProxy/Kubernetes/PostgreSQL.")
    L.append("")
    return L


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    pre_f = latest("p5-162-preflight-*.json")
    pre = json.load(open(pre_f, encoding="utf-8")) if pre_f else None
    run_s = latest("p4-vmstop162-*/summary.json")
    summ = json.load(open(run_s, encoding="utf-8")) if run_s else None
    model = M.run(20000)
    wif = M.what_if(6000)
    L = []
    executed = bool(summ and summ.get("criteria"))
    L.append("# GATE-4-162 — 4e scénario réel : perte brutale de la VM `.162` (k8s-lb2, LB ACTIF du DNS)")
    L.append("")
    if not executed:
        L.append("**STATUT : PRÉPARÉ — AUCUNE EXTINCTION EFFECTUÉE.** Ce document est le rapport de préparation ; il sera réécrit avec les résultats bruts après l'essai. "
                 "**L'extinction de `.162` exige une confirmation écrite spécifique de l'utilisateur** (fichier `CONFIRMATION-162.txt` + `--confirmed`) ; sans elle, `p4_vmstop.py` refuse.")
    else:
        ok_n = sum(1 for c in summ["criteria"] if c["ok"])
        L.append("**STATUT : EXÉCUTÉ le %s UTC — %d/%d critères PASS%s.**" % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(summ.get("t_stop", 0))), ok_n, len(summ["criteria"]),
                                                                           " — ABANDON : %s" % summ["abort"] if summ.get("abort") else ""))
    L.append("")
    obj = ("Objectif : mesurer si HELIOS reste disponible après la perte complète de `.162`, qui est le LB actif du DNS (`HELIOS_DNS_ORDER=162,160`, une seule adresse publiée, TTL 30 s). "
           "Les trois autres pertes réelles (`.160` 29/29, `.155` 27/27, `.158` 26/27) sont dans `GATE-4.md`. ")
    if executed:
        obj += ("**Essai exécuté : les critères en FAIL sont publiés tels quels (mesures brutes au §8, liste et lecture au §9) ; leur interprétation (R1 et R2 prévus, Option A : DNS inchangé, `.153` inchangée) "
                "et la comparaison avec les trois autres essais sont dans `GATE-4.md`. Les §3 à §6 sont le texte de PRÉPARATION, conservé tel quel : les écarts à l'exécution sont notés au §5.**")
    else:
        obj += "**Trois VM validées avant ce scénario ; ce document ne valide rien tant que l'essai n'est pas exécuté.**"
    L.append(obj)
    L.append("")
    L.append("## 1. Préflight (lecture seule) — PASS/FAIL")
    if pre:
        L.append("Exécuté le %s UTC par `ops/p5_162_preflight.py` (fichier `%s`) : **%d PASS · %d WARN · %d FAIL**. FAIL = condition à lever avant l'essai ; WARN = information à lire." % (pre["iso"], os.path.basename(pre_f), pre["pass"], pre["warn"], pre["fail"]))
        L.append("")
        L.append("| Section | Contrôle | Résultat | Détail |")
        L.append("|---|---|---|---|")
        for r in pre["checks"]:
            L.append("| %s | %s | **%s** | %s |" % (r["section"], r["check"], r["status"], r["detail"].replace("|", "/")[:200]))
    else:
        L.append("_Aucun préflight enregistré : lancer `python ops/p5_162_preflight.py`._")
    L.append("")
    L.extend(historique_preflights(summ.get("t_stop") if executed else None))
    L.append("## 2. Prévision du modèle DNS (hors ligne, contrôleur réel importé)")
    c = model["constantes"]
    L.append("Constantes lues dans `dns/helios-dns-health.py` : ordre %s, FALL=%s, période=%s s, expiration de sonde=%s s, RISE=%s, HOLD_DOWN=%s s, TTL dnsmasq=%s s." % (c["ORDER"], c["FALL"], c["PERIOD_s"], c["TIMEOUT_sonde_s"], c["RISE"], c["HOLD_DOWN_s"], c["TTL_dnsmasq_s"]))
    L.append("")
    L.append("| Type de client | min | médiane | p95 | max |")
    L.append("|---|---|---|---|---|")
    for k, lab in (("bascule_controleur_s", "Écriture du hostsdir par le contrôleur survivant (.160)"), ("client_sans_cache_s", "Client SANS cache (résout à chaque connexion)"),
                   ("client_cache_TTL_s", "Client à CACHE TTL 30 s (Windows `api.helios`, CoreDNS, resolved)"), ("client_hosts_statique_s", "Client à fichier hosts statique (.160 d'abord)")):
        v = model[k]
        L.append("| %s | %.1f s | %.1f s | %.1f s | %.1f s |" % (lab, v["min"], v["median"], v["p95"], v["max"]))
    L.append("")
    L.append("Clients à cache coupés plus de 10 s : **%.1f %%** ; plus de 30 s : **%.1f %%**. Concorde avec la mesure de l'écart E12 (8 s sans cache, 17 s avec cache TTL, 25 s avec le cache de Windows)." % (model["part_des_clients_cache_au_dela_de_10s_pct"], model["part_des_clients_cache_au_dela_de_30s_pct"]))
    L.append("")
    L.append("**Options chiffrées par le modèle (NON appliquées)** :")
    L.append("")
    L.append("| Réglage | bascule (méd.) | sans cache (méd. / max) | cache TTL (méd. / p95 / max) | cache > 10 s |")
    L.append("|---|---|---|---|---|")
    for lab, f, nc, cc, p10 in wif:
        L.append("| %s | %.1f s | %.1f / %.1f s | %.1f / %.1f / %.1f s | %.1f %% |" % (lab, f["median"], nc["median"], nc["max"], cc["median"], cc["p95"], cc["max"], p10))
    L.append("")
    L.append("## 3. Risques identifiés")
    for titre, desc, cons, mit in RISQUES:
        # sous-liste (et non « deux espaces en fin de ligne » pour forcer le retour à la ligne) : même rendu, sans espaces de fin de ligne (git diff --check propre)
        L.append("- **%s**\n  - _Cause_ : %s\n  - _Conséquence attendue_ : %s\n  - _Parade / décision_ : %s" % (titre, desc, cons, mit))
    L.append("")
    L.append("## 4. Seuils d'abandon (relance immédiate de `.162`)")
    for s in SEUILS:
        L.append("- " + s)
    L.append("")
    L.append("## 5. Commandes exactes (après confirmation écrite uniquement)")
    if executed:
        L.append("**Écarts entre cette liste (préparation du 4 nov.) et la séquence réellement exécutée le 5 nov. 2026 (« plan final de lancement », décisions de l'utilisateur : Option A, `.153` inchangée, "
                 "leader etcd non déplacé, aucune libération artificielle de RAM) :** les étapes **1** (libération de RAM) et **3** (leader etcd vers `.162`) n'ont **PAS** été exécutées ; "
                 "le leader est resté `.153` (terme 1820 -> 1820), donc le pire cas « LB actif et leader etcd perdus ensemble » n'a **pas** été rejoué pour `.162` (il l'a été pour `.155`, essai n°3). "
                 "Les étapes 0, 2, 4, 5, 6 et 7 l'ont été, avec en plus des portes de départ : RAM hôte >= 2300 Mo sur 3 mesures et 10 min, >= 30 min sans gel d'E/S, sauvegardes `.161` renouvelées puis vérifiées, "
                 "préflight spécifique exécuté sous supervision externe. Preuves : `lancement-162-20261105T070734Z/` et `reprise-post-coupure-20261104T212635Z/`.")
    for t, cmd in COMMANDES:
        L.append("- **%s** : %s" % (t, cmd))
    L.append("")
    L.append("## 6. Conséquences de l'arrêt de `.162`")
    for s in CONSEQUENCES:
        L.append("- " + s)
    L.append("")
    L.append("## 7. Garde-fous absolus de cette préparation (respectés)")
    L.append("Aucun `vmrun stop`, aucun arrêt de service (HAProxy, dnsmasq, etcd…), aucune coupure réseau, aucun VIP/Keepalived/VRRP, `.153` non modifiée, `Incident.java` et BPMN intacts, aucune suppression, aucun commit, aucun push. "
             "Seules actions d'écriture : fichiers d'outillage dans `infra/ha01/`, résultats du préflight, et deux tests du filet de relance (VM en marche : « tourne déjà », puis désarmement).")
    L.append("")
    if executed:
        L.append("## 8. RÉSULTATS BRUTS DE L'ESSAI — critères PASS/FAIL")
        L.append("Dossier : `%s`" % os.path.basename(os.path.dirname(run_s)))
        L.append("")
        L.append("| Critère | PASS/FAIL | Mesure brute |")
        L.append("|---|---|---|")
        for cr in summ["criteria"]:
            L.append("| %s | %s | %s |" % (cr["c"], "**PASS**" if cr["ok"] else "**FAIL**", str(cr["v"]).replace("|", "/")[:420]))
        L.append("")
        da = os.path.join(os.path.dirname(run_s), "dnswatch-analysis.json")
        if os.path.exists(da):
            L.append("### Bascule DNS — mesures brutes (`dnswatch-analysis.json`)")
            L.append("```json")
            L.append(json.dumps(json.load(open(da, encoding="utf-8")), ensure_ascii=False, indent=1))
            L.append("```")
            L.append("")
        sm = os.path.join(os.path.dirname(run_s), "summary.md")
        if os.path.exists(sm):
            txt = open(sm, encoding="utf-8").read()
            i = txt.find("## Bascule DNS")
            L.append("### Détail par point de vue, chronologie, retour (extrait de `summary.md`)")
            L.append(txt[i:] if i >= 0 else txt)
        L.append("")
        L.append("### Fichiers bruts de l'essai")
        for f in sorted(os.listdir(os.path.dirname(run_s))):
            L.append("- `%s` (%d o)" % (f, os.path.getsize(os.path.join(os.path.dirname(run_s), f))))
        fails = [cr for cr in summ["criteria"] if not cr["ok"]]
        L.append("")
        L.append("## 9. Lecture des résultats")
        L.append("**%d critère(s) sur %d en PASS ; %d en FAIL** (mesures brutes au §8, rien n'est arrondi ni recalculé) :" % (ok_n, len(summ["criteria"]), len(fails)))
        for cr in fails:
            L.append("- %s — %s" % (cr["c"], str(cr["v"]).replace("|", "/")[:200]))
        L.append("")
        L.append("Aucun seuil n'a été relâché ni aucun critère retiré pour cet essai ; les FAIL sont publiés tels quels. Interprétation (lien avec R1 et R2), décisions qui restent à l'utilisateur "
                 "et comparaison avec les essais `.160`, `.155` et `.158` : `GATE-4.md` (§1 à §5).")
    else:
        L.append("## 8. Résultats de l'essai")
        L.append("_Non exécuté._ Après l'essai, relancer `python ops/gate4_162_report.py` : ce chapitre contiendra tous les résultats bruts et chaque critère PASS/FAIL.")
    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L).rstrip("\n") + "\n")
    print("écrit :", OUT, "(%d lignes)" % len(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
