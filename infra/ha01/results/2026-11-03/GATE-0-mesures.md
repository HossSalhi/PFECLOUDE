# GATE-0 — mesures (2026-11-03 22:40:28 UTC)

| Groupe | Précondition | Résultat | Mesure |
|---|---|---|---|
| 1 | Nœuds Kubernetes Ready 3/3 | ✅ | ['k8s-master', 'k8s-worker1', 'k8s-worker2'] |
| 1 | etcd 5/5 membres sains | ✅ | 5/5 |
| 1 | CNPG sain 2/2 | ✅ | Cluster in healthy state/postgres-7/2 |
| 1 | 0 pod non sain (hors Completed) | ✅ | aucun |
| 1 | HAProxy .160 et .162 : tous serveurs UP | ✅ |  |
| 1 | .153 stable >= 2 h : HAProxy lastchg apiserver >= 7200 s sur les 2 LB | ✅ | {'160': 19096, '162': 19095} |
| 1 | .153 : 0 transition Ready sur 2 h (Prometheus) | ✅ | 0.0 |
| 1 | .153 : etcd et apiserver sans redémarrage depuis 2 h | ✅ | aucun |
| 2 | Jeu de sauvegardes hors VM (.161) : sommes vérifiées | ✅ | 18 OK |
| 2 | Snapshots etcd ha01-pre sur les 3 nœuds (> 20 Mo) | ✅ | 3/3 |
| 2 | Export YAML des objets à modifier (dépôt) | ✅ | results/2026-11-03/pre/ |
| 2 | Configs non secrètes sauvegardées (HAProxy, dnsmasq, manifests) | ✅ | 5 |
| 2 | Test de restauration du dump frais (16 tables, 11 659 lignes identiques) | ✅ | fait le 2026-11-03 (base jetable ha01_verify, supprimée) |
| 3 | RAM hôte disponible >= 2000 Mo sur toute la fenêtre de 10 min (>= 3 mesures) | ✅ | min=2668 max=2901 n=52 |
| 3 | RAM hôte jamais < 600 Mo sur 10 min | ✅ | 2668 |
| 4 | Images préchargées sur .155 (traefik v3.7.17 + metrics-server v0.8.1) | ✅ | 2 |
| 4 | Images préchargées sur .158 (traefik v3.7.17 + metrics-server v0.8.1) | ✅ | 2 |
| 5 | Sondes web/API/LB : fenêtre récente de 30 min sans échec | ✅ | 30.1 min, 7 sondes, 0 échecs (historique complet: 86 échecs sur 30064 s/sonde) |
| 5 | Canari DB : fenêtre récente de 30 min, 0 échec, 0 écriture acquittée perdue | ✅ | 30.2 min, acquittées=8822, échecs=0, perdues=0, trou max=2.83s (historique: échecs=8, perdues=0) |
| 5 | Échantillonneur actif sur la dernière heure (aucun trou > 90 s) | ✅ | n=331, trou max=40s |
| 6 | Automatismes HELIOS inventoriés (agent :9999, watchdog :9099, Bridge, backend) + D7 consigné | ✅ | helios-automatismes-P0.txt |

**Verdict automatique : VALIDÉ**
