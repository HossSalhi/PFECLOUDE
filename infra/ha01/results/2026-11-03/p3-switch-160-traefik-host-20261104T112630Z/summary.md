# Bascule .160 : traefik -> traefik-host

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant l'observation (informatif, E6) | ✅ | aucun |
| config finale de .160 = ports de traefik-host | ✅ | {'http': 2, 'https': 2, 'check': 1} |
| HAProxy .160 : tous les serveurs UP | ✅ | [] |
| [Windows] lb160_web >= 99,9 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 66 ms / BRUT: 100.0 % / 0.0 s / p95 66 ms |
| [Windows] lb160_api >= 99,9 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 27 ms / BRUT: 100.0 % / 0.0 s / p95 27 ms |
| [Windows] web >= 99.0 % et série <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 74 ms / BRUT: 100.0 % / 0.0 s / p95 74 ms |
| [Windows] api_app >= 99.0 % et série <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 79 ms / BRUT: 100.0 % / 0.0 s / p95 79 ms |
| [Windows] kube_api >= 99.0 % et série <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 28 ms / BRUT: 100.0 % / 0.0 s / p95 28 ms |
| charge 20 req/s forcée vers .160 : >= 99,9 % de succès (hors gels), p95 < 1500 ms | ✅ | 3701/3701 p95=75 ms |
| mémoire : workers >= 300 Mo (minimum mesuré) | ✅ | {'155': 1211, '158': 708} |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 2854 |
| pas d'abandon (sinon config d'origine restaurée) | ✅ | None |
