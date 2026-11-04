# GATE-2b — VALIDÉ AVEC RÉSERVES le 4 novembre 2026, 08:50 UTC (09:50 heure locale)

Injections de service **sans éteindre de VM**, DNS à santé contrôlée en mode **enforce** (les deux LB, depuis 07:57 UTC), mesures sur **3 points de vue** (Windows ; `.161` sans cache DNS ;
`.161` avec cache DNS à TTL = comme `systemd-resolved` des VM), en **brut et hors gel d'E/S de l'hôte** (E6, détecteur en direct). Dossiers : `p2b-*-<étiquette>-*/`.

| Test | Critères | Lecture |
|---|---|---|
| `enf162`, `enf160` — passage en **enforce** | 5/5 chacun | **Incident corrigé** (07:52Z) : les sauvegardes `.bak-ha01-*` déposées dans `/etc/dnsmasq.d` empêchaient dnsmasq de redémarrer (« illegal repeated keyword ») — `.162` sans DNS ~1,5 min, sans effet client. Corrigé à la source (sauvegardes hors `conf-dir`, `systemd-helper checkconfig`) |
| `s162` — arrêt du service HAProxy de `.162` (300 s) | **25/25 réels** (23/25 affichés : 2 « contrôles d'injection » mal calibrés, corrigés) | web/API/API K8s 100 %, 0 s d'échec sur les 3 points de vue ; 0 écriture perdue ; retour en 7 s, DNS nominal en 14 s |
| `s160` — arrêt de HAProxy `.160`, **`.160` en tête du DNS** | **18/25 ❌** | bascule du contrôleur en 7 s mais **client coupé 8 s (sans cache), 17 s (cache à TTL), 25 s (cache DNS Windows)** → seuils ≤ 10 s / ≥ 99 % non tenus → **écart E12** |
| `s160b` — idem après **E12 (`.162` en tête)** | **25/25 ✅** | perte de `.160` **invisible** : web/API/API K8s 100 % / 0 s sur les 3 points de vue (y compris avec cache), 0 écriture perdue, aucune élection etcd, retour 7 s, DNS 15 s |
| `i160b` — **isolation réseau complète de `.160`** (240 s, simule la VM morte) | 23/25 | `.153` **reste Ready** (bail max 10 s) ; API K8s 100 % ; web/API 100 % (avec cache) et 99,67 % (sans cache, 1 s au début) ; 0 écriture perdue ; 0 élection ; retour 32 s, DNS nominal 26 s. **2 ❌ voir réserve** |

## Décision C4 : **GARDER `api.helios.orange.tn` pour le kubelet de `.153`**
Pendant l'isolation totale de `.160`, `.153` est resté **Ready** sans aucune transition (bail de nœud max 10,2 s sur un seuil de ~40 s) : l'API Kubernetes (via le nom) est restée à 100 % / 0 s sur les 3 points de vue.
Aucune modification de `kubelet.conf` n'est nécessaire.

## Décisions et écarts de cette phase
- **E12** — le LB actif du DNS devient `.162` (**décision de l'utilisateur : « faire le mieux »**, après présentation de 3 options chiffrées). Appliqué sans coupure, rejoué et validé (`s160b`).
- **B0** (référence en DNS statique) sautée (E11, décision de l'utilisateur de tout faire aujourd'hui).
- Correctifs d'outillage (E8) : contrôle d'injection fondé sur la plus longue série d'échecs ; envoi de fichiers par SSH en mode binaire (le mode texte de Python/Windows ajoutait des `\r` aux scripts et unités poussés).

## Réserve (consignée, non masquée)
`i160b` : **2 critères ❌ côté Windows** (`web` et `api_app` : 98,61 % au lieu de 99 % sur 144 échantillons, **série max 1,0 s ≤ 10 s ✓**). Origine : 4 échantillons `SSLEOFError` à l'instant EXACT du rétablissement
(08:48:37Z), pas pendant la panne. Pendant l'isolation, HAProxy de `.160` ne recevait plus les réponses de ses contrôles de santé et a marqué ses backends DOWN ; au retour du réseau il a accepté des
connexions ~4 s avant de les revalider, puis les a fermées. Seul le client qui a `.160` en **première adresse statique** (fichier `hosts` de Windows) est touché ; les clients DNS restent sur `.162` (E12).
Pistes (non appliquées) : retirer les 2 lignes `hosts` de Windows (D3) ; `tcp-request connection reject if { nbsrv(ingress_https) lt 1 }` dans HAProxy pour refuser plutôt qu'accepter-puis-fermer.
La VRAIE remise en service de `.160` (reboot de la VM, HAProxy repart avec ses backends « UP ») sera mesurée en Phase 4.

## Incident hors périmètre noté
Défaut d'outillage découvert au passage : fichiers poussés par `stdin` avec des `\r` (script de rétablissement, unité et script du contrôleur DNS) — normalisés en place sur `.160` et `.162` ; `ha01_ops.ssh()` envoie désormais en binaire.

**Suite : Phase 3** (Traefik : shadow, recette, bascule des ports `.162` puis `.160`, observation ~1 h sous charge comprimée — E11).
