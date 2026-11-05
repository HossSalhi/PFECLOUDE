# Infrastructure réseau / HA / monitoring — HELIOS

Architecture mise en place suite aux remarques de l'encadrant technique (LB, Ingress, VIP, monitoring).
Le cluster est un **HA 3-masters** (kubeadm, stacked etcd) : `k8s-master (.153)`, `k8s-worker1 (.158)`,
`k8s-worker2 (.155)` sont **tous des control-plane nodes** (les noms « worker » sont trompeurs).

**Objectif HA de bout en bout** : la plateforme reste accessible avec n'importe quel nœud du cluster
(`.153`/`.155`/`.158`) seul en vie, **et** avec un seul des deux LB (`.160` ou `.162`) seul en vie.
Aucun composant n'est un SPOF : ni un nœud cluster, ni une machine de LB, ni `.160` (monitoring) prise
isolément.

> ⚠️ **Mise en garde (nov. 2026)** : les affirmations de juillet ci-dessus (« aucun composant n'est un SPOF : ni une machine de LB… », « un seul des deux LB en vie ») ont été **re-prouvées par arrêt réel de VM pour `.160`, `.155`, `.158` (4 nov.) et `.162` (5 nov., avec 4 critères ❌ prévus)**.
> La perte réelle de **`.162`** (devenu LB actif du DNS) coupe les clients DNS à cache 11 à 31 s et fait passer `.153` NotReady ≈ 10 s : voir la section suivante et `ha01/results/2026-11-03/GATE-4.md` (R9).

## État actuel après HA-01 (4 nov. 2026, complété le 5 nov. avec `.162`) — survivre à la perte d'UNE VM (`.160`, `.155`, `.158` ou `.162`)
> Cette section **prime** sur les sections suivantes (état de fin juillet) en cas de divergence. Détail, écarts E1-E19, runbooks, retours arrière : [`ha01/README-HA-01.md`](ha01/README-HA-01.md) ·
> verdict et réserves : `ha01/results/2026-11-03/GATE-4.md` · mesures de chaque arrêt réel : `ha01/results/2026-11-03/GATE-4-mesures.md` · 4e scénario `.162` : `ha01/results/2026-11-03/GATE-4-162.md`.

- **Entrée web** : HAProxy actif/actif sur `.160` + `.162`, configuration canonique **identique** [`ha01/haproxy/haproxy.cfg`](ha01/haproxy/haproxy.cfg), **sans VIP**. Le DNS (dnsmasq sur chaque LB + contrôleur
  `helios-dns-health`, [`ha01/dns/`](ha01/dns/)) ne publie que le LB sain : **`.162` est le LB actif**, `.160` le secours. Santé des serveurs web = `GET /ping` de Traefik (:8082), détection en ~4 s.
- **Contrôleur d'ingress** : **Traefik v3.7** en `hostNetwork` (:8000/:8443) sur `.155` et `.158` ([`ha01/k8s/traefik-shadow.yaml`](ha01/k8s/traefik-shadow.yaml)). **ingress-nginx** (retiré par l'amont) est conservé **inactif**,
  non décommissionné. ⚠️ Les délais d'un service du fournisseur Ingress NGINX se règlent par `--providers.kubernetesingressnginx.*` (connexion 1 s, 4 tentatives), **pas** par `--serversTransport.*` (sans effet, mesuré).
- **Frontend → backend** : nginx joint le Service headless `backend-headless` ([`ha01/k8s/`](ha01/k8s/)) avec connexion 1 s et reprise sur l'autre pod.
- **Base** : CloudNativePG 2 instances (réplication **asynchrone**), sonde de vivacité `failureThreshold 9`. Sauvegardes **hors VM** sur `.161` (timer 03:00 UTC, dump + snapshot etcd) et CronJob B sur `.155`.
  Après les essais, le primaire courant est `postgres-5` (`.155`) ; CNPG est symétrique.
