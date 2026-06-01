package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIOpsAnalyzeResponse;
import com.orange.pfe.backend.dto.AIPredictRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.dto.AlertWebhookRequest;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
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
 * Handles incoming alert webhooks. Every webhook is enriched by the AI
 * service: ML classification + LLM explanation + recommended action. When AI
 * agrees an incident is happening, it delegates to the full AIOps pipeline
 * (analyze) which also starts the Camunda workflow + Human Task per the
 * remediation policy. Otherwise we create the incident from the webhook
 * payload but still attach AI's explanation + recommended action so the
 * operator sees what the AI looked at.
 */
@Service
public class OrchestrationService {

    private static final Logger log = LoggerFactory.getLogger(OrchestrationService.class);

    private static final Pattern NAMESPACE_HINT = Pattern.compile("(?i)\\bnamespace[=: ]+([a-z0-9-]+)");
    private static final Pattern APP_HINT = Pattern.compile("(?i)\\bapp[=: ]+([a-z0-9-]+)");
    private static final Pattern KNOWN_APPS = Pattern.compile(
            "(?i)\\b(backend|frontend|ai-service|chromadb|postgres|demo-app|grafana|prometheus|loki|camunda|ollama)\\b");

    private final IncidentRepository incidentRepo;
    private final AlertRepository alertRepo;
    private final ActionService actionService;
    private final IncidentHistoryService historyService;
    private final AIOpsOrchestrationService aiOpsOrchestrationService;
    private final AIOpsClientService aiOpsClientService;

    public OrchestrationService(
            IncidentRepository incidentRepo,
            AlertRepository alertRepo,
            ActionService actionService,
            IncidentHistoryService historyService,
            AIOpsOrchestrationService aiOpsOrchestrationService,
            AIOpsClientService aiOpsClientService
    ) {
        this.incidentRepo = incidentRepo;
        this.alertRepo = alertRepo;
        this.actionService = actionService;
        this.historyService = historyService;
        this.aiOpsOrchestrationService = aiOpsOrchestrationService;
        this.aiOpsClientService = aiOpsClientService;
    }

    public void handleIncomingAlert(AlertWebhookRequest req) {
        AlertSource alertSource = req.getSource() != null ? req.getSource() : AlertSource.EXTERNAL;
        String message = req.getMessage() == null ? "" : req.getMessage();

        String namespace = extractNamespace(message);
        String app = extractApp(message);

        // 1. Call AI predict first. Used for two things:
        //    - if AI sees an incident, delegate to the full pipeline (which
        //      also creates the Camunda workflow + human task)
        //    - if not, we still attach the AI explanation to the fallback
        //      incident so the user sees the IA work in the UI.
        AIPredictResponse aiPrediction = tryPredict(namespace, app);

        if (aiPrediction != null && aiPrediction.isIncident()) {
            Long id = tryAiPipeline(namespace, app);
            if (id != null) {
                recordAlertAndNotification(id, message, alertSource);
                log.info("Webhook handled by AI pipeline -> incident #{}", id);
                return;
            }
            log.warn("AI agreed on incident but pipeline did not create one - falling back");
        }

        // 2. Fallback. AI either disagrees or pipeline failed. Create an
        //    incident from the webhook + enrich with whatever AI returned.
        IncidentSeverity severity = parseSeverity(req.getSeverity());
        Incident incident = createIncidentFromWebhook(message, severity, alertSource, namespace, app, aiPrediction);
        recordAlertAndNotification(incident.getId(), message, alertSource);

        if (aiPrediction != null && aiPrediction.getRecommendedAction() != null
                && !aiPrediction.getRecommendedAction().isBlank()
                && !"NO_ACTION".equalsIgnoreCase(aiPrediction.getRecommendedAction())) {
            actionService.createAction(
                    incident,
                    ActionType.INVESTIGATION,
                    "AI recommended action: " + aiPrediction.getRecommendedAction()
                            + (aiPrediction.getDecisionExplanation() != null
                                    ? " | " + aiPrediction.getDecisionExplanation() : ""),
                    "AI-SYSTEM",
                    namespace,
                    RemediationTargetKind.DEPLOYMENT,
                    app,
                    AutomationMode.MANUAL
            );
        }

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

    private AIPredictResponse tryPredict(String namespace, String app) {
        try {
            AIPredictRequest req = new AIPredictRequest();
            req.setNamespace(namespace);
            req.setApp(app);
            req.setWindowMinutes(10);
            return aiOpsClientService.predict(req);
        } catch (Exception ex) {
            log.warn("AI predict failed for webhook (ns={} app={}): {}", namespace, app, ex.getMessage());
            return null;
        }
    }

    private Long tryAiPipeline(String namespace, String app) {
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
            log.warn("AI pipeline failed for webhook (ns={} app={}): {}", namespace, app, ex.getMessage());
        }
        return null;
    }

    private Incident createIncidentFromWebhook(String message, IncidentSeverity severity,
                                               AlertSource alertSource, String namespace, String app,
                                               AIPredictResponse prediction) {
        Incident incident = new Incident();
        incident.setTitle("Auto Incident from Alertmanager");
        incident.setSeverity(severity);
        incident.setStatus(IncidentStatus.NEW);
        incident.setNamespace(namespace);
        incident.setApp(app);

        // If AI ran but said no-incident, mark as AI source and attach its
        // analysis so the UI still shows ML type + LLM explanation. If AI was
        // unreachable, keep the legacy mapping.
        if (prediction != null) {
            incident.setSource(IncidentSource.AI);
            incident.setIncidentType(prediction.getIncidentType());
            String explanation = prediction.getDecisionExplanation();
            if (explanation == null || explanation.isBlank()) explanation = prediction.getSummary();
            incident.setExplanation(explanation);
            incident.setDescription(buildEnrichedDescription(message, prediction));
        } else {
            incident.setSource(mapAlertToIncidentSource(alertSource));
            incident.setDescription(message);
        }

        incident = incidentRepo.save(incident);
        historyService.record(incident, IncidentStatus.NEW,
                prediction != null ? "Incident created from alert (AI enriched)" : "Incident created from alert (AI unavailable)");
        return incident;
    }

    private String buildEnrichedDescription(String message, AIPredictResponse p) {
        String enriched = String.format(
                "%s | type=%s | action=%s | executionMode=%s | confidence=%.2f | explanation=%s",
                safe(message),
                safe(p.getIncidentType()),
                safe(p.getRecommendedAction()),
                safe(p.getExecutionMode()),
                p.getConfidence() != null ? p.getConfidence() : 0.0,
                safe(p.getDecisionExplanation())
        );
        if (enriched.length() > 1000) enriched = enriched.substring(0, 997) + "...";
        return enriched;
    }

    private String safe(String value) {
        return value == null ? "unknown" : value;
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
