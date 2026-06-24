# 🎬 GUIDE DÉMO HELIOS — devant l'encadrant Orange

> Tout se lance depuis **PowerShell** dans `C:\Users\houss\IdeaProjects\PFE-Orange`.
> Garde l'**interface HELIOS** (page Incidents) ouverte à côté.

---

## ✅ AVANT DE COMMENCER (vérif 1 min)
```powershell
cd C:\Users\houss\IdeaProjects\PFE-Orange
kubectl get nodes
kubectl get pods -n pfe
```
Attendu : **3 nœuds Ready**, podinfo **2/2**, backend **2/2**, postgres **2/2**.

---

## 🔹 PARTIE 1 — Détection + diagnostic IA (3 pannes différentes) — ~5 min

**1. Déployer 3 apps qui tombent en panne de 3 façons :**
```powershell
kubectl apply -f demo-incident-apps.yaml
```
**2. Montrer les états des pods :**
```powershell
kubectl get pods -n pfe | findstr incident
```
→ `incident-pending` = Pending · `incident-crashloop` = CrashLoopBackOff · `incident-imagepull` = ImagePullBackOff

**3. Attendre ~2 min** → dans l'UI **page Incidents**, 3 incidents apparaissent :

| App | Type détecté par l'IA |
|---|---|
| incident-crashloop | **POD_CRASH_LOOP** |
| incident-imagepull | DEPLOYMENT_UNAVAILABLE |
| incident-pending | DEPLOYMENT_UNAVAILABLE |

**4. Ouvrir le détail de l'incident `POD_CRASH_LOOP`** → montrer l'explication IA (cite `CrashLoopBackOff`, `StartError`).

> 🗣️ **À dire :** « 3 pannes, 3 diagnostics distincts avec 3 actions recommandées adaptées — HELIOS découvre les apps automatiquement et l'IA lit la vraie cause racine sur le cluster. »

---

## 🔹 PARTIE 2 — Résolution honnête (basée sur vérification) — ~4 min

**5. Supprimer les apps (= corriger la cause racine) :**
```powershell
kubectl delete -f demo-incident-apps.yaml
```
**6. Attendre ~3-4 min** → les 3 incidents passent **RESOLVED** tout seuls.

> 🗣️ **À dire :** « Tant que la panne persiste, HELIOS reste honnêtement ouvert. Dès que la cause racine disparaît, il revérifie l'état réel et ferme l'incident lui-même. »

---

## 🔹 PARTIE 3 — Boucle complète : Démarrer → Camunda répare → RESOLVED — ~5 min

**7. Casser podinfo de façon RÉPARABLE :**
```powershell
.\casser-podinfo.ps1
```
**8. Attendre ~2 min** → un incident **DEPLOYMENT_UNAVAILABLE sur podinfo** apparaît dans la liste.

**9. Cliquer le bouton `Démarrer`** sur cet incident podinfo.

**10. Observer (l'incident se met à jour tout seul, ~2 min) :**
- statut → **IN_PROGRESS**
- action → **RESTART_POD** exécutée par Camunda
- Camunda **vérifie** que podinfo est sain → statut → **RESOLVED** ✅

> 🗣️ **À dire :** « Le clic Démarrer lance le workflow Camunda : il exécute la remédiation PUIS vérifie la guérison réelle du service avant de fermer l'incident. Il ne ferme jamais sans preuve. »

---

## 🔄 RÉINITIALISER (entre 2 répétitions)
```powershell
.\reparer-podinfo.ps1
```
Remet podinfo sain (2/2).

---

## 🆘 Si un incident reste bloqué
- Une panne **permanente** (image/commande cassée) reste IN_PROGRESS → **c'est normal et voulu** (HELIOS ne ment pas).
- Pour nettoyer : supprime l'app concernée → HELIOS auto-résout en ~3 min.