- **Placement** : `.153` = control-plane seul (taint) ; deux réplicas (anti-affinité) sur `.155` et `.158`.
- **Preuves** (arrêts brutaux réels, mesurés depuis 3 points de vue) : `.160` **29/29**, `.155` **27/27** (3e essai, après deux échecs qui ont révélé et fait corriger 3 défauts réels), `.158` **26/27** (tous les critères de service tenus,
  reprise des écritures en 54 s, 0 écriture acquittée perdue), et **`.162`** (LB actif du DNS, 5 nov. 2026) **26/30** sans abandon : web/`api_app` par `hosts` 100 %, `.160` seul LB 100 %, 0 écriture perdue, etcd 4/4 en 1 s sans élection.
- **Perte réelle de `.162` — QUATRE arrêts réels au total, `.162` « VALIDÉ AVEC RÉSERVES » (verdict à confirmer)** : 4 ❌ **prévus et non corrigés** (Option A : DNS et `.153` laissés tels quels) — (a) clients DNS à cache coupés : `kube_api` par nom vu de Windows 31,1 s (> 15 s), `.161` avec cache `web`/`api_app` 13 s (> 10 s) ;
  (b) `.153` NotReady ≈ 10 s (sessions API persistantes du kubelet/kube-proxy gelées ~44 s sur `.162:6443`). Pire cas non rejoué : le leader etcd n'a pas été déplacé sur `.162`. Décisions restantes (DNS resserré, kubelet de `.153`, ingress-nginx) : `GATE-4.md` §5.
- **Limites assumées** : RPO ≈ lag (asynchrone) ; `.153` fragile (rafales CPU) et l'opérateur CNPG qui y est épinglé redémarre lors des gels d'E/S de l'hôte ; gels d'E/S du SSD de l'hôte (13 en ~42 h au 5 nov.) ; Kubernetes 1.29 hors support.

