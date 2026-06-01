package com.orange.pfe.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.human.HumanTask;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Orchestrates all notifications: SSE (real-time), webhook, and email.
 */
@Service
public class NotificationService {

    private static final Logger log = LoggerFactory.getLogger(NotificationService.class);

    @Value("${notification.webhook.url:}")
    private String webhookUrl;

    @Value("${notification.webhook.enabled:false}")
    private boolean enabled;

    @Value("${notification.min-severity:HIGH}")
    private String minSeverity;

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    private final SseEventService sseEventService;
    private final Optional<EmailNotificationService> emailService;

    public NotificationService(SseEventService sseEventService,
                                @Autowired(required = false) EmailNotificationService emailService) {
        this.sseEventService = sseEventService;
        this.emailService = Optional.ofNullable(emailService);
    }

    public void notifyNewIncident(Incident incident) {
        sseEventService.broadcast("incident_created", Map.of(
                "id",          incident.getId(),
                "title",       incident.getTitle(),
                "severity",    incident.getSeverity() != null ? incident.getSeverity().name() : "UNKNOWN",
                "status",      incident.getStatus() != null ? incident.getStatus().name() : "NEW",
                "description", incident.getDescription() != null ? incident.getDescription() : ""
        ));
        if (!shouldNotify(incident.getSeverity())) return;
        sendWebhook(buildWebhookPayload(incident, "🚨 Nouvel incident détecté"));
        emailService.ifPresent(e -> e.sendIncidentDetected(incident));
    }

    public void notifyIncidentUpdated(Incident incident) {
        sseEventService.broadcast("incident_updated", Map.of(
                "id",       incident.getId(),
                "title",    incident.getTitle(),
                "severity", incident.getSeverity() != null ? incident.getSeverity().name() : "UNKNOWN",
                "status",   incident.getStatus() != null ? incident.getStatus().name() : "UNKNOWN"
        ));
    }

    public void notifyEscalation(Incident incident) {
        sseEventService.broadcast("incident_escalated", Map.of(
                "id",    incident.getId(),
                "title", incident.getTitle()
        ));
        sendWebhook(buildWebhookPayload(incident, "⚠️ Escalade — approbation humaine requise"));
        emailService.ifPresent(e -> e.sendApprovalRequired(incident));
    }

    public void notifyIncidentResolved(Incident incident) {
        sseEventService.broadcast("incident_resolved", Map.of(
                "id",     incident.getId(),
                "title",  incident.getTitle(),
                "status", "RESOLVED"
        ));
        sendWebhook(buildWebhookPayload(incident, "✅ Incident résolu"));
        emailService.ifPresent(e -> e.sendIncidentResolved(incident));
    }

    public void notifyRemediationFailed(Incident incident, String reason) {
        sseEventService.broadcast("remediation_failed", Map.of(
                "id",     incident.getId(),
                "title",  incident.getTitle(),
                "reason", reason != null ? reason : "unknown"
        ));
        sendWebhook(buildWebhookPayload(incident, "❌ Remédiation échouée — " + reason));
        emailService.ifPresent(e -> e.sendRemediationFailed(incident, reason));
    }

    public void notifyHumanTaskCreated(HumanTask task) {
        Incident incident = task.getWorkflowInstance().getIncident();
        sseEventService.broadcast("human_task_created", Map.of(
                "taskId",    task.getId(),
                "taskName",  task.getTaskName() != null ? task.getTaskName() : "",
                "incidentId", incident.getId(),
                "severity",  incident.getSeverity() != null ? incident.getSeverity().name() : "UNKNOWN"
        ));
        sendWebhook(buildWebhookPayload(incident, "⏳ Tâche humaine en attente — " + task.getTaskName()));
        emailService.ifPresent(e -> e.sendHumanTaskCreated(task));
    }

    public void notifyHumanTaskDecided(HumanTask task, String actor) {
        Incident incident = task.getWorkflowInstance().getIncident();
        sseEventService.broadcast("human_task_decided", Map.of(
                "taskId",    task.getId(),
                "decision",  task.getDecision().name(),
                "incidentId", incident.getId(),
                "actor",     actor != null ? actor : "SYSTEM"
        ));
        emailService.ifPresent(e -> e.sendHumanTaskDecided(task, actor));
    }

    public void notifyRemediationExecuted(Incident incident, String actionType, String target) {
        sseEventService.broadcast("remediation_executed", Map.of(
                "incidentId", incident.getId(),
                "actionType", actionType != null ? actionType : "",
                "target",     target != null ? target : ""
        ));
        emailService.ifPresent(e -> e.sendRemediationExecuted(incident, actionType, target));
    }

    public void notifyFineTuningCompleted(String status, int incidents, double f1Score) {
        emailService.ifPresent(e -> e.sendFineTuningCompleted(status, incidents, f1Score));
    }

    // ── Private ──────────────────────────────────────────────────────────────

    private boolean shouldNotify(IncidentSeverity severity) {
        if (severity == null) return false;
        return severity == IncidentSeverity.CRITICAL || severity == IncidentSeverity.HIGH;
    }

    private Map<String, Object> buildWebhookPayload(Incident incident, String header) {
        String text = String.format("%s\n*%s* — %s\nSévérité: `%s` | Statut: `%s`",
                header,
                incident.getTitle(),
                incident.getDescription() != null ? incident.getDescription() : "",
                incident.getSeverity() != null ? incident.getSeverity().name() : "N/A",
                incident.getStatus() != null ? incident.getStatus().name() : "N/A"
        );

        // Slack-compatible format (also works with Teams via Incoming Webhook)
        return Map.of("text", text, "attachments", List.of(
                Map.of(
                        "color",  incident.getSeverity() == IncidentSeverity.CRITICAL ? "danger" : "warning",
                        "fields", List.of(
                                Map.of("title", "ID",        "value", String.valueOf(incident.getId()), "short", true),
                                Map.of("title", "Sévérité",  "value", incident.getSeverity() != null ? incident.getSeverity().name() : "N/A", "short", true),
                                Map.of("title", "Source",    "value", incident.getSource() != null ? incident.getSource().name() : "N/A", "short", true),
                                Map.of("title", "Dashboard", "value", "http://localhost:4200/incidents", "short", false)
                        )
                )
        ));
    }

    private void sendWebhook(Map<String, Object> payload) {
        if (!enabled || webhookUrl == null || webhookUrl.isBlank()) {
            log.debug("Webhook disabled or URL not configured");
            return;
        }
        try {
            String body = objectMapper.writeValueAsString(payload);
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(webhookUrl))
                    .timeout(Duration.ofSeconds(5))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(body))
                    .build();
            HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            log.info("Webhook sent → HTTP {}", resp.statusCode());
        } catch (Exception e) {
            log.warn("Webhook failed: {}", e.getMessage());
        }
    }
}
