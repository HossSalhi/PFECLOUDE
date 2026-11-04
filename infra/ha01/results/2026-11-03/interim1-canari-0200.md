# Contrôle intermédiaire n°1 (4 nov. 03:08 UTC) — note sur le trou d'écriture du canari à 02:00 UTC

- Constat : le canari (pod sur `.153`) n'a rien acquitté de 02:00:03 à 02:00:34 (31 s), avec 2 échecs côté client
  (`SSL SYSCALL error: EOF detected`, `connection to server was lost`) puis `SESSION_END` et reprise à 02:00:34.
- Cause établie : le journal du **primaire** (`postgres-7`) ne montre AUCUNE erreur ni arrêt ; il enregistre à 02:00:14.305
  `could not receive data from client: Connection reset by peer` pour la connexion du canari (reset **côté client**).
  Au même instant `.153` est le seul nœud sous pression CPU (PSI 0,70 contre 0,12–0,14 sur `.155`/`.158`).
  => artefact de mesure : le canari tourne sur `.153`, le nœud qui sature périodiquement.
- Conséquence pour les preuves : un canari unique sur `.153` peut produire de faux trous d'écriture de plusieurs secondes.
  **À corriger avant la Phase 2a** : 2e canari hors `.153` (sur `.161`, via un NodePort temporaire de `postgres-rw` sur les workers,
  connexion libpq multi-hôtes) ; seuls les trous vus par les deux canaris seront comptés comme indisponibilité réelle.
- Tout le reste : OK (sondes 100 % sur 3 h 42 vues de 2 points, malgré un pic de pression CPU de `.153` à 95,8 %).
