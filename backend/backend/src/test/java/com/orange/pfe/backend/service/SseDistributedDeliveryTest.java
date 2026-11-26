package com.orange.pfe.backend.service;

import org.junit.jupiter.api.Test;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/** Diffusion via l'outbox PostgreSQL (simulée ici) : une seule remise, reprise, dédoublonnage, isolation, repli. */
class SseDistributedDeliveryTest {

    static class Capturing extends SseEmitter {
        final List<String> events = new ArrayList<>();
        @Override public synchronized void send(SseEventBuilder b) {
            events.add(b.build().stream().map(d -> String.valueOf(d.getData())).reduce("", String::concat));
        }
    }
    private final List<Capturing> made = new ArrayList<>();
    private SseEventService service(SseOutbox ob) {
        return new SseEventService(ob) { @Override protected SseEmitter newEmitter() { Capturing c = new Capturing(); made.add(c); return c; } };
    }
    private long count(Capturing c, String needle) { return c.events.stream().filter(e -> e.contains(needle)).count(); }

    @Test void broadcast_writesToTheOutbox_andDoesNotDeliverLocallyBeforeThePoller() {
        SseOutbox ob = mock(SseOutbox.class); SseEventService s = service(ob); s.subscribe(Set.of());
        s.broadcast("incident_created", "ns", Map.of("title", "T1"));
        verify(ob).append(eq("incident_created"), eq("ns"), anyString());
        assertThat(count(made.get(0), "T1")).isZero();
    }
    @Test void deliver_reachesLocalSubscribers_onceAndInOrder_andSkipsAlreadySeenIds() {
        SseEventService s = service(mock(SseOutbox.class)); s.subscribe(Set.of());
        s.deliver(5, "incident_created", "ns", "{\"title\":\"A\"}"); s.deliver(5, "incident_created", "ns", "{\"title\":\"A\"}"); s.deliver(4, "incident_created", "ns", "{\"title\":\"VIEUX\"}"); s.deliver(6, "incident_created", "ns", "{\"title\":\"B\"}");
        assertThat(count(made.get(0), "\"A\"")).isEqualTo(1); assertThat(count(made.get(0), "VIEUX")).isZero(); assertThat(count(made.get(0), "\"B\"")).isEqualTo(1);
    }
    @Test void reconnection_replaysMissedEvents_withoutDuplicatesAndRespectingNamespaces() {
        SseOutbox ob = mock(SseOutbox.class);
        when(ob.replay(10, SseEventService.REPLAY_MAX_AGE_SECONDS, SseEventService.REPLAY_MAX_EVENTS)).thenReturn(List.of(
                new SseOutbox.Row(11, "incident_created", "ns-a", "{\"title\":\"MANQUE-A\"}"),
                new SseOutbox.Row(12, "incident_created", "ns-b", "{\"title\":\"MANQUE-B\"}")));
        SseEventService s = service(ob);
        s.subscribe(Set.of("ns-a"), 10L); s.subscribe(Set.of(), 10L);
        assertThat(count(made.get(0), "MANQUE-A")).isEqualTo(1); assertThat(count(made.get(0), "MANQUE-B")).isZero();   // restreint : isolé
        assertThat(count(made.get(1), "MANQUE-A")).isEqualTo(1); assertThat(count(made.get(1), "MANQUE-B")).isEqualTo(1);
        s.deliver(12, "incident_created", "ns-b", "{\"title\":\"MANQUE-B\"}");                                            // le poller le repasse : dédoublonné
        assertThat(count(made.get(1), "MANQUE-B")).isEqualTo(1);
        s.deliver(13, "incident_created", "ns-a", "{\"title\":\"NOUVEAU\"}"); assertThat(count(made.get(1), "NOUVEAU")).isEqualTo(1);
    }
    @Test void withoutLastEventId_noReplay() {
        SseOutbox ob = mock(SseOutbox.class); service(ob).subscribe(Set.of());
        verify(ob, org.mockito.Mockito.never()).replay(org.mockito.ArgumentMatchers.anyLong(), org.mockito.ArgumentMatchers.anyInt(), org.mockito.ArgumentMatchers.anyInt());
    }
    @Test void outboxFailure_fallsBackToLocalDelivery() {
        SseOutbox ob = mock(SseOutbox.class); when(ob.append(anyString(), anyString(), anyString())).thenThrow(new RuntimeException("db down"));
        SseEventService s = service(ob); s.subscribe(Set.of());
        s.broadcast("incident_created", "ns", Map.of("title", "REPLI"));
        assertThat(count(made.get(0), "REPLI")).isEqualTo(1);
    }
    @Test void poller_firstPlacesTheCursorAtTheEnd_thenDeliversNewRowsInOrder_andSurvivesErrors() {
        SseOutbox ob = mock(SseOutbox.class); SseEventService events = mock(SseEventService.class);
        when(ob.maxId()).thenReturn(100L);
        when(ob.after(100L, 200)).thenReturn(List.of(new SseOutbox.Row(101, "a", "ns", "{}"), new SseOutbox.Row(102, "b", "ns", "{}")));
        SseOutboxPoller p = new SseOutboxPoller(ob, events);
        p.poll(); assertThat(p.lastId()).isEqualTo(100L); verifyNoInteractions(events);
        p.poll(); assertThat(p.lastId()).isEqualTo(102L);
        org.mockito.InOrder o = org.mockito.Mockito.inOrder(events); o.verify(events).deliver(101, "a", "ns", "{}"); o.verify(events).deliver(102, "b", "ns", "{}");
        when(ob.after(102L, 200)).thenThrow(new RuntimeException("db")); p.poll(); assertThat(p.lastId()).isEqualTo(102L);
    }
}