# Bascule .160 : nginx -> traefik

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant l'observation (informatif, E6) | ✅ | 11-04 09:05:24 -> 09:06:09 UTC; 11-04 09:05:33 -> 09:06:03 UTC |
| config finale de .160 = ports de traefik | ✅ | {'http': 2, 'https': 2, 'check': 1} |
| HAProxy .160 : tous les serveurs UP | ✅ | [] |
| [Windows] lb160_web >= 99,9 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 70 ms / BRUT: 100.0 % / 0.0 s / p95 77 ms |
| [Windows] lb160_api >= 99,9 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 27 ms / BRUT: 99.324 % / 10.3 s / p95 32 ms |
| [Windows] web >= 99.0 % et série <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 75 ms / BRUT: 100.0 % / 0.0 s / p95 81 ms |
| [Windows] api_app >= 99.0 % et série <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 84 ms / BRUT: 100.0 % / 0.0 s / p95 91 ms |
| [Windows] kube_api >= 99.0 % et série <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 36 ms / BRUT: 100.0 % / 0.0 s / p95 37 ms |
| charge 20 req/s forcée vers .160 : >= 99,9 % de succès (hors gels), p95 < 1500 ms | ✅ | 4901/4901 p95=77 ms |
| mémoire : workers >= 300 Mo (minimum mesuré) | ✅ | {'155': 876, '158': 882} |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 2967 |
| pas d'abandon (sinon config d'origine restaurée) | ✅ | None |
