# Durcissement sécurité — HELIOS / Kubernetes

Corrections appliquées d'après les bonnes pratiques Kubernetes (cours TechWorld with Nana,
« Défense en profondeur »). État du cluster : HA 3-masters (kubeadm 1.29, stacked etcd),
endpoint API via HAProxy sur `.160`, monitoring hors cluster.

## 1. Chiffrement des secrets au repos (etcd) — APPLIQUÉ
Par défaut les Secrets sont en clair (base64) dans etcd. Activé le chiffrement `aescbc` :
- fichier `EncryptionConfiguration` en `/etc/kubernetes/pki/encryption-config.yaml` sur **les 3
  masters** (répertoire déjà monté dans le pod apiserver ; mode 600 ; **clé identique partout**,
  jamais versionnée) ;
- flag `--encryption-provider-config=/etc/kubernetes/pki/encryption-config.yaml` ajouté au manifeste
  `/etc/kubernetes/manifests/kube-apiserver.yaml` de chaque master (backup `.bak-enc`), **un master à
  la fois** (le quorum etcd 2/3 maintient l'API pendant le redémarrage de l'apiserver) ;
- re-chiffrement des secrets existants : `kubectl get secrets -A -o json | kubectl replace -f -`.

Provider avec repli `identity` (lit les anciens secrets en clair, écrit les nouveaux chiffrés) :
```yaml
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources: [secrets]
    providers:
      - aescbc: { keys: [ { name: key1, secret: <BASE64_32_OCTETS_—_NON_VERSIONNÉ> } ] }
      - identity: {}
```
**Preuve** : `etcdctl get /registry/secrets/pfe/backend-secret` renvoie un blob préfixé
`k8s:enc:aescbc:v1:key1:` (chiffré) ; `kubectl get secret` reste lisible.
Rollback : restaurer `/root/kube-apiserver.yaml.bak-enc` sur le master concerné.

## 2. Pod Security Standards — APPLIQUÉ
Namespace `pfe` étiqueté :
```
pod-security.kubernetes.io/enforce = baseline     # bloque privileged/hostNetwork/hostPID à la création
pod-security.kubernetes.io/warn    = restricted   # avertit sur les pods root (apps-cobayes de chaos)
pod-security.kubernetes.io/audit   = restricted
```
`enforce=baseline` vérifié sans risque (aucun deploy pfe ne viole baseline). `backend` et `frontend`
tournent déjà **non-root** (uid 1000/101, `runAsNonRoot`, `drop: [ALL]`, `allowPrivilegeEscalation:false`).
Les 19 apps `helios-*` de chaos tournent en root (jetables) — remontées par `warn/audit` sans blocage.

## 3. Endpoint API en HA pour kubectl — APPLIQUÉ
`~/.kube/config` bascule de `.153:6443` (SPOF) vers `.160:6443` (HAProxy → 3 masters), pour que
`kubectl` survive à la perte d'un master, comme les kubelets.

## 4. NetworkPolicies — NON appliqué dans `pfe` (choix assumé)
Nana recommande un deny-by-default. **Non fait dans `pfe`** car :
- la remédiation HELIOS exécute `kubectl delete networkpolicy -n pfe --all` → toute policy serait supprimée ;
- `pfe` est le **namespace-lab de chaos** : le détecteur y **injecte** des NetworkPolicies pour simuler
  des pannes réseau/DNS. Un réseau plat y est nécessaire.
En production, un namespace applicatif dédié serait verrouillé (default-deny + allows DNS/ingress/DB).

## 5. TLS / HTTPS — APPLIQUÉ
`https://helios.orange.tn` disponible. Certificat auto-signé (CN + SAN `helios.orange.tn` + IP
`.153/.155/.158`, 825 j) :
- **Secret TLS `helios-tls`** (ns `pfe`) + bloc `tls:` sur l'objet `helios-ingress` (TLS natif K8s),
  annotation `ssl-redirect: "false"`.
