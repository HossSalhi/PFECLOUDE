package com.orange.pfe.backend.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Chaque pod lit l'outbox (curseur sur l'id) et remet les évènements à SES abonnés : un évènement produit sur n'importe quel pod
 * atteint les abonnés de tous les pods, une seule fois par abonné. Au démarrage le curseur se place à la fin : l'historique n'est
 * rejoué qu'à la demande d'un client (Last-Event-ID). Délai de remise ≈ 1 s.
 */
@Component
public class SseOutboxPoller {

    private static final Logger log = LoggerFactory.getLogger(SseOutboxPoller.class);
    private final SseOutbox outbox;
    private final SseEventService events;
    private volatile long lastId = -1;
    private int ticks = 0;

    public SseOutboxPoller(SseOutbox outbox, SseEventService events) {
        this.outbox = outbox;
        this.events = events;
    }

    @Scheduled(fixedDelayString = "${helios.sse.poll-interval-ms:1000}", initialDelay = 3000)
    public void poll() {
        try {
            if (lastId < 0) { lastId = outbox.maxId(); return; }
            List<SseOutbox.Row> rows = outbox.after(lastId, 200);
            for (SseOutbox.Row r : rows) {
                events.deliver(r.id(), r.eventName(), r.namespace(), r.payload());
                lastId = r.id();
            }
            if (++ticks % 300 == 0) { outbox.purgeOlderThanOneHour(); }
        } catch (Exception e) {
            log.warn("Outbox SSE indisponible : {}", e.getClass().getSimpleName());
        }
    }

    long lastId() { return lastId; }
}