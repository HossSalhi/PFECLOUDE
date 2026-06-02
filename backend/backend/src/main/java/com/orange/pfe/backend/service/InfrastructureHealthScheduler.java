package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.InfraHealthProperties;
import com.orange.pfe.backend.dto.AIPredictRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.InfrastructureHealthService.ComponentStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * Periodically checks the health of the entire infrastructure:
 * - Kubernetes nodes (Master, Worker1, Worker2)
 * - VM Monitoring (Prometheus, Loki, Grafana)
 * - VM IaCamunda (AI service, Camunda, Ollama)
 *
 * When a component is DOWN an incident is created and enriched by the AI
 * service (ML classification + LLM explanation + recommended action). When it
 * comes back UP the incident is resolved automatically.
 */
@Service
public class InfrastructureHealthScheduler {

    private static final Logger log = LoggerFactory.getLogger(InfrastructureHealthScheduler.class);

    private static final List<IncidentStatus> ACTIVE_STATUSES = List.of(
            IncidentStatus.NEW, IncidentStatus.OPEN,
            IncidentStatus.IN_PROGRESS, IncidentStatus.WAITING_FOR_APPROVAL
    );

    private final InfraHealthProperties props;
    private final InfrastructureHealthService healthService;
    private final IncidentRepository incidentRepository;
    private final IncidentHistoryService historyService;
    private final AIOpsClientService aiOpsClientService;
    private final ActionService actionService;
    private final LeaderElectionService leaderElectionService;

    public InfrastructureHealthScheduler(
            InfraHealthProperties props,
            InfrastructureHealthService healthService,
            IncidentRepository incidentRepository,
            IncidentHistoryService historyService,
            AIOpsClientService aiOpsClientService,
            ActionService actionService,
            LeaderElectionService leaderElectionService
    ) {
        this.props = props;
        this.healthService = healthService;
        this.incidentRepository = incidentRepository;
        this.historyService = historyService;
        this.aiOpsClientService = aiOpsClientService;
        this.actionService = actionService;
        this.leaderElectionService = leaderElectionService;
    }

    @Scheduled(fixedDelayString = "${infra.health.interval-seconds:300}000")
    @Transactional
    public void checkInfrastructure() {
        if (!props.isEnabled()) return;
        // HA: only the leader replica runs infra health checks (avoids duplicate incidents).
        if (!leaderElectionService.isLeader()) return;

        log.info("Infrastructure health check started");
        List<ComponentStatus> statuses = healthService.checkAll();

        long totalDown = statuses.stream().filter(s -> !s.up()).count();
        int newIncidents = 0, resolved = 0;
        for (ComponentStatus status : statuses) {
            if (!status.up()) {
                if (openIncidentIfNeeded(status)) newIncidents++;
            } else {
                if (resolveIncidentIfExists(status)) resolved++;
            }
        }

        if (totalDown > 0) {
            log.warn("Infrastructure check: {}/{} component(s) DOWN ({} new incident(s), {} auto-resolved)",
                    totalDown, statuses.size(), newIncidents, resolved);
        } else {
            log.info("Infrastructure check: all {} component(s) UP", statuses.size());
        }
    }

    // -------------------------------------------------------------------------

    private boolean openIncidentIfNeeded(ComponentStatus status) {
        boolean alreadyOpen = incidentRepository.existsByStatusInAndSourceAndNamespaceAndApp(
                ACTIVE_STATUSES, IncidentSource.AI, status.namespace(), status.app()
        );
        if (alreadyOpen) {
            log.debug("Incident already open for {}/{}, skipping", status.namespace(), status.app());
            return false;
        }

        Incident incident = new Incident();
        incident.setSource(IncidentSource.AI);
        incident.setStatus(IncidentStatus.OPEN);
        incident.setNamespace(status.namespace());
        incident.setApp(status.app());
        incident.setIncidentType("VM_DOWN");
        incident.setSeverity(severityFor(status));
        incident.setTitle("Infrastructure DOWN: " + status.namespace() + "/" + status.app());
        incident.setDescription(buildDescription(status));
        incident = incidentRepository.save(incident);

        historyService.record(incident, IncidentStatus.OPEN,
                "Infrastructure health check detected component DOWN: " + status.detail());

        // Enrich with AI: ask the AI service for an ML classification + LLM
        // explanation + recommended action. If the AI service is reachable, the
        // incident gets a real human-readable explanation and a remediation
        // action so the operator can hit "Demarrer remediation" to fire Camunda.
        enrichIncidentWithAi(incident, status);

        log.warn("Incident created - infrastructure component DOWN: {}/{} - {}",
                status.namespace(), status.app(), status.detail());
        return true;
    }

