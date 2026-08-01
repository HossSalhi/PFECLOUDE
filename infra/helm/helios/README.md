# Chart Helm — HELIOS

Packaging Helm de l'application HELIOS (backend Spring Boot + frontend React) pour le cluster
Kubernetes HA. Remplace le déploiement « à la main » par `kubectl apply` de fichiers séparés :
tout est paramétré dans `values.yaml` et déployable/évolutif en une commande.

## Ce que le chart déploie
| Ressource | Détail |
|---|---|
| `Deployment/backend` | image + répliques + probes + resources + securityContext + **anti-affinité** + nodeSelector |
| `Service/backend` (+ `backend-nodeport`) | ClusterIP `:8080` (+ NodePort `30808`) |
| `HorizontalPodAutoscaler/backend-hpa` | min/max + cibles CPU/mémoire |
| `Deployment/frontend` | image + répliques + probes + resources + securityContext |
| `Service/frontend` | NodePort `:80 → 30880` |
| `Ingress/helios-ingress` | `helios.orange.tn` → frontend, TLS (`helios-tls`), `ssl-redirect: false` |

## Géré HORS chart (volontaire)
- **`backend-secret`** (JWT, mots de passe…) — un secret ne doit **jamais** être versionné dans un chart.
  Le créer avec `scripts/generate-production-secrets.sh`.
- **`backend-config`** (ConfigMap) — appliqué depuis `backend/backend/infra/k8s/backend-configmap.yaml`.
- Le `Secret/helios-tls` (certificat) et l'opérateur CNPG (base de données).

## Utilisation
```bash
# rendu local (verif, sans rien appliquer)
helm template helios ./infra/helm/helios

# validation
helm lint ./infra/helm/helios

# installation / mise a jour
helm upgrade --install helios ./infra/helm/helios -n pfe

# rollback en une commande
helm rollback helios -n pfe

# surcharge par environnement
helm upgrade --install helios ./infra/helm/helios -n pfe -f values-prod.yaml
```

## Étape suivante (GitOps)
Ce chart est prêt pour **ArgoCD** : une `Application` ArgoCD pointant sur `infra/helm/helios`
synchroniserait automatiquement le cluster sur le dépôt Git (déploiement déclaratif, rollback `git revert`).
