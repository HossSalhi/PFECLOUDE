# Répétition p3w2 — k8s-worker2

Fenêtre taint : 2026-11-04T09:18:07Z → 2026-11-04T09:28:09Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant la répétition (informatif ; mesures concernées classées hors gel) | ✅ | aucun |
| web >= 98 % et série d'échecs <= 10 s (hors gels) | ✅ | 100.0 % / 0.0 s ; brut 100.0 % / 0.0 s |
| api_app >= 98 % et série <= 10 s (hors gels) | ✅ | 99.834 % / 1.0 s ; brut 99.834 % / 1.0 s |
| kube_api >= 99 % et trou <= 15 s (hors gels) | ✅ | 100.0 % / 0.0 s ; brut 100.0 % / 0.0 s |
| canari ha01-canary-w1 : trou d'écriture <= 120 s (hors gels) | ✅ | 0.33 s (brut 0.33 s) |
| canari ha01-canary-w1 : 0 écriture acquittée perdue (jamais exemptée) | ✅ | 0 |
| MemAvailable survivant >= 300 Mo | ✅ | 741 Mo |
| MemAvailable .153 >= 500 Mo | ✅ | 606 Mo |
| RAM hôte >= 1500 Mo | ✅ | 2517 Mo |
| 0 OOMKilled | ✅ | 0 |
| 0 éviction pour mémoire | ✅ | 0 |
| pas d'abandon | ✅ | None |
| récupération complète <= 15 min | ✅ | 36 s |
