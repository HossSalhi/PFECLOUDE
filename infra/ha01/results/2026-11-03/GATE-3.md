# GATE-3 — VALIDÉ AVEC RÉSERVE le 4 novembre 2026, 09:31 UTC (10:31 heure locale)

**Traefik v3.7.17** (provider « Ingress NGINX ») remplace ingress-nginx v1.11.3 (projet retiré par l'amont en mars 2026) dans le chemin applicatif des deux répartiteurs de charge.
L'Ingress existant `pfe/helios-ingress` est lu tel quel (aucune modification). **ingress-nginx n'est PAS décommissionné** : ses 2 pods tournent, inactifs, comme retour arrière.

| Étape | Résultat |
|---|---|
| Shadow `p3_shadow.py apply` (08:51Z) — NodePorts 32080/32443, 1 pod par worker | opérationnel en 139 s ; 0 erreur |
| Recette `p3_shadow.py accept` | **17/17 ✅** : statuts et corps identiques (`/` et `/api/test/health-readiness`, http et https, sur les 2 workers), **certificat TLS identique**, `ssl-redirect=false` respecté, hôte inconnu = 404 des deux côtés, **SSE sans buffering** (1er octet 0,04 s, connexion tenue 120 s), charge **30 req/s × 60 s = 1801/1801, p95 70 ms** |
| Bascule `.160` (09:01:45Z) `p3_switch.py` | **12/12 ✅** — 20 req/s forcés : **4901/4901**, p95 77 ms, serveurs UP en 2 s |
| Bascule `.162` (09:11:36Z, LB actif, trafic réel) | **12/12 ✅** — **6101/6101**, p95 78 ms, serveurs UP en 1 s |
| Répétition de drain de `.155` avec Traefik dans le chemin (`p3w2`) | **13/13 ✅** — web 100 %, API appli 99,83 % (1 s), API K8s 100 %, 0 écriture perdue, mémoire survivant 741 Mo, récupération 36 s |
| Santé de Traefik à 09:31Z | 0 redémarrage, **0 erreur** dans les journaux (30 min), cluster sain |

## Réserve (consignée)
- **La durée d'observation est de ~30 min** (09:01 → 09:31Z, dont 12 min de drain), pas 48 h : décision de l'utilisateur de tout faire aujourd'hui (E11). Les 3 arrêts réels de VM de la Phase 4, ~3 h
  de mesures supplémentaires dans le chemin Traefik, prolongent de fait cette observation. Une fuite mémoire ou un défaut lent de Traefik ne seraient pas visibles sur 30 min.
- **Décommission d'ingress-nginx : non faite, soumise à l'accord explicite de l'utilisateur** (retour arrière : `p3_switch.py --lb <160|162> --to nginx`, ~1 min, tant que ses pods tournent).
  Remarque mémoire : ses 2 pods occupent de la RAM sur les workers (retirer le retour arrière libérerait ~200 Mo).
- Incidents d'outillage (E8) : la recette comptait le corps `chunked` de Traefik avec ses marqueurs de blocs (+13 octets) → 4 faux ❌ ; corrigée (décodage `chunked`), rejouée : 17/17.
  Le préflight a aussi refusé, à raison, de démarrer juste après un gel d'E/S de l'hôte (09:05:32Z).

## Fichiers
`infra/ha01/haproxy/haproxy.cfg` (canonique) mis à jour avec les ports 32080/32443 — identique à la config en service (sha256 `4cd703f8…`).
Résultats : `p3-accept-20261104T085800Z.md`, `p3-switch-160-traefik-*/`, `p3-switch-162-traefik-*/`, `p2a-p3w2-*/`.

**Suite : Phase 4** — arrêts réels de VM (`.160`, puis `.155`, puis `.158`), **avec votre confirmation explicite avant chacun**.
