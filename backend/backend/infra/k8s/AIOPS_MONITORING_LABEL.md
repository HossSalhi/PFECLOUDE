# Surveillance automatique de toutes les applications

## Comment ça marche

**Aucune configuration requise.** Tu déploies ton application → elle est surveillée automatiquement dans les 120 secondes.

Le système AIOps scanne tous les namespaces du cluster toutes les 2 minutes et surveille automatiquement tout Deployment trouvé.

## Namespaces exclus automatiquement (namespaces système)

| Namespace | Raison |
|-----------|--------|
| `kube-system` | Composants K8s internes |
| `kube-public` | Ressources publiques K8s |
| `kube-node-lease` | Heartbeat des nœuds |
| `cnpg-system` | Opérateur PostgreSQL |
| `cert-manager` | Gestion des certificats |
| `ingress-nginx` | Contrôleur ingress |
| `monitoring` | Stack Prometheus/Grafana |

Tout autre namespace est surveillé automatiquement.

## Ce qui est surveillé automatiquement

- CPU usage (pods de l'app)
- Mémoire working set
- Nombre de restarts
- Replicas unavailable
- Latence HTTP p95
- Ratio erreurs 5xx
- DB connection pool
- Logs Loki (erreurs, patterns crash, OOM)
- État des nœuds K8s (toujours surveillé, peu importe l'app)
- CPU/RAM/Disk des VMs (toujours surveillé, cluster-wide)

## Label optionnel — nom personnalisé

Si tu veux donner un nom spécifique à ton app dans les incidents (sinon le nom du Deployment est utilisé) :

```yaml
metadata:
  labels:
    aiops.io/app: "mon-app-nom"   # optionnel
```

## Apps actuellement surveillées

| Namespace | Ce qui est surveillé |
|-----------|---------------------|
| `pfe` | backend, ai-service, et tout autre Deployment |
| Tout autre namespace utilisateur | Automatiquement |