## Point d'entrée des applications — HAProxy actif/actif sur `.160` + `.162`, SANS VIP
- **nginx a été entièrement supprimé** (3 nœuds du cluster + `.160`) et remplacé par **HAProxy**, présent
  sur **deux** machines dédiées : `.160` (monitoring) et `.162` (`k8s-lb2`, VM créée pour ce seul rôle).
  Config identique sur les deux : `infra/haproxy-apps-lb.cfg` (frontend `apps_http` :80 mode http,
  `apps_https` :443 mode tcp — le TLS est terminé par l'ingress). Backend = les 3 NodePort ingress
  (`:31756` http / `:31411` https) des nœuds `.153/.155/.158`, health-check actif (`fall 3 rise 2`).
- **PAS de VIP, PAS de keepalived** : testés puis retirés sur demande explicite (« un VIP n'est pas une
  notion Kubernetes »). Les deux HAProxy tournent **en même temps** (actif/actif) ; le basculement se
  fait **côté client** en résolvant `helios.orange.tn` vers **les deux adresses** `.160` **et** `.162`
  (2 lignes dans le fichier `hosts`, ou 2 A-records DNS en prod) — le navigateur retente l'autre IP si
  l'une ne répond pas.
- **ingress-nginx** (manifeste `cloud` upstream, 2 répliques HA, `externalTrafficPolicy: Cluster`) + objet
  **Ingress** (`ingress-helios.yaml`) : `helios.orange.tn` → `frontend:80`. Le pod frontend proxifie
  `/api/` → `backend.pfe.svc:8080` (même origine) et `deny all` sur `/actuator/`.
- **MetalLB** (`metallb-pool.yaml`, pool `.200-210`) reste en place pour l'`EXTERNAL-IP` de l'ingress
  (`192.168.148.201`, joignable depuis l'hôte car même L2 sur VMnet8) mais **n'est plus le chemin
  principal** : HAProxy cible directement les NodePorts, pas cette IP MetalLB.

Chaîne complète : navigateur → **HAProxy `.160` OU `.162`** (actif/actif) → NodePort ingress d'un des
3 nœuds → ingress-nginx → `frontend` → `/api/` → `backend` → Postgres (CNPG).

**Preuve HA** : 30/30 requêtes en 200 avec n'importe quel nœud + les 2 LB up ; reprise automatique en
~2 min avec un seul nœud + un seul LB en vie (gardien de quorum etcd, cf ci-dessous). Voir la mémoire
`project_helios_lb_haproxy_vip` / `project_helios_etcd_5_membres` pour le détail des tests.

## HA du control-plane — apiserver local par nœud (sans VIP, sans dépendre de `.160`)
Le VIP keepalived `.150` a été **supprimé** (un VIP n'est pas une notion Kubernetes). Puis on a retiré
la dépendance à l'HAProxy `.160` pour les kubelets :
- **`kubelet.conf` de chaque nœud → son propre apiserver local** (`.153→.153:6443`, `.155→.155:6443`,
  `.158→.158:6443` ; backup `kubelet.conf.bak-160`). Chaque nœud est autonome : si `.160` tombe,
  **les 3 nœuds restent `Ready`**. Repointage fait **un nœud à la fois** (chaque IP est déjà dans le SAN
  du cert apiserver correspondant).
- **`~/.kube/config`** : master → `.153` (local, pour que la remédiation via SSH survive à `.160`) ;
  workers repointés depuis le VIP mort `.150` vers leur IP locale.
- **HAProxy `.160` ET `.162`** (`:6443` → 3 apiservers, stats `:8404`) : **conservé** comme endpoint HA
  optionnel (ex. kubectl externe), mais plus utilisé par les kubelets → **plus un SPOF**.
- `.160` figure dans les **SAN** des 3 certs apiserver (`.150/.153/.155/.158/.160`).
- **Bascule prouvée** : arrêt d'un apiserver → le cluster survit (quorum etcd 2/3), les autres nœuds `Ready`.

### etcd étendu à 5 membres (`.153`+`.155`+`.158`+`.160`+`.162`), `.161` retiré
`.160` et `.162` ont été ajoutés comme membres etcd **à part entière** (voix de vote), via
`member add --learner` puis `member promote` (jamais un ajout direct en votant — casse le quorum d'un
cluster à 1 membre). `.161` (IA/Camunda) a été **retiré** d'etcd : il n'a plus aucun rôle dans le
maintien du cluster. Un **gardien de quorum** (`helios-quorum-guard`, systemd sur les 3 nœuds,
`infra/helios-quorum-guard.py`) reconstruit automatiquement le cluster etcd sur un survivant unique
après confirmation de 5 garde-fous anti-split-brain (délai réduit à 90s car `.160`/`.162` agissent comme
témoins de confiance). **Prouvé** : n'importe quel nœud seul + les 2 LB, ou + un seul LB, redémarre le
cluster automatiquement (30/30 requêtes kubectl et HELIOS pendant l'incident).

## Monitoring 100% hors cluster (demande encadrant)
La stack (Prometheus, Grafana, Loki, node-exporter) tourne en **systemd sur la VM .160**, hors cluster.
- **kube-state-metrics externalisé** sur .160 (`kube-state-metrics.service`, binaire extrait de l'image v2.10.1,
  jeton lecture-seule du SA `kube-system/kube-state-metrics` via `ksm.kubeconfig` pointant sur `.160:6443`).
  Prometheus le scrape en `127.0.0.1:8080`. Le KSM in-cluster a été supprimé.
- **metrics-server RESTE** dans le cluster : c'est l'API cœur K8s `metrics.k8s.io` (APIService agrégée) requise
  par le HPA — **non externalisable**, ce n'est pas du monitoring.
- Résultat : **0 pod de monitoring dans le cluster**. Si `.160` tombe, la détection/monitoring est en pause
  (attendu : c'est la VM de monitoring), mais **la plateforme applicative continue de servir**.

## Accès
| Service | URL |
|---|---|
| Frontend HELIOS (nom) | http://helios.orange.tn/ (résout vers `.160` **et** `.162`, actif/actif) |
| API backend | via `/api/...` sur la même origine (proxifié par le frontend) |
| Grafana | http://192.168.148.160:3000 |
| Prometheus | http://192.168.148.160:9090 |
| API Kubernetes (kubelets) | apiserver local de chaque nœud (`.153/.155/.158:6443`) |
| API Kubernetes (HAProxy, optionnel) | https://192.168.148.160:6443 **ou** https://192.168.148.162:6443 |
| Stats HAProxy | http://192.168.148.160:8404 · http://192.168.148.162:8404 |
