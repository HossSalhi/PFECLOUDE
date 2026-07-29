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

## Écarts restants (bonnes pratiques, à planifier hors démo)
- `SECURITY_STRICT_MODE=true` + vrai `JWT_SECRET`/`BACKEND_API_PASSWORD` via Secret (redémarrage backend).
- Rotation de la clé de chiffrement etcd (planifiée).
- GitOps (ArgoCD) et généralisation de Helm (packaging backend/frontend).
- `--anonymous-auth=false` sur l'apiserver : **piège** — les probes liveness/readiness des static pods
  frappent `/livez`/`/readyz` en anonyme ; à ne faire qu'avec des probes authentifiées.
