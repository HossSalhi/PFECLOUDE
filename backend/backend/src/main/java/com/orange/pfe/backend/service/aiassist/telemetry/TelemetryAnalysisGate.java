package com.orange.pfe.backend.service.aiassist.telemetry;

import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

import java.util.UUID;

/**
 * Limite les analyses SIMULTANÉES : une par utilisateur et {@value #MAX_GLOBAL} au total. L'état est en base (table
 * {@code telemetry_analysis_inflight}) et non en mémoire : le backend tourne en plusieurs réplicas, un verrou local à un pod
 * laisserait un utilisateur lancer une analyse sur chaque pod.
 *
 * <p>Aucune transaction n'est conservée pendant l'analyse : {@link #acquire} et {@link #release} sont deux instructions
 * isolées (validation immédiate). Une ligne plus ancienne que {@value #STALE_SECONDS} s (analyse la plus longue possible :
 * environ 35 s) est considérée abandonnée, par exemple si un réplica s'est arrêté en cours d'analyse.</p>
 */
@Component
public class TelemetryAnalysisGate {

    static final int STALE_SECONDS = 90;
    static final int MAX_GLOBAL = 3;

    private final JdbcTemplate jdbc;
    private final String instance = System.getenv().getOrDefault("HOSTNAME", "local");

    public TelemetryAnalysisGate(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** Réserve une place ; refuse (HTTP 429) si l'utilisateur a déjà une analyse en cours ou si le plafond global est atteint. */
    public String acquire(long userId) {
        String token = UUID.randomUUID().toString();
        int taken = jdbc.update("""
                insert into telemetry_analysis_inflight (user_id, token, started_at, instance)
                select ?, ?, now(), ?
                where (select count(*) from telemetry_analysis_inflight where started_at > now() - make_interval(secs => ?)) < ?
                on conflict (user_id) do update
                   set token = excluded.token, started_at = excluded.started_at, instance = excluded.instance
                   where telemetry_analysis_inflight.started_at <= now() - make_interval(secs => ?)
                """, userId, token, instance, (double) STALE_SECONDS, MAX_GLOBAL, (double) STALE_SECONDS);
        if (taken == 1) {
            return token;
        }
        Integer mine = jdbc.queryForObject("select count(*) from telemetry_analysis_inflight where user_id = ? and started_at > now() - make_interval(secs => ?)",
                Integer.class, userId, (double) STALE_SECONDS);
        throw new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS, mine != null && mine > 0
                ? "Une analyse est déjà en cours pour votre compte : attendez sa fin."
                : "Trop d'analyses sont en cours sur la plateforme : réessayez dans un instant.");
    }

    /** Libère la place (seulement si elle appartient toujours à cette analyse : jamais celle d'une analyse plus récente). */
    public void release(long userId, String token) {
        jdbc.update("delete from telemetry_analysis_inflight where user_id = ? and token = ?", userId, token);
    }
}
