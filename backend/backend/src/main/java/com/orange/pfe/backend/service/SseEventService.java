package com.orange.pfe.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Diffusion SSE avec ISOLATION par espace de noms : un abonné dont l'ensemble d'espaces autorisés est non vide
 * (compte USER restreint, voir {@code TenantContext}) ne reçoit QUE les évènements de ces espaces ; un évènement sans
 * espace connu n'est remis qu'aux abonnés non restreints (échec fermé).
 */
@Service
public class SseEventService {

    private static final Logger log = LoggerFactory.getLogger(SseEventService.class);
    private static final long SSE_TIMEOUT = 300_000L; // 5 minutes

    private record Subscriber(SseEmitter emitter, Set<String> allowedNamespaces) {
        boolean accepts(String namespace) {
            return allowedNamespaces.isEmpty() || (namespace != null && allowedNamespaces.contains(namespace));
        }
    }

    private final Map<Long, Subscriber> emitters = new ConcurrentHashMap<>();
    private final AtomicLong counter = new AtomicLong(0);
    private final ObjectMapper objectMapper = new ObjectMapper();

    protected SseEmitter newEmitter() { return new SseEmitter(SSE_TIMEOUT); }

    /** @param allowedNamespaces vide = abonné non restreint (voit tout). */
    public SseEmitter subscribe(Set<String> allowedNamespaces) {
        long id = counter.incrementAndGet();
        SseEmitter emitter = newEmitter();
        emitters.put(id, new Subscriber(emitter, allowedNamespaces == null ? Set.of() : Set.copyOf(allowedNamespaces)));

        emitter.onCompletion(() -> emitters.remove(id));
        emitter.onTimeout(() -> emitters.remove(id));
        emitter.onError(e -> emitters.remove(id));

        try {
            emitter.send(SseEmitter.event().name("connected").data("{\"status\":\"connected\"}"));
        } catch (IOException e) {
            emitters.remove(id);
        }

        log.debug("SSE client connected (id={}), total={}", id, emitters.size());
        return emitter;
    }

    /** Évènement sans espace de noms : uniquement les abonnés non restreints. */
    public void broadcast(String eventName, Object payload) {
        broadcast(eventName, null, payload);
    }

    public void broadcast(String eventName, String namespace, Object payload) {
        if (emitters.isEmpty()) return;
        String json;
        try {
            json = objectMapper.writeValueAsString(payload);
        } catch (Exception e) {
            log.warn("SSE serialize error: {}", e.getMessage());
            return;
        }
        emitters.forEach((id, sub) -> {
            if (!sub.accepts(namespace)) return;
            try {
                sub.emitter().send(SseEmitter.event().name(eventName).data(json));
            } catch (Exception ex) {
                emitters.remove(id);
            }
        });
    }
}