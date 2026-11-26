package com.orange.pfe.backend.service;

import org.junit.jupiter.api.Test;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/** Isolation par espace de noms du flux SSE (même règle que TenantContext). */
class SseEventServiceScopeTest {

    static class Capturing extends SseEmitter {
        final List<String> events = new ArrayList<>();
        @Override public synchronized void send(SseEventBuilder b) { events.add(b.build().stream().map(d -> String.valueOf(d.getData())).reduce("", String::concat)); }
    }

    private final List<Capturing> made = new ArrayList<>();
    private SseEventService service() {
        return new SseEventService() { @Override protected SseEmitter newEmitter() { Capturing c = new Capturing(); made.add(c); return c; } };
    }
    private long countOf(Capturing c, String needle) { return c.events.stream().filter(e -> e.contains(needle)).count(); }

    @Test void restrictedSubscriber_receivesOnlyItsNamespaces_unrestrictedReceivesAll() {
        SseEventService s = service();
        s.subscribe(Set.of("ns-a")); s.subscribe(Set.of()); s.subscribe(Set.of("ns-b"));
        s.broadcast("incident_created", "ns-a", Map.of("id", 1, "title", "TITRE-A"));
        s.broadcast("incident_created", "ns-b", Map.of("id", 2, "title", "TITRE-B"));
        assertThat(countOf(made.get(0), "TITRE-A")).isEqualTo(1); assertThat(countOf(made.get(0), "TITRE-B")).isZero();
        assertThat(countOf(made.get(1), "TITRE-A")).isEqualTo(1); assertThat(countOf(made.get(1), "TITRE-B")).isEqualTo(1);
        assertThat(countOf(made.get(2), "TITRE-A")).isZero();     assertThat(countOf(made.get(2), "TITRE-B")).isEqualTo(1);
    }
    @Test void eventWithoutNamespace_failsClosed_forRestrictedSubscribers() {
        SseEventService s = service();
        s.subscribe(Set.of("ns-a")); s.subscribe(Set.of());
        s.broadcast("incident_created", Map.of("title", "SANS-NS"));
        s.broadcast("incident_created", null, Map.of("title", "NS-NUL"));
        assertThat(countOf(made.get(0), "SANS-NS")).isZero(); assertThat(countOf(made.get(0), "NS-NUL")).isZero();
        assertThat(countOf(made.get(1), "SANS-NS")).isEqualTo(1); assertThat(countOf(made.get(1), "NS-NUL")).isEqualTo(1);
    }
}