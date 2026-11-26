package com.orange.pfe.backend.service;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.util.List;

/** Accès PostgreSQL à l'outbox des évènements SSE (table {@code sse_event}, migration V29). */
@Component
public class SseOutbox {

    public record Row(long id, String eventName, String namespace, String payload) {}

    private final JdbcTemplate jdbc;

    public SseOutbox(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    public long append(String eventName, String namespace, String json) {
        Long id = jdbc.queryForObject(
                "INSERT INTO sse_event (event_name, namespace, payload) VALUES (?, ?, ?) RETURNING id",
                Long.class, eventName, namespace, json);
        return id == null ? 0 : id;
    }

    public long maxId() {
        Long m = jdbc.queryForObject("SELECT COALESCE(MAX(id), 0) FROM sse_event", Long.class);
        return m == null ? 0 : m;
    }

    public List<Row> after(long lastId, int limit) {
        return jdbc.query("SELECT id, event_name, namespace, payload FROM sse_event WHERE id > ? ORDER BY id LIMIT ?",
                (rs, i) -> new Row(rs.getLong(1), rs.getString(2), rs.getString(3), rs.getString(4)), lastId, limit);
    }

    /** Évènements récents (au plus {@code maxAgeSeconds}) postérieurs à {@code lastId}, pour la reprise après déconnexion. */
    public List<Row> replay(long lastId, int maxAgeSeconds, int limit) {
        return jdbc.query("SELECT id, event_name, namespace, payload FROM sse_event "
                        + "WHERE id > ? AND created_at > now() - (? * interval '1 second') ORDER BY id LIMIT ?",
                (rs, i) -> new Row(rs.getLong(1), rs.getString(2), rs.getString(3), rs.getString(4)), lastId, maxAgeSeconds, limit);
    }

    public int purgeOlderThanOneHour() {
        return jdbc.update("DELETE FROM sse_event WHERE created_at < now() - interval '1 hour'");
    }
}