# Déploiement HAProxy canonique sur .162

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant l'observation (informatif, E6) | ✅ | aucun |
| config finale de .162 = config canonique (sha256 808d3a9c4580eca6) | ✅ | 808d3a9c4580eca6 |
| HAProxy .162 : tous les serveurs UP | ✅ | [] |
| [Windows] lb162_web >= 99,9 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 68 ms / BRUT: 100.0 % / 0.0 s / p95 68 ms |
| [Windows] lb162_api >= 99,9 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 32 ms / BRUT: 100.0 % / 0.0 s / p95 32 ms |
| [Windows] web >= 99.0 % et série <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 67 ms / BRUT: 100.0 % / 0.0 s / p95 67 ms |
| [Windows] api_app >= 99.0 % et série <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 79 ms / BRUT: 100.0 % / 0.0 s / p95 79 ms |
| [Windows] kube_api >= 99.0 % et série <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 33 ms / BRUT: 100.0 % / 0.0 s / p95 33 ms |
| charge 10 req/s forcée vers .162 : >= 99,9 % de succès (hors gels), p95 < 1500 ms | ✅ | 1251/1251 p95=74 ms |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 2433 |
| pas d'abandon (sinon config d'origine restaurée) | ✅ | None |

```
--- deploye
+++ canonique
@@ -13,2 +13,7 @@
 #   - le backend https est contrôlé comme le http (GET / sur le NodePort http du même nœud) au lieu d'un simple TCP
+#   - E16 (4 nov. 2026, arrêts réels de .155) : les backends web sont contrôlés sur `GET /ping` de Traefik (entrypoint `ping` :8082, indépendant de
+#     l'application) et NON plus sur `GET / Host: helios.orange.tn` qui traversait Traefik puis le pod frontend. Mesuré (journal HAProxy du 4 nov. 12:35Z) :
+#     pendant ~45 s après la mort d'un worker ses pods restent dans les endpoints ; un contrôle sur deux du SURVIVANT tombait sur le pod mort, dépassait
+#     3 s (« Layer7 timeout ») et le survivant était déclaré DOWN à son tour -> « backend has no server available » -> coupure web de 11 à 32 s.
+#     `timeout connect 1s` sur ces deux backends (LAN) : une connexion vers un nœud mort est retentée sur l'autre serveur après 1 s au lieu de 3 s.
 # =============================================================================
@@ -95,6 +100,9 @@
     balance roundrobin
+    # Santé = le processus Traefik (/ping, entrypoint `ping` :8082), pas le chemin applicatif (cf. E16 en tête de fichier).
     option httpchk
-    http-check send meth GET uri / hdr Host helios.orange.tn
-    http-check expect status 200,301,302,404
-    default-server inter 2s fastinter 1s downinter 2s fall 2 rise 2 check on-marked-down shutdown-sessions
+    http-check send meth GET uri /ping
+    http-check expect status 200
+    timeout connect 1s
+    timeout check 2s
+    default-server inter 2s fastinter 1s downinter 2s fall 2 rise 2 check port 8082 on-marked-down shutdown-sessions
     server node-155 192.168.148.155:8000
@@ -111,8 +119,10 @@
     balance roundrobin
-    # TLS terminé par l'ingress (Secret helios-tls) : on contrôle la santé applicative via le NodePort http du même nœud.
+    # TLS terminé par Traefik (Secret helios-tls). Santé = le processus Traefik du même nœud (/ping :8082), pas le chemin applicatif (cf. E16).
     option httpchk
-    http-check send meth GET uri / hdr Host helios.orange.tn
-    http-check expect status 200,301,302,404
-    default-server inter 2s fastinter 1s downinter 2s fall 2 rise 2 check port 8000 on-marked-down shutdown-sessions
+    http-check send meth GET uri /ping
+    http-check expect status 200
+    timeout connect 1s
+    timeout check 2s
+    default-server inter 2s fastinter 1s downinter 2s fall 2 rise 2 check port 8082 on-marked-down shutdown-sessions
     server node-155 192.168.148.155:8443
-    server node-158 192.168.148.158:8443+    server node-158 192.168.148.158:8443

```
