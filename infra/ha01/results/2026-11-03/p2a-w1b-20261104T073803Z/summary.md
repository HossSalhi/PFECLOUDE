# Répétition w1b — k8s-worker1

Fenêtre taint : 2026-11-04T07:39:42Z → 2026-11-04T07:49:44Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant la répétition (informatif ; mesures concernées classées hors gel) | ✅ | aucun |
| web >= 98 % et série d'échecs <= 10 s (hors gels) | ✅ | 100.0 % / 0.0 s ; brut 100.0 % / 0.0 s |
| api_app >= 98 % et série <= 10 s (hors gels) | ✅ | 100.0 % / 0.0 s ; brut 100.0 % / 0.0 s |
| kube_api >= 99 % et trou <= 15 s (hors gels) | ✅ | 100.0 % / 0.0 s ; brut 100.0 % / 0.0 s |
| canari ha01-canary-w2 : trou d'écriture <= 120 s (hors gels) | ✅ | 15.62 s (brut 15.62 s) |
| canari ha01-canary-w2 : 0 écriture acquittée perdue (jamais exemptée) | ✅ | 0 |
| MemAvailable survivant >= 300 Mo | ✅ | 939 Mo |
| MemAvailable .153 >= 500 Mo | ✅ | 616 Mo |
| RAM hôte >= 1500 Mo | ✅ | 3280 Mo |
| 0 OOMKilled | ✅ | 0 |
| 0 éviction pour mémoire | ✅ | 0 |
| pas d'abandon | ✅ | None |
| récupération complète <= 15 min | ✅ | 37 s |
