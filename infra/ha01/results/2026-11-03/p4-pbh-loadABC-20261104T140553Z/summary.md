# p4_webpath_blackhole loadABC

CRITÈRE INDICATIF TENU

```
Windows  web        96.491 % / 1.0 s / p95 2050 ms | échecs≈2  séries: [['14:06:28', 1.0], ['14:06:38', 1.0]]   => OK
Windows  api_app    98.148 % / 1.0 s / p95 2070 ms | échecs≈1  séries: [['14:06:22', 1.0]]   => OK
Windows  lb160_web  96.491 % / 1.0 s / p95 2049 ms | échecs≈2  séries: [['14:06:29', 1.0], ['14:06:40', 1.0]]
Windows  lb162_web  98.333 % / 1.0 s / p95 2062 ms | échecs≈1  séries: [['14:06:35', 1.0]]
Windows  kube_api   100.0 % / 0.0 s / p95 29 ms | échecs≈0
.161     web        98.438 % / 1.0 s / p95 2050 ms | échecs≈1  séries: [['14:06:45', 1.0]]   => OK
.161     api_app    98.077 % / 1.0 s / p95 3054 ms | échecs≈1  séries: [['14:06:11', 1.0]]   => OK
.161     lb160_web  98.333 % / 1.0 s / p95 3051 ms | échecs≈1  séries: [['14:06:51', 1.0]]
.161     lb162_web  100.0 % / 0.0 s / p95 3054 ms | échecs≈0
.161     kube_api   100.0 % / 0.0 s / p95 22 ms | échecs≈0
charge / : n=241 échecs=9 (3.7 %) p50=74 p95=3054
charge /api/test/health-readiness : n=239 échecs=17 (7.1 %) p50=2053 p95=4059
```
