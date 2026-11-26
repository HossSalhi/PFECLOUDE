package com.orange.pfe.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Diffusion SSE DISTRIBUÉE et ISOLÉE par espace de noms.
 * - Distribution : {@link #broadcast} écrit l'évènement dans l'outbox PostgreSQL ; {@link SseOutboxPoller} le remet, sur chaque pod,
 *   aux abonnés locaux. Si l'outbox est indisponible, repli sur la remise locale (aucun évènement perdu pour les abonnés du pod).
 * - Reprise : chaque évènement porte son id (Last-Event-ID) ; un client reconnecté reçoit les évènements manqués (≤ 5 min).
 * - Isolation : un abonné dont l'ensemble d'espaces autorisés est non vide ne reçoit QUE ces espaces ; un évènement sans espace
 *   n'est remis qu'aux abonnés non restreints (échec fermé).
 */
@Service
public class SseEventService {

    private static final Logger log = LoggerFactory.getLogger(SseEventService.class);
    private static final long SSE_TIMEOUT = 300_000L; // 5 minutes
    static final int REPLAY_MAX_AGE_SECONDS = 300;
    static final int REPLAY_MAX_EVENTS = 500;

    private static final class Subscriber {
        final SseEmitter emitter;
        final Set<String> allowedNamespaces;
        final AtomicLong lastSent = new AtomicLong(0);
        Subscriber(SseEmitter e, Set<String> ns) { this.emitter = e; this.allowedNamespaces = ns; }
        boolean accepts(String namespace) {
            return allowedNamespaces.isEmpty() || (namespace != null && allowedNamespaces.contains(namespace));
        }
    }

    private final Map<Long, Subscriber> emitters = new ConcurrentHashMap<>();
    private final AtomicLong counter = new AtomicLong(0);
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final SseOutbox outbox;

    public SseEventService() { this(null); }

    @Autowired
    public SseEventService(@Autowired(required = false) SseOutbox outbox) { this.outbox = outbox; }

    protected SseEmitter newEmitter() { return new SseEmitter(SSE_TIMEOUT); }

    public SseEmitter subscribe(Set<String> allowedNamespaces) { return subscribe(allowedNamespaces, null); }

    /** @param allowedNamespaces vide = abonné non restreint ; @param lastEventId dernier id reçu par le client (reprise), ou null. */
    public SseEmitter subscribe(Set<String> allowedNamespaces, Long lastEventId) {
        long id = counter.incrementAndGet();
        SseEmitter emitter = newEmitter();
        Subscriber sub = new Subscriber(emitter, allowedNamespaces == null ? Set.of() : Set.copyOf(allowedNamespaces));
        synchronized (sub) {
            emitters.put(id, sub);
            emitter.onCompletion(() -> emitters.remove(id));
            emitter.onTimeout(() -> emitters.remove(id));
            emitter.onError(e -> emitters.remove(id));
            try {
                emitter.send(SseEmitter.event().name("connected").data("{\"status\":\"connected\"}"));
            } catch (IOException e) {
                emitters.remove(id);
                return emitter;
            }
            if (lastEventId != null && lastEventId >= 0 && outbox != null) {
                sub.lastSent.set(lastEventId);
                try {
                    List<SseOutbox.Row> missed = outbox.replay(lastEventId, REPLAY_MAX_AGE_SECONDS, REPLAY_MAX_EVENTS);
                    for (SseOutbox.Row r : missed) { send(id, sub, r.id(), r.eventName(), r.namespace(), r.payload()); }
                } catch (Exception e) {
                    log.warn("Reprise SSE impossible : {}", e.getClass().getSimpleName());
                }
            }
        }
        log.debug("SSE client connected (id={}), total={}", id, emitters.size());
        return emitter;
    }

    /** Évènement sans espace de noms : uniquement les abonnés non restreints. */
    public void broadcast(String eventName, Object payload) { broadcast(eventName, null, payload); }

    public void broadcast(String eventName, String namespace, Object payload) {
        String json;
        try {
            json = objectMapper.writeValueAsString(payload);
        } catch (Exception e) {
            log.warn("SSE serialize error: {}", e.getMessage());
            return;
        }
        if (outbox != null) {
            try {
                outbox.append(eventName, namespace, json);
                return;                      // remis par SseOutboxPoller sur TOUS les pods, ce pod compris
            } catch (Exception e) {
                log.warn("Outbox SSE indisponible, remise locale : {}", e.getClass().getSimpleName());
            }
        }
        deliver(0, eventName, namespace, json);
    }

    /** Remise aux abonnés LOCAUX ; id {@code > 0} = id d'outbox (dédoublonnage et reprise), 0 = sans id. */
    void deliver(long eventId, String eventName, String namespace, String json) {
        if (emitters.isEmpty()) return;
        emitters.forEach((id, sub) -> {
            synchronized (sub) { send(id, sub, eventId, eventName, namespace, json); }
        });
    }

    private void send(long id, Subscriber sub, long eventId, String eventName, String namespace, String json) {
        if (!sub.accepts(namespace)) return;
        if (eventId > 0) {
            if (eventId <= sub.lastSent.get()) return;       // déjà remis (reprise)
            sub.lastSent.set(eventId);
        }
        try {
            SseEmitter.SseEventBuilder b = SseEmitter.event().name(eventName).data(json);
            if (eventId > 0) b = b.id(Long.toString(eventId));
            sub.emitter.send(b);
        } catch (Exception ex) {
            emitters.remove(id);
        }
    }
}