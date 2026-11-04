# LB actif du DNS : .162

| Critère | Résultat | Mesure |
|---|---|---|
| [Windows] web >= 99,5 % et série <= 10 s pendant le changement (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 31 ms / BRUT: 100.0 % / 0.0 s / p95 31 ms |
| [Windows] api_app >= 99,5 % et série <= 10 s pendant le changement (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 38 ms / BRUT: 100.0 % / 0.0 s / p95 38 ms |
| [Windows] kube_api >= 99,5 % et série <= 10 s pendant le changement (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 30 ms / BRUT: 100.0 % / 0.0 s / p95 30 ms |
| [.161] web >= 99,5 % et série <= 10 s pendant le changement (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 21 ms / BRUT: 100.0 % / 0.0 s / p95 21 ms |
| [.161] api_app >= 99,5 % et série <= 10 s pendant le changement (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 31 ms / BRUT: 100.0 % / 0.0 s / p95 31 ms |
| [.161] kube_api >= 99,5 % et série <= 10 s pendant le changement (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 24 ms / BRUT: 100.0 % / 0.0 s / p95 24 ms |
| contrôleur .162 : enforce, publie .162 seule, dnsmasq cohérent | ✅ | {'mode': 'enforce', 'publish': ['192.168.148.162'], 'dnsmasq': {'helios.orange.tn': ['192.168.148.162'], 'api.helios.orange.tn': ['192.168.148.162']}} |
| contrôleur .160 : enforce, publie .162 seule, dnsmasq cohérent | ✅ | {'mode': 'enforce', 'publish': ['192.168.148.162'], 'dnsmasq': {'helios.orange.tn': ['192.168.148.162'], 'api.helios.orange.tn': ['192.168.148.162']}} |
| pas d'abandon | ✅ | None |

Gels d'E/S hôte : aucun
