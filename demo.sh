#!/bin/bash
TOKEN=$(curl -s -X POST http://localhost:8080/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"superadmin","password":"superadmin-change-me"}' \
  | grep -o '"accessToken":"[^"]*"' | sed 's/"accessToken":"//;s/"//')
echo "Token: ${TOKEN:0:30}..."

# Incident 1 : Anomalie CPU détectée par IA
curl -s -w "\nHTTP:%{http_code}\n" \
  -X POST http://localhost:8080/api/incidents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"title":"Anomalie CPU detectee par IA - pod backend","description":"Le modele XGBoost a detecte une anomalie sur les metriques CPU et memoire du pod backend dans le namespace pfe. Score de confiance: 87%. Analyse basee sur 5 minutes de donnees Prometheus et embeddings de logs Loki. Action recommandee: RESTART_POD.","severity":"HIGH","source":"AI"}'

# Incident 2 : Taux d'erreurs élevé
curl -s -w "\nHTTP:%{http_code}\n" \
  -X POST http://localhost:8080/api/incidents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"title":"Taux erreurs HTTP anormal - namespace pfe","description":"Detection dune augmentation anormale du taux derreurs HTTP 5xx. Confiance: 73%. Le modele de correlation de logs a identifie un pattern derreurs recurrent. Aucune action automatique: seuil de confiance inferieur a 70% requis pour lautonomie.","severity":"MEDIUM","source":"AI"}'

# Incident 3 : Incident critique
curl -s -w "\nHTTP:%{http_code}\n" \
  -X POST http://localhost:8080/api/incidents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"title":"Indisponibilite service critique detectee","description":"Le modele de prediction proactive a identifie un risque eleve dindisponibilite dans les 10 prochaines minutes. Score de confiance: 92%. Metriques: CPU 89%, restart count 3, error ratio 34%. Remediation automatique declenchee via Camunda.","severity":"CRITICAL","source":"AI"}'

echo "Done."
