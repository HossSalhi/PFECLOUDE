package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIOpsAnalyzeResponse;
import com.orange.pfe.backend.dto.AlertWebhookRequest;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.core.Alert;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.AlertSource;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.AlertRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Handles incoming alert webhooks. Retrait de l'IA (11 nov. 2026) : chaque webhook est d'abord confronté aux marqueurs
 * d'état Kubernetes par le pipeline déterministe (analyze) qui, si une panne est confirmée, crée l'incident typé et
 * démarre le workflow Camunda selon la politique de remédiation. Sinon, l'incident est créé depuis la charge utile
 * du webhook (source mappée sur l'origine de l'alerte).
 */
@Service
public class OrchestrationService {

    private static final Logger log = LoggerFactory.getLogger(OrchestrationService.class);

    private static final Pattern NAMESPACE_HINT = Pattern.compile("(?i)\\bnamespace[=: ]+([a-z0-9-]+)");
    private static final Pattern APP_HINT = Pattern.compile("(?i)\\bapp[=: ]+([a-z0-9-]+)");
    private static final Pattern KNOWN_APPS = Pattern.compile(
            "(?i)\\b(backend|frontend|postgres|demo-app|grafana|prometheus|loki|camunda)\\b");

    private final IncidentRepository incidentRepo;
    private final AlertRepository alertRepo;
    private final ActionService actionService;
    private final IncidentHistoryService historyService;
    private final AIOpsOrchestrationService aiOpsOrchestrationService;

    public OrchestrationService(
            IncidentRepository incidentRepo,
            AlertRepository alertRepo,
            ActionService actionService,
            IncidentHistoryService historyService,
            AIOpsOrchestrationService aiOpsOrchestrationService
    ) {
        this.incidentRepo = incidentRepo;
        this.alertRepo = alertRepo;
        this.actionService = actionService;
        this.historyService = historyService;
        this.aiOpsOrchestrationService = aiOpsOrchestrationService;
    }

    public void handleIncomingAlert(AlertWebhookRequest req) {
        AlertSource alertSource = req.getSource() != null ? req.getSource() : AlertSource.EXTERNAL;
        String message = req.getMessage() == null ? "" : req.getMessage();

        String namespace = extractNamespace(message);
        String app = extractApp(message);

        // 1. Pipeline déterministe d'abord : si les marqueurs d'état Kubernetes confirment une panne, analyze() crée
        //    l'incident typé et démarre le workflow Camunda selon la politique de remédiation.
        Long id = tryPipeline(namespace, app);
        if (id != null) {
            recordAlertAndNotification(id, message, alertSource);
            log.info("Webhook handled by the deterministic pipeline -> incident #{}", id);
            return;
        }

        // 2. Repli : le pipeline n'a rien créé. On crée l'incident depuis la charge utile du webhook.
        IncidentSeverity severity = parseSeverity(req.getSeverity());
        Incident incident = createIncidentFromWebhook(message, severity, alertSource, namespace, app);
        recordAlertAndNotification(incident.getId(), message, alertSource);

        boolean shouldTriggerRemediation =
                severity == IncidentSeverity.CRITICAL || severity == IncidentSeverity.HIGH;

        if (shouldTriggerRemediation) {
            try {
                aiOpsOrchestrationService.executeRecommendation(incident.getId());
                log.info("Fallback path: Camunda workflow started for incident #{}", incident.getId());
            } catch (Exception ex) {
                log.warn("Failed to start Camunda workflow for incident #{}: {}",
                        incident.getId(), ex.getMessage());
            }
        } else {
            incident.setStatus(IncidentStatus.OPEN);
            historyService.record(incident, IncidentStatus.OPEN,
                    "Incident enregistre (severite faible/moyenne) - action manuelle requise");
            incidentRepo.save(incident);
        }
    }

    private Long tryPipeline(String namespace, String app) {
        try {
            AIOpsAnalyzeRequest analyzeReq = new AIOpsAnalyzeRequest();
            analyzeReq.setNamespace(namespace);
            analyzeReq.setApp(app);
            analyzeReq.setWindowMinutes(10);
            analyzeReq.setCreateIncident(true);

            AIOpsAnalyzeResponse response = aiOpsOrchestrationService.analyze(analyzeReq);
            if (response != null && response.isIncidentCreated()) {
                return response.getIncidentId();
            }
        } catch (Exception ex) {
            log.warn("Deterministic pipeline failed for webhook (ns={} app={}): {}", namespace, app, ex.getMessage());
        }
        return null;
    }

    private Incident createIncidentFromWebhook(String message, IncidentSeverity severity,
                                               AlertSource alertSource, String namespace, String app) {
        Incident incident = new Incident();
        incident.setTitle("Auto Incident from Alertmanager");
        incident.setSeverity(severity);
        incident.setStatus(IncidentStatus.NEW);
        incident.setNamespace(namespace);
        incident.setApp(app);
        incident.setSource(mapAlertToIncidentSource(alertSource));
        incident.setDescription(message);

        incident = incidentRepo.save(incident);
        historyService.record(incident, IncidentStatus.NEW, "Incident created from alert");
        return incident;
    }

    private void recordAlertAndNotification(Long incidentId, String message, AlertSource alertSource) {
        Incident incident = incidentRepo.findById(incidentId).orElse(null);
        if (incident == null) return;

        Alert alert = new Alert();
        alert.setMessage(message);
        alert.setSource(alertSource);
        alert.setIncident(incident);
        alertRepo.save(alert);

        String username = getCurrentUsername();
        actionService.createAction(incident, ActionType.NOTIFICATION,
                "Notification envoyee aux equipes NOC", username);
    }

    private IncidentSeverity parseSeverity(String raw) {
        if (raw == null || raw.isBlank()) return IncidentSeverity.MEDIUM;
        try {
            return IncidentSeverity.valueOf(raw.toUpperCase());
        } catch (Exception e) {
            return IncidentSeverity.MEDIUM;
        }
    }

    private String extractNamespace(String message) {
        Matcher m = NAMESPACE_HINT.matcher(message);
        if (m.find()) return m.group(1).toLowerCase();
        return "pfe";
    }

    private String extractApp(String message) {
        Matcher m = APP_HINT.matcher(message);
        if (m.find()) return m.group(1).toLowerCase();
        Matcher k = KNOWN_APPS.matcher(message);
        if (k.find()) return k.group(1).toLowerCase();
        return "backend";
    }

    private IncidentSource mapAlertToIncidentSource(AlertSource alertSource) {
        return switch (alertSource) {
            case SYSTEM -> IncidentSource.PROMETHEUS;
            case USER -> IncidentSource.MANUAL;
            case EXTERNAL -> IncidentSource.OTHER;
        };
    }

    private String getCurrentUsername() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.isAuthenticated()) {
            return auth.getName();
        }
        return "SYSTEM";
    }
}
