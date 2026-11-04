# Bascule .162 : traefik -> traefik-host

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant l'observation (informatif, E6) | ✅ | aucun |
| config finale de .162 = ports de traefik-host | ✅ | {'http': 2, 'https': 2, 'check': 1} |
| HAProxy .162 : tous les serveurs UP | ✅ | [] |
| [Windows] lb162_web >= 99,9 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 79 ms / BRUT: 100.0 % / 0.0 s / p95 79 ms |
| [Windows] lb162_api >= 99,9 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 34 ms / BRUT: 100.0 % / 0.0 s / p95 34 ms |
| [Windows] web >= 99.0 % et série <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 77 ms / BRUT: 100.0 % / 0.0 s / p95 77 ms |
| [Windows] api_app >= 99.0 % et série <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 87 ms / BRUT: 100.0 % / 0.0 s / p95 87 ms |
| [Windows] kube_api >= 99.0 % et série <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 34 ms / BRUT: 100.0 % / 0.0 s / p95 34 ms |
| charge 20 req/s forcée vers .162 : >= 99,9 % de succès (hors gels), p95 < 1500 ms | ✅ | 3701/3701 p95=76 ms |
| mémoire : workers >= 300 Mo (minimum mesuré) | ✅ | {'155': 1144, '158': 697} |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 2986 |
| pas d'abandon (sinon config d'origine restaurée) | ✅ | None |