- **Terminaison TLS par l'ingress** : MetalLB attribue une IP au Service ingress-nginx
  (type LoadBalancer), le trafic arrive directement sur `ingress-nginx` qui termine le TLS
  avec le Secret `helios-tls`. Plus de HAProxy externe pour les apps (remplacé par MetalLB
  K8s-natif). HAProxy sur `.160` ne gère plus que le control-plane kubeadm (`:6443`).
- Prod : remplacer l'auto-signé par Let's Encrypt / CA d'entreprise (le navigateur avertit sinon).

## 6. Backups de la base — APPLIQUÉ
`CronJob pfe/postgres-backup` (`infra/postgres-backup.yaml`) : `pg_dump` quotidien **02:00**, gzip vers
le PVC `postgres-backups` (local-path 5Gi), **rétention 7**. Réutilise l'image CNPG + secret `postgres-app`
(user `appuser`). Testé : dump `pfe_db-*.sql.gz` produit. Restauration : `gunzip | psql -h postgres-rw ...`.
(Étape suivante possible : backup CNPG natif barman-cloud vers MinIO/S3.)

## 7. Mode strict + secrets réels — APPLIQUÉ
`SECURITY_STRICT_MODE=true` (le backend **refuse de démarrer** avec des secrets par défaut, cf.
`SecurityStartupValidator`). Régénéré dans `backend-secret` (hors git) : **`JWT_SECRET`** (`openssl rand
-base64 32` — l'ancien défaut était **public** ⇒ tokens forgeables), `SUPER_ADMIN_PASSWORD`,
`BACKEND_API_PASSWORD` (ce dernier = *legacy, plus utilisé pour l'auth*). Vérifié : log
« Security startup check PASSED », login `admin` toujours OK (mot de passe en base, inchangé par le JWT).
Fait en **rolling update** : le pod strict qui échoue laisse l'ancien servir (zéro coupure).

## 8. Rotation de la clé etcd — APPLIQUÉE (une fois)
Rotation faite : nouvelle clé `key2` **en tête** de l'`EncryptionConfiguration` (chiffre les nouveaux
écrits), `key1` conservée en 2ᵉ (lecteur). Config déployée identique sur les 3 masters, apiserver
redémarrés **un par un** (manifest sorti/remis, API servie par les 2 autres), puis re-chiffrement
`kubectl get secrets -A -o json | kubectl replace -f -`. Preuve : `etcdctl get .../backend-secret`
→ `k8s:enc:aescbc:v1:**key2**:`. `key1` désormais **inerte** (plus rien chiffré avec) — la retirer
(round 2 : config `[key2, identity]` + restart) est une simple maintenance ultérieure. Backups de
sécurité : snapshots etcd + `encryption-config.yaml.bak-rot-*`.

## 9. Snapshot etcd planifié — APPLIQUÉ
`helios-etcd-snapshot.timer` (systemd, **quotidien 01:30**) sur les 3 masters → `/var/backups/etcd/`
(`etcdctl snapshot save`, rétention 7). `etcdctl` copié sur `.155`/`.158` (n'était que sur `.153`).

## 10. NetworkPolicies (production) — LIVRÉ EN CODE (non appliqué à `pfe`)
`infra/network-policies.yaml` : modèle zero-trust (default-deny + DNS + ingress→frontend→backend→postgres)
pour un namespace applicatif de prod. **Pas appliqué à `pfe`** (bac à sable chaos) — voir §4.

## Écarts restants (bonnes pratiques, hors démo)
- GitOps (ArgoCD) : optionnel. **Helm** : charts fournis (`infra/helm/helios/`).
- `--anonymous-auth=false` sur l'apiserver : **piège** — les probes liveness/readiness des static pods
  frappent `/livez`/`/readyz` en anonyme ; à ne faire qu'avec des probes authentifiées.
