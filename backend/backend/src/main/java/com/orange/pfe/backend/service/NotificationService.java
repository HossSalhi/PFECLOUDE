package com.orange.pfe.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.dto.WorkflowLogDTO;
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

    @Value("${notification.webhook.pagerduty-routing-key:}")
    private String pagerdutyRoutingKey;

    @Value("${notification.webhook.enabled:false}")
    private boolean enabled;

    /** slack (default) | teams | pagerduty | generic */
    @Value("${notification.webhook.type:slack}")
    private String webhookType;

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
        sseEventService.broadcast("incident_created", incident.getNamespace(), Map.of(
                "id",          incident.getId(),
                "title",       incident.getTitle(),
                "severity",    incident.getSeverity() != null ? incident.getSeverity().name() : "UNKNOWN",
                "status",      incident.getStatus() != null ? incident.getStatus().name() : "NEW",
                "description", incident.getDescription() != null ? incident.getDescription() : ""
        ));
        if (!shouldNotify(incident.getSeverity())) return;
        sendWebhook(buildTypedWebhookPayload(incident, "🚨 Nouvel incident détecté"));
        emailService.ifPresent(e -> e.sendIncidentDetected(incident));
    }

    /**
     * VMDOWN-POLICY-20261105 — notification INTERFACE SEULE (SSE) : aucun webhook, aucun e-mail. Utilisée pour la
     * VM en DÉTECTION SEULE (.160) : l'incident est visible et historisé, l'administrateur n'est pas sollicité
     * automatiquement et rien n'est escaladé.
     */
    public void notifyNewIncidentUiOnly(Incident incident) {
        sseEventService.broadcast("incident_created", incident.getNamespace(), Map.of(
                "id",          incident.getId(),
                "title",       incident.getTitle(),
                "severity",    incident.getSeverity() != null ? incident.getSeverity().name() : "UNKNOWN",
                "status",      incident.getStatus() != null ? incident.getStatus().name() : "NEW",
                "description", incident.getDescription() != null ? incident.getDescription() : ""
        ));
    }

    /** Pendant de {@link #notifyNewIncidentUiOnly} à la clôture : SSE seulement, ni webhook ni e-mail. */
    public void notifyIncidentResolvedUiOnly(Incident incident) {
        sseEventService.broadcast("incident_resolved", incident.getNamespace(), Map.of(
                "id",     incident.getId(),
                "title",  incident.getTitle(),
                "status", "RESOLVED"
        ));
    }

    /**
     * Échec de remédiation, INTERFACE SEULE (SSE) : ni webhook ni e-mail. Utilisé pour la VM dont la politique limite
     * les notifications à l'interface (.160) : l'état d'échec est explicite et visible, personne n'est sollicité.
     */
    public void notifyRemediationFailedUiOnly(Incident incident, String reason) {
        sseEventService.broadcast("remediation_failed", incident.getNamespace(), Map.of(
                "id",     incident.getId(),
                "title",  incident.getTitle(),
                "reason", reason != null ? reason : "unknown"
        ));
    }

    public void notifyIncidentUpdated(Incident incident) {
        sseEventService.broadcast("incident_updated", incident.getNamespace(), Map.of(
                "id",       incident.getId(),
                "title",    incident.getTitle(),
                "severity", incident.getSeverity() != null ? incident.getSeverity().name() : "UNKNOWN",
                "status",   incident.getStatus() != null ? incident.getStatus().name() : "UNKNOWN"
        ));
    }

    public void notifyEscalation(Incident incident) {
        sseEventService.broadcast("incident_escalated", incident.getNamespace(), Map.of(
                "id",    incident.getId(),
                "title", incident.getTitle()
        ));
        sendWebhook(buildTypedWebhookPayload(incident, "⚠️ Escalade — approbation humaine requise"));
        emailService.ifPresent(e -> e.sendApprovalRequired(incident));
    }

    public void notifyIncidentResolved(Incident incident) {
        sseEventService.broadcast("incident_resolved", incident.getNamespace(), Map.of(
                "id",     incident.getId(),
                "title",  incident.getTitle(),
                "status", "RESOLVED"
        ));
        sendWebhook(buildTypedWebhookPayload(incident, "✅ Incident résolu"));
        emailService.ifPresent(e -> e.sendIncidentResolved(incident));
    }

    /** Résolution avec rapport COMPLET (détection + décision IA + trace Camunda/kubectl). */
    public void notifyIncidentResolvedWithTrace(Incident incident, List<WorkflowLogDTO> workflowLogs) {
        sseEventService.broadcast("incident_resolved", incident.getNamespace(), Map.of(
                "id",     incident.getId(),
                "title",  incident.getTitle(),
                "status", "RESOLVED"
        ));
        sendWebhook(buildTypedWebhookPayload(incident, "✅ Incident résolu"));
        emailService.ifPresent(e -> e.sendIncidentResolvedWithTrace(incident, workflowLogs));
    }

    public void notifyRemediationFailed(Incident incident, String reason) {
        sseEventService.broadcast("remediation_failed", incident.getNamespace(), Map.of(
                "id",     incident.getId(),
                "title",  incident.getTitle(),
                "reason", reason != null ? reason : "unknown"
        ));
        sendWebhook(buildTypedWebhookPayload(incident, "❌ Remédiation échouée — " + reason));
        emailService.ifPresent(e -> e.sendRemediationFailed(incident, reason));
    }

    public void notifyRemediationFailedWithTrace(Incident incident, String reason, List<WorkflowLogDTO> workflowLogs) {
        sseEventService.broadcast("remediation_failed", incident.getNamespace(), Map.of(
                "id",     incident.getId(),
                "title",  incident.getTitle(),
                "reason", reason != null ? reason : "unknown"
        ));
        sendWebhook(buildTypedWebhookPayload(incident, "❌ Remédiation échouée — " + reason));
        emailService.ifPresent(e -> e.sendRemediationFailedWithTrace(incident, reason, workflowLogs));
    }

    public void notifyHumanTaskCreated(HumanTask task) {
        Incident incident = task.getWorkflowInstance().getIncident();
        sseEventService.broadcast("human_task_created", incident.getNamespace(), Map.of(
                "taskId",    task.getId(),
                "taskName",  task.getTaskName() != null ? task.getTaskName() : "",
                "incidentId", incident.getId(),
                "severity",  incident.getSeverity() != null ? incident.getSeverity().name() : "UNKNOWN"
        ));
        sendWebhook(buildTypedWebhookPayload(incident, "⏳ Tâche humaine en attente — " + task.getTaskName()));
        emailService.ifPresent(e -> e.sendHumanTaskCreated(task));
    }

    public void notifyHumanTaskDecided(HumanTask task, String actor) {
        Incident incident = task.getWorkflowInstance().getIncident();
        sseEventService.broadcast("human_task_decided", incident.getNamespace(), Map.of(
                "taskId",    task.getId(),
                "decision",  task.getDecision().name(),
                "incidentId", incident.getId(),
                "actor",     actor != null ? actor : "SYSTEM"
        ));
        emailService.ifPresent(e -> e.sendHumanTaskDecided(task, actor));
    }

    public void notifyRemediationExecuted(Incident incident, String actionType, String target) {
        sseEventService.broadcast("remediation_executed", incident.getNamespace(), Map.of(
                "incidentId", incident.getId(),
                "actionType", actionType != null ? actionType : "",
                "target",     target != null ? target : ""
        ));
        emailService.ifPresent(e -> e.sendRemediationExecuted(incident, actionType, target));
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

    private Map<String, Object> buildTypedWebhookPayload(Incident incident, String header) {
        return switch (webhookType == null ? "slack" : webhookType.toLowerCase()) {
            case "teams"     -> buildTeamsPayload(incident, header);
            case "pagerduty" -> buildPagerDutyPayload(incident, header);
            case "generic"   -> buildGenericPayload(incident, header);
            default          -> buildWebhookPayload(incident, header);
        };
    }

    /** Microsoft Teams Incoming Webhook — MessageCard format */
    private Map<String, Object> buildTeamsPayload(Incident incident, String header) {
        String color = incident.getSeverity() == IncidentSeverity.CRITICAL ? "FF0000" : "FFA500";
        return Map.of(
                "@type", "MessageCard",
                "@context", "http://schema.org/extensions",
                "summary", header,
                "themeColor", color,
                "title", "HELIOS — " + header,
                "text", incident.getDescription() != null ? incident.getDescription() : "",
                "sections", List.of(Map.of("facts", List.of(
                        Map.of("name", "Incident ID",  "value", String.valueOf(incident.getId())),
                        Map.of("name", "Sévérité",     "value", incident.getSeverity() != null ? incident.getSeverity().name() : "N/A"),
                        Map.of("name", "Statut",       "value", incident.getStatus()   != null ? incident.getStatus().name()   : "N/A"),
                        Map.of("name", "Namespace",    "value", incident.getNamespace() != null ? incident.getNamespace()      : "N/A"),
                        Map.of("name", "App",          "value", incident.getApp()       != null ? incident.getApp()            : "N/A")
                )))
        );
    }

    /** PagerDuty Events API v2 — trigger event */
    private Map<String, Object> buildPagerDutyPayload(Incident incident, String header) {
        String pdSeverity = incident.getSeverity() == IncidentSeverity.CRITICAL ? "critical"
                : incident.getSeverity() == IncidentSeverity.HIGH ? "error" : "warning";
        return Map.of(
                "routing_key", pagerdutyRoutingKey != null ? pagerdutyRoutingKey : "",
                "event_action", "trigger",
                "payload", Map.of(
                        "summary", header + " — " + incident.getTitle(),
                        "severity", pdSeverity,
                        "source", "HELIOS/K8s",
                        "custom_details", Map.of(
                                "incidentId", incident.getId(),
                                "namespace",  incident.getNamespace() != null ? incident.getNamespace() : "",
                                "app",        incident.getApp()       != null ? incident.getApp()       : "",
                                "status",     incident.getStatus()    != null ? incident.getStatus().name() : ""
                        )
                )
        );
    }

    /** Generic flat JSON — works with any webhook consumer */
    private Map<String, Object> buildGenericPayload(Incident incident, String header) {
        return Map.of(
                "source",      "HELIOS",
                "header",      header,
                "incidentId",  incident.getId(),
                "title",       incident.getTitle(),
                "description", incident.getDescription() != null ? incident.getDescription() : "",
                "severity",    incident.getSeverity()  != null ? incident.getSeverity().name()  : "N/A",
                "status",      incident.getStatus()    != null ? incident.getStatus().name()    : "N/A",
                "namespace",   incident.getNamespace() != null ? incident.getNamespace()        : "",
                "app",         incident.getApp()       != null ? incident.getApp()              : ""
        );
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
