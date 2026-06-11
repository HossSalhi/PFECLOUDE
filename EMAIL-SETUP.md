# HELIOS — Activer la notification par email (L16)

> **Le code est déjà 100 % implémenté.** `EmailNotificationService.java` envoie un mail
> à chaque escalade (`AIOpsOrchestrationService.notifyEscalation`) et sur les tâches
> humaines. Il n'y a **rien à coder** — uniquement 3-4 variables d'environnement à fournir.

## Ce qui existe déjà
- `EmailNotificationService.java` — rendu HTML + envoi via Spring `JavaMailSender`.
- Déclenché sur : **ESCALATION**, création/décision de tâche humaine, incident résolu.
- Config templatée dans `application.properties` (lignes 142-158), désactivée par défaut.

## Activation (production)
Définir ces variables d'env sur le backend (deployment K8s `backend` ou `.env`) :

```bash
NOTIFICATION_EMAIL_ENABLED=true
NOTIFICATION_EMAIL_TO=ton.email@orange.tn          # destinataire(s) des alertes
NOTIFICATION_EMAIL_MIN_SEVERITY=HIGH               # HIGH | CRITICAL
NOTIFICATION_EMAIL_DASHBOARD_URL=http://192.168.148.153:30880

# SMTP (exemple Gmail — utiliser un App Password, pas le mot de passe du compte)
MAIL_HOST=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=ton.compte@gmail.com
MAIL_PASSWORD=xxxx_app_password_xxxx
```

### Dans K8s
Ajouter au `backend-secret.yaml` / `backend-configmap.yaml` puis :
```bash
kubectl rollout restart deployment/backend -n pfe
```

### Alternative locale (démo sans SMTP réel) — MailHog
Le projet utilise déjà MailHog en dev (voir mémoire ops). Pointer le SMTP dessus :
```bash
MAIL_HOST=192.168.148.161   # ou l'hôte MailHog
MAIL_PORT=1025
# pas d'auth/starttls pour MailHog : surcharger si besoin
SPRING_MAIL_PROPERTIES_MAIL_SMTP_AUTH=false
SPRING_MAIL_PROPERTIES_MAIL_SMTP_STARTTLS_ENABLE=false
```
UI MailHog : http://<host>:8025 — les mails d'escalade y apparaissent.

## Test de bout en bout
1. Activer les variables ci-dessus, redémarrer le backend.
2. Déclencher un incident HIGH/CRITICAL (ex. arrêt worker → `NODE_NOT_READY`,
   ou `kubectl scale deploy/backend --replicas=0 -n pfe`).
3. Laisser l'escalade se produire (timeout d'approbation ou action ESCALATION).
4. Vérifier la réception du mail (ou l'UI MailHog).

> Si `NOTIFICATION_EMAIL_ENABLED=false`, le service log juste l'escalade sans envoyer —
> c'est le comportement par défaut volontaire pour ne pas spammer en dev.
