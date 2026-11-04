# Reprise applicative de la base — journaux des pods backend, fenêtre 2026-11-04T15:13:00Z -> 2026-11-04T15:30:00Z

- **backend-75b8d9569b-4tfvg** (k8s-worker1) : **aucune** erreur de connexion JDBC dans la fenêtre
- **backend-75b8d9569b-f7qpn** (k8s-worker2) : 6 lignes d'erreur JDBC, de 2026-11-04T15:13:30Z à 2026-11-04T15:13:59Z (dernière erreur 29 s après la première)
    - `2026-11-04T15:13:30.927848151Z 2026-11-04 16:13:30.927 WARN  [HikariPool-1:housekeeper] [] [] com.zaxxer.hikari.pool.PoolBase - HikariPool-1 - Failed to validate connection org.postgresql.jdbc.PgConnection@1ad596ac (This connection has been`
    - `2026-11-04T15:13:36.935245100Z 2026-11-04 16:13:36.934 WARN  [HikariPool-1:housekeeper] [] [] com.zaxxer.hikari.pool.PoolBase - HikariPool-1 - Failed to validate connection org.postgresql.jdbc.PgConnection@1b6876fb (This connection has been`
    - `2026-11-04T15:13:53.785542936Z 2026-11-04 16:13:53.785 WARN  [scheduling-1] [] [] com.zaxxer.hikari.pool.PoolBase - HikariPool-1 - Failed to validate connection org.postgresql.jdbc.PgConnection@4e806448 (This connection has been closed.). P`
    - `2026-11-04T15:13:59.798867350Z 2026-11-04 16:13:59.798 WARN  [scheduling-1] [] [] com.zaxxer.hikari.pool.PoolBase - HikariPool-1 - Failed to validate connection org.postgresql.jdbc.PgConnection@2cbc7e97 (This connection has been closed.). P`
