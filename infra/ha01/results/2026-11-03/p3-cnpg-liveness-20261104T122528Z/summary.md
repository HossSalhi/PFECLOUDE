# CNPG : liveness failureThreshold 9

| Critère | Résultat | Mesure |
|---|---|---|
| seuil de vivacité = 9 sur les 2 instances, cluster sain 2/2, primaire d'origine postgres-7 | ✅ | {'primaire': ('postgres-7', 'k8s-worker1'), 'seuils': {'postgres-5': (9, 'k8s-worker2', 'replica'), 'postgres-7': (9, 'k8s-worker1', 'primary')}} |
| canari ha01-canary-w1 : 0 écriture acquittée perdue pendant l'opération | ✅ | perdues=0 acquittées=486 trou max hors gel=7.3s (brut 7.3s) |
| canari ha01-canary-w1 : trou d'écriture <= 120 s | ✅ | 7.3 s |
| canari ha01-canary-w2 : 0 écriture acquittée perdue pendant l'opération | ✅ | perdues=0 acquittées=482 trou max hors gel=7.7s (brut 7.7s) |
| canari ha01-canary-w2 : trou d'écriture <= 120 s | ✅ | 7.7 s |
