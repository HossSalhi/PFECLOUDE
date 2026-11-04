# p4_webpath_blackhole loadAB

CRITÈRE INDICATIF TENU

```
Windows  web        100.0 % / 0.0 s / p95 2748 ms | échecs≈0   => OK
Windows  api_app    93.878 % / 6.0 s / p95 3136 ms | échecs≈3  séries: [['14:01:09', 1.0], ['14:01:37', 6.0]]   => OK
Windows  lb160_web  98.305 % / 1.0 s / p95 2070 ms | échecs≈1  séries: [['14:01:23', 1.0]]
Windows  lb162_web  100.0 % / 0.0 s / p95 3050 ms | échecs≈0
Windows  kube_api   100.0 % / 0.0 s / p95 30 ms | échecs≈0
.161     web        98.214 % / 1.0 s / p95 2057 ms | échecs≈1  séries: [['14:01:03', 1.0]]   => OK
.161     api_app    94.0 % / 1.0 s / p95 4046 ms | échecs≈3  séries: [['14:01:10', 1.0], ['14:01:23', 1.0], ['14:01:38', 1.0]]   => OK
.161     lb160_web  98.305 % / 1.0 s / p95 2057 ms | échecs≈1  séries: [['14:01:16', 1.0]]
.161     lb162_web  98.182 % / 1.0 s / p95 3057 ms | échecs≈1  séries: [['14:01:05', 1.0]]
.161     kube_api   100.0 % / 0.0 s / p95 32 ms | échecs≈0
charge / : n=241 échecs=6 (2.5 %) p50=80 p95=3073
charge /api/test/health-readiness : n=237 échecs=22 (9.3 %) p50=1072 p95=4130
```
