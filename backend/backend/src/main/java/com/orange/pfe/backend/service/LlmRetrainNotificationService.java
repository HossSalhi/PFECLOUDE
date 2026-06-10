package com.orange.pfe.backend.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.Map;

/**
 * Polls the AI service /mlops/status endpoint at a configurable interval.
 * When the AI signals that LLM fine-tuning is recommended (drift + enough
 * new incidents), sends a notification so the operator knows to run
 * python scripts/llm_continuous_learning.py.
 *
 * Enabled by default; disable with LLM_RETRAIN_NOTIFICATION_ENABLED=false.
 */
@Service
@ConditionalOnProperty(name = "llm.retrain.notification.enabled", havingValue = "true", matchIfMissing = true)
public class LlmRetrainNotificationService {

    private static final Logger log = LoggerFactory.getLogger(LlmRetrainNotificationService.class);

    private final RestClient restClient;
    private final NotificationService notificationService;
    private final SseEventService sseEventService;

    private boolean lastRecommendation = false;

    public LlmRetrainNotificationService(
            RestClient.Builder builder,
            @Value("${aiops.service.base-url}") String aiBaseUrl,
            @Value("${aiops.service.api-key:}") String apiKey,
            NotificationService notificationService,
            SseEventService sseEventService
    ) {
        this.restClient = builder
                .baseUrl(aiBaseUrl)
                .requestInterceptor((req, body, execution) -> {
                    if (apiKey != null && !apiKey.isBlank()) req.getHeaders().set("X-API-Key", apiKey);
                    return execution.execute(req, body);
                })
                .build();
        this.notificationService = notificationService;
        this.sseEventService = sseEventService;
    }

    @Scheduled(
            fixedDelayString  = "${llm.retrain.notification.poll-interval-ms:3600000}",
            initialDelayString = "${llm.retrain.notification.poll-initial-delay-ms:120000}"
    )
    @SuppressWarnings("unchecked")
    public void checkAndNotify() {
        try {
            Map<String, Object> status = restClient.get()
                    .uri("/mlops/status")
                    .retrieve()
                    .body(Map.class);

            if (status == null) return;

            Map<String, Object> llm = (Map<String, Object>) status.get("llm");
            if (llm == null) return;

            boolean recommended = Boolean.TRUE.equals(llm.get("retrain_recommended"));
            Object newIncidents = llm.get("new_incidents");
            Object minRequired  = llm.get("min_incidents_required");

            if (recommended && !lastRecommendation) {
                log.warn("LLM fine-tuning recommended: {} new incidents (min={}). "
                        + "Run: python scripts/llm_continuous_learning.py",
                        newIncidents, minRequired);

                sseEventService.broadcast("llm_retrain_recommended", Map.of(
                        "new_incidents",  newIncidents  != null ? newIncidents  : 0,
                        "min_incidents",  minRequired   != null ? minRequired   : 0,
                        "hint", "Run: python scripts/llm_continuous_learning.py"
                ));

                notificationService.notifyFineTuningCompleted(
                        "RECOMMENDED",
                        newIncidents instanceof Number n ? n.intValue() : 0,
                        0.0
                );
            } else if (!recommended && lastRecommendation) {
                log.info("LLM fine-tuning no longer recommended (threshold not met)");
            }

            lastRecommendation = recommended;
        } catch (Exception ex) {
            log.debug("LLM retrain status poll failed (non-critical): {}", ex.getMessage());
        }
    }
}