    private void enrichIncidentWithAi(Incident incident, ComponentStatus status) {
        try {
            AIPredictRequest req = new AIPredictRequest();
            req.setNamespace(status.namespace());
            req.setApp(status.app());
            req.setWindowMinutes(10);

            AIPredictResponse prediction = aiOpsClientService.predict(req);
            if (prediction == null) return;

            String aiExplanation = prediction.getDecisionExplanation();
            if (aiExplanation == null || aiExplanation.isBlank()) {
                aiExplanation = prediction.getSummary();
            }
            if (aiExplanation != null && !aiExplanation.isBlank()) {
                incident.setExplanation(aiExplanation);
            }

            // Override the type only if AI returned something more specific.
            if (prediction.getIncidentType() != null && !prediction.getIncidentType().isBlank()
                    && !"unknown".equalsIgnoreCase(prediction.getIncidentType())) {
                incident.setIncidentType(prediction.getIncidentType());
            }

            // Rich description with the AI metadata, same format the AIOps
            // pipeline uses so the UI shows action / executionMode / confidence.
            String enrichedDesc = String.format(
                    "%s | type=%s | action=%s | executionMode=%s | confidence=%.2f | explanation=%s",
                    safe(buildDescription(status)),
                    safe(prediction.getIncidentType()),
                    safe(prediction.getRecommendedAction()),
                    safe(prediction.getExecutionMode()),
                    prediction.getConfidence() != null ? prediction.getConfidence() : 0.0,
                    safe(aiExplanation)
            );
            if (enrichedDesc.length() > 1000) {
                enrichedDesc = enrichedDesc.substring(0, 997) + "...";
            }
            incident.setDescription(enrichedDesc);

            incidentRepository.save(incident);

            String recommendedAction = prediction.getRecommendedAction();
            if (recommendedAction != null && !recommendedAction.isBlank()) {
                actionService.createAction(
                        incident,
                        ActionType.INVESTIGATION,
                        "AI recommended action: " + recommendedAction
                                + (aiExplanation != null ? " | " + aiExplanation : ""),
                        "AI-SYSTEM",
                        status.namespace(),
                        RemediationTargetKind.DEPLOYMENT,
                        status.app(),
                        AutomationMode.MANUAL
                );
            }

            historyService.record(incident, incident.getStatus(),
                    "AI enrichment: type=" + safe(prediction.getIncidentType())
                            + " action=" + safe(prediction.getRecommendedAction())
                            + " confidence=" + (prediction.getConfidence() != null ? prediction.getConfidence() : 0.0));

            log.info("Incident {} enriched by AI: type={} action={}",
                    incident.getId(), prediction.getIncidentType(), prediction.getRecommendedAction());
        } catch (Exception ex) {
            log.warn("AI enrichment failed for incident {} ({}): {}",
                    incident.getId(), status.namespace() + "/" + status.app(), ex.getMessage());
        }
    }

    private String safe(String value) {
        return value == null ? "unknown" : value;
    }

    private boolean resolveIncidentIfExists(ComponentStatus status) {
        List<Incident> active = incidentRepository.findBySource(IncidentSource.AI).stream()
                .filter(i -> ACTIVE_STATUSES.contains(i.getStatus()))
                .filter(i -> status.namespace().equals(i.getNamespace()))
                .filter(i -> status.app().equals(i.getApp()))
                .toList();

        if (active.isEmpty()) return false;

        for (Incident incident : active) {
            incident.setStatus(IncidentStatus.RESOLVED);
            incidentRepository.save(incident);
            historyService.record(incident, IncidentStatus.RESOLVED,
                    "Infrastructure component is back UP: " + status.detail());
            log.info("Incident auto-resolved - {}/{} is back UP", status.namespace(), status.app());
        }
        return true;
    }

    private IncidentSeverity severityFor(ComponentStatus status) {
        // K8s nodes and critical infra = CRITICAL, monitoring tools = HIGH
        if ("k8s-nodes".equals(status.namespace())) return IncidentSeverity.CRITICAL;
        if ("camunda".equals(status.app()) || "ai-service".equals(status.app())) return IncidentSeverity.HIGH;
        return IncidentSeverity.HIGH;
    }

    private String buildDescription(ComponentStatus status) {
        return switch (status.namespace()) {
            case "k8s-nodes" ->
                    "Kubernetes node '" + status.app() + "' is NotReady. " +
                    "Workloads on this node may be unavailable. Detail: " + status.detail();
            case "infra" ->
                    "Infrastructure component '" + status.app() + "' is unreachable. " +
                    "This may impact the AIOps pipeline and cluster observability. Detail: " + status.detail();
            default ->
                    "Component " + status.namespace() + "/" + status.app() +
                    " is DOWN. Detail: " + status.detail();
        };
    }
}
