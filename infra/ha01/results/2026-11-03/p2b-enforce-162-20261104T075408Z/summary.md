# Passage en enforce DNS .162

| Critère | Résultat | Mesure |
|---|---|---|
| contrôleur de .162 en mode enforce | ✅ | enforce |
| dnsmasq .162 publie .160 seule (les 2 noms), TTL 30 | ✅ | {'helios.orange.tn': (['192.168.148.160'], 30), 'api.helios.orange.tn': (['192.168.148.160'], 30)} |
| fichier hostsdir de .162 = .160 seule | ✅ | # géré par helios-dns-health (mode enforce) — ne pas éditer à la main / 192.168.148.160 helios.orange.tn api.helios.orange.tn |
| l'AUTRE LB (.160) n'a pas changé de réponses DNS | ✅ | {'avant': {'helios.orange.tn': (['192.168.148.160', '192.168.148.162'], 30), 'api.helios.orange.tn': (['192.168.148.160', '192.168.148.162'], 30)}, 'après': {'helios.orange.tn': (['192.168.148.160', '192.168.148.162'], 30), 'api.helios.orange.tn': (['192.168.148.160', '192.168.148.162'], 30)}} |
| script enforce-dns-health.sh terminé sans erreur | ✅ | 0 |
| [Windows] web >= 99,5 % et série <= 10 s pendant le passage (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 38 ms / BRUT: 100.0 % / 0.0 s / p95 38 ms |
| [Windows] api_app >= 99,5 % et série <= 10 s pendant le passage (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 41 ms / BRUT: 100.0 % / 0.0 s / p95 41 ms |
| [Windows] kube_api >= 99,5 % et série <= 10 s pendant le passage (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 26 ms / BRUT: 100.0 % / 0.0 s / p95 26 ms |
| [.161] web >= 99,5 % et série <= 10 s pendant le passage (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 22 ms / BRUT: 100.0 % / 0.0 s / p95 22 ms |
| [.161] api_app >= 99,5 % et série <= 10 s pendant le passage (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 19 ms / BRUT: 100.0 % / 0.0 s / p95 19 ms |
| [.161] kube_api >= 99,5 % et série <= 10 s pendant le passage (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 21 ms / BRUT: 100.0 % / 0.0 s / p95 21 ms |

Gels d'E/S hôte : aucun
