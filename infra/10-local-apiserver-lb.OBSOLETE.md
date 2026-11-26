# `10-local-apiserver-lb.sh` — script retiré

Ce script installait un HAProxy local sur `127.0.0.1:7443` sur chaque nœud
K8s (pattern Kubespray / RKE2 / Talos) pour supprimer la dépendance des
kubelets à `.160`.

## Pourquoi il a été retiré

**Le problème qu'il résolvait était déjà résolu, et mieux.**

Chaque `kubelet.conf` pointe déjà vers l'API server **local de son propre
nœud** (`.153→.153:6443`, `.158→.158:6443`, `.155→.155:6443`) — voir
`infra/README.md`, section « HA du control-plane ».

Puisque les 3 nœuds sont eux-mêmes control-plane, chacun a un API server en
local. Ajouter un HAProxy devant serait un composant de plus à maintenir pour
le même résultat.

## Ce qu'il faut retenir

| Approche | Quand elle a du sens |
|---|---|
| **API server local direct** ✅ | Le nœud est lui-même control-plane — **notre cas** |
| **HAProxy local `127.0.0.1`** | Le nœud est un worker pur, sans API server local |

Le pattern HAProxy local reste pertinent si des workers purs sont ajoutés au
cluster plus tard : ils n'auraient pas d'API server local vers lequel pointer.

## Limite connue de l'approche actuelle

Si l'API server local d'un nœud meurt alors que le nœud reste vivant, le
kubelet perd l'accès à l'API et le nœud passe `NotReady` — un LB local aurait
basculé vers un autre API server.

En pratique ce cas est rare et le nœud est de toute façon dégradé. Le
compromis est raisonnable, mais mérite d'être mentionné en soutenance.
