# p4_webpath_blackhole loadV2

CRITÈRE INDICATIF TENU

```
Windows  web        98.438 % / 1.0 s / p95 2064 ms | échecs≈1  séries: [['14:11:44', 1.0]]   => OK
Windows  api_app    96.364 % / 1.0 s / p95 2075 ms | échecs≈2  séries: [['14:11:57', 1.0], ['14:12:05', 1.0]]   => OK
Windows  lb160_web  94.34 % / 1.0 s / p95 3074 ms | échecs≈3  séries: [['14:11:39', 1.0], ['14:11:59', 1.0], ['14:12:14', 1.0]]
Windows  lb162_web  96.774 % / 1.0 s / p95 2049 ms | échecs≈2  séries: [['14:11:56', 1.0], ['14:12:08', 1.0]]
Windows  kube_api   100.0 % / 0.0 s / p95 26 ms | échecs≈0
.161     web        98.333 % / 1.0 s / p95 2053 ms | échecs≈1  séries: [['14:12:01', 1.0]]   => OK
.161     api_app    100.0 % / 0.0 s / p95 3057 ms | échecs≈0   => OK
.161     lb160_web  96.296 % / 1.0 s / p95 3052 ms | échecs≈2  séries: [['14:11:42', 1.0], ['14:12:08', 1.0]]
.161     lb162_web  100.0 % / 0.0 s / p95 3052 ms | échecs≈0
.161     kube_api   100.0 % / 0.0 s / p95 17 ms | échecs≈0
charge / : n=237 échecs=6 (2.5 %) p50=1007 p95=3050
charge /api/test/health-readiness : n=236 échecs=11 (4.7 %) p50=1055 p95=3085
```
