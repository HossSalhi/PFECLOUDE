package com.orange.pfe.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

@Service
public class SseEventService {

    private static final Logger log = LoggerFactory.getLogger(SseEventService.class);
    private static final long SSE_TIMEOUT = 300_000L; // 5 minutes

    private final Map<Long, SseEmitter> emitters = new ConcurrentHashMap<>();
    private final AtomicLong counter = new AtomicLong(0);
    private final ObjectMapper objectMapper = new ObjectMapper();

    public SseEmitter subscribe() {
        long id = counter.incrementAndGet();
        SseEmitter emitter = new SseEmitter(SSE_TIMEOUT);
        emitters.put(id, emitter);

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

    public void broadcast(String eventName, Object payload) {
        if (emitters.isEmpty()) return;
        String json;
        try {
            json = objectMapper.writeValueAsString(payload);
        } catch (Exception e) {
            log.warn("SSE serialize error: {}", e.getMessage());
            return;
        }
        emitters.forEach((id, emitter) -> {
            try {
                emitter.send(SseEmitter.event().name(eventName).data(json));
            } catch (Exception ex) {
                emitters.remove(id);
            }
        });
    }
}
