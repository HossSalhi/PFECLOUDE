# HELIOS — Guide des tests manuels (sans assistance)

> Tous les commandes sont à lancer dans **PowerShell sur l'hôte Windows**.
> Règle d'or : **un seul worker à la fois, JAMAIS les deux en même temps.**

---

## 0) Pré-checks AVANT tout test (obligatoire)

```powershell
# a) Les 3 nœuds Ready
kubectl get nodes

# b) L'opérateur CNPG doit être Running (sinon pas de failover DB → DB DOWN au test node)
kubectl get pods -n cnpg-system

# c) La base PostgreSQL HA est saine (2/2, "Cluster in healthy state")
kubectl get cluster -n pfe
kubectl get pods -n pfe -l cnpg.io/cluster

# d) Le watchdog d'auto-récupération tourne (c'est LUI qui rallume les workers)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*autoheal*" } |
  Select-Object ProcessId

# Si le watchdog est absent, le relancer :
# python "C:\Users\houss\helios-node-autoheal.py"
# (il est aussi lancé automatiquement au démarrage via le dossier Démarrage)
```

Variables utiles :
```powershell
$vmrun = "C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
$w1    = "C:\Users\houss\Documents\Virtual Machines\k8s-worker1\k8s-worker1.vmx"
$w2    = "C:\Users\houss\Documents\Virtual Machines\k8s-worker2\k8s-worker2.vmx"
```

---

## 1) TEST WORKER 1 — panne brutale + auto-remédiation

```powershell
# Couper worker1 (équivaut à un arrêt brutal / panne matérielle)
& $vmrun stop $w1 hard
```

**Ce qui doit se passer TOUT SEUL (ne touche à rien) :**
1. ~40 s : worker1 passe `NotReady`.
2. À 60 s de NotReady : le watchdog déclenche l'auto-heal.
3. Le watchdog lance `vmrun start k8s-worker1` → le worker redémarre.
4. ~2 min après l'arrêt : worker1 revient `Ready`.

**Observer (dans 2 fenêtres) :**
```powershell
# Fenêtre A — le journal du watchdog (doit afficher "AUTO-HEAL: vmrun start k8s-worker1")
Get-Content C:\Users\houss\helios-autoheal.log -Wait -Tail 10

# Fenêtre B — l'état du nœud
while ($true) { kubectl get node k8s-worker1 --no-headers; Start-Sleep 5 }
```

✅ **Succès** = worker1 repasse `Ready` sans que tu l'aies rallumé.

---

## 2) TEST WORKER 2 — (seulement APRÈS que worker1 soit revenu Ready)

```powershell
# Vérifier d'abord que tout est stable + opérateur CNPG Running
kubectl get nodes
kubectl get pods -n cnpg-system

# Puis couper worker2
& $vmrun stop $w2 hard
```
Même observation que worker1, mais avec `k8s-worker2`.

---

## 3) APRÈS un test node — si la réplique DB reste bloquée

Après le reboot d'un worker, la réplique PostgreSQL peut rester `0/1` (elle vise
l'ancienne IP du primary). Si `kubectl get pods -n pfe -l cnpg.io/cluster` montre
un pod `0/1` plus de ~2 min :

```powershell
# Forcer un rejoin propre (l'opérateur recrée le pod + resync depuis le primary)
kubectl delete pod -n pfe postgres-2     # ou postgres-1 selon lequel est 0/1
# Revérifier
kubectl get cluster -n pfe               # doit redevenir "Cluster in healthy state"
```

---

## 4) TEST DIVERS — crash d'un pod applicatif (résilience + détection HELIOS)

```powershell
# Voir les pods podinfo
kubectl get pods -n pfe -l app=podinfo

# En supprimer un (= simule un crash)
kubectl delete pod -n pfe <nom-du-pod-podinfo>
```

**Ce qui doit se passer :**
- Kubernetes recrée immédiatement le pod (~10 s) ; l'autre réplique sert sans coupure.
- HELIOS détecte l'évènement → un incident apparaît dans l'UI (souvent
  `DEPLOYMENT_UNAVAILABLE`), soit **auto-résolu**, soit en **attente d'approbation**.

---

## 5) Où regarder dans l'interface HELIOS

- **Incidents** : la liste des incidents (auto-résolus = vert `RESOLVED` ;
  manuels = `WAITING_FOR_APPROVAL` avec boutons **Démarrer / Résoudre**).
- **Tâches Humaines** : les incidents en attente de validation humaine.
- **Décisions IA** : ce que l'IA a prédit/recommandé.
- **Dashboard / Infrastructure** : l'état des nœuds et des apps.

Pour montrer le chemin **manuel** en direct : ouvrir un incident
`WAITING_FOR_APPROVAL` → cliquer **Démarrer** puis **Résoudre**.

---

## 6) Vérifier les emails

Les incidents de sévérité **HIGH** déclenchent un email vers
`houssamsalhi972@gmail.com` (via le relais hôte). Vérifier la boîte Gmail.
Si rien n'arrive : voir que `helios-host-relay.py` tourne sur l'hôte.

---

## ⚠️ Rappels sécurité
- **Jamais** couper les deux workers en même temps.
- **Toujours** vérifier `kubectl get pods -n cnpg-system` = Running avant un test node.
- **Ne pas** rallumer le worker à la main : c'est le rôle du watchdog (sinon le test
  ne prouve rien).
- **Ne pas** augmenter la RAM de la VM `.161` (risque de cascade sur l'hôte).
