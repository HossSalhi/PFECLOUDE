package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.InfraHealthProperties;
import com.orange.pfe.backend.dto.AIPredictRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.InfrastructureHealthService.ComponentStatus;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
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
    private final WorkflowService workflowService;
    private final CamundaWorkflowService camundaWorkflowService;
    private final NotificationService notificationService;
    private final KubernetesSignalEnrichmentService signalEnrichmentService;
    // "camunda" = Camunda orchestre AUSSI la création des incidents infra/nœud (via le process
    // incident-detection → worker ai-classify, branche infra déterministe). "backend" = création directe.
    @org.springframework.beans.factory.annotation.Value("${helios.detection.pipeline:backend}")
    private String detectionPipeline;

    public InfrastructureHealthScheduler(
            InfraHealthProperties props,
            InfrastructureHealthService healthService,
            IncidentRepository incidentRepository,
            IncidentHistoryService historyService,
            AIOpsClientService aiOpsClientService,
            ActionService actionService,
            LeaderElectionService leaderElectionService,
            WorkflowService workflowService,
            CamundaWorkflowService camundaWorkflowService,
            NotificationService notificationService,
            KubernetesSignalEnrichmentService signalEnrichmentService
    ) {
        this.props = props;
        this.healthService = healthService;
        this.incidentRepository = incidentRepository;
        this.historyService = historyService;
        this.aiOpsClientService = aiOpsClientService;
        this.actionService = actionService;
        this.leaderElectionService = leaderElectionService;
        this.workflowService = workflowService;
        this.camundaWorkflowService = camundaWorkflowService;
        this.notificationService = notificationService;
        this.signalEnrichmentService = signalEnrichmentService;
    }

    @Scheduled(fixedDelayString = "${infra.health.interval-seconds:300}000")
    @Transactional
    public void checkInfrastructure() {
        if (!props.isEnabled()) return;
        // HA: only the leader replica runs infra health checks (avoids duplicate incidents).
        if (!leaderElectionService.isLeader()) return;
        // Camunda = orchestrateur CENTRAL : Camunda arrêté => AUCUNE détection (ni app, ni infra/nœud).
        if (!camundaWorkflowService.isReachable()) {
            log.warn("Infra health check: Camunda injoignable — détection infra/nœud SUSPENDUE (0 incident tant que Camunda est arrêté)");
            return;
        }
        // DÉTECTION NŒUD = CAMUNDA + MONITORING (pas l'IA) : un nœud tombé est TOUJOURS détecté tant que
        // Camunda est joignable. MAIS l'AUTO-HEAL (power-on de la VM) EXIGE DÉSORMAIS l'IA : c'est elle le
        // cerveau qui « décide la solution », Camunda ne fait qu'exécuter. Si l'IA est injoignable, le
        // nœud est détecté + escaladé, mais AUCUN power-on automatique (gate dans CamundaJobWorkerLifecycle,
        // triggerHostWatchdogRestart) — cohérent avec le repli app (IA down => ESCALADE, jamais de
        // remédiation sans IA). Camunda reste la garde de DÉTECTION ; l'IA est la garde d'EXÉCUTION.

        log.info("Infrastructure health check started");
        List<ComponentStatus> statuses = healthService.checkAll();

        long totalDown = statuses.stream().filter(s -> !s.up()).count();
        int newIncidents = 0, resolved = 0;
        boolean viaCamunda = "camunda".equalsIgnoreCase(detectionPipeline);
        for (ComponentStatus status : statuses) {
            if (!status.up()) {
                if (viaCamunda) {
                    // Camunda orchestre AUSSI la création des incidents infra/nœud : on lui délègue
                    // (sauf si un incident est déjà ouvert -> évite de démarrer un process par cycle).
                    boolean alreadyOpen = incidentRepository.existsByStatusInAndSourceAndNamespaceAndApp(
                            ACTIVE_STATUSES, IncidentSource.AI, status.namespace(), status.app());
                    if (!alreadyOpen && camundaWorkflowService.startDetectionWorkflow(
                            status.namespace(), status.app(), "infra", status.detail())) {
                        newIncidents++;
                    }
                } else if (openIncidentIfNeeded(status)) {
                    newIncidents++;
                }
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

    /**
     * Point d'entrée appelé PAR CAMUNDA (worker ai-classify, branche infra) pour créer un incident
     * infra/nœud. C'est ainsi que Camunda orchestre AUSSI les incidents infra. @Transactional car
     * appelé hors du cycle checkInfrastructure. Classification déterministe (un nœud/VM mort n'a
     * pas de métriques exploitables par l'IA) — la logique nœud (handleNodeOutageRecovery) est INCHANGÉE.
     */
    @Transactional
    public boolean openInfraIncidentFromCamunda(String namespace, String app, String detail) {
        return openIncidentIfNeeded(new ComponentStatus(namespace, app, false, detail));
    }

    private boolean openIncidentIfNeeded(ComponentStatus status) {
        boolean alreadyOpen = incidentRepository.existsByStatusInAndSourceAndNamespaceAndApp(
                ACTIVE_STATUSES, IncidentSource.AI, status.namespace(), status.app()
        );
        if (alreadyOpen) {
            log.debug("Incident already open for {}/{}, skipping", status.namespace(), status.app());
            return false;
        }
        // Anti-doublon (flapping) : si un incident pour CE composant a deja ete cree il y a moins
        // de 5 min (meme deja resolu), on n'en recree pas -> evite la rafale quand une VM infra
        // clignote (down/up/down) sur plusieurs cycles de detection.
        if (incidentRepository.existsBySourceAndNamespaceAndAppAndCreatedAtAfter(
                IncidentSource.AI, status.namespace(), status.app(),
                java.time.LocalDateTime.now().minusMinutes(5))) {
            log.debug("Incident recent (<5min) pour {}/{} — skip (anti-doublon)", status.namespace(), status.app());
            return false;
        }

        Incident incident = new Incident();
        incident.setSource(IncidentSource.AI);
        incident.setStatus(IncidentStatus.OPEN);
        incident.setNamespace(status.namespace());
        incident.setApp(status.app());
        incident.setIncidentType(incidentTypeFor(status));
        incident.setSeverity(severityFor(status));
        incident.setTitle(titleFor(status));
        incident.setDescription(buildDescription(status));
        incident = incidentRepository.save(incident);

        historyService.record(incident, IncidentStatus.OPEN,
                "Infrastructure health check detected component DOWN: " + status.detail());

        // AI enrichment for all infra incidents
        enrichIncidentWithAi(incident, status);

        // Worker node down → recovery is delegated to the HOST watchdog (vmrun power-on); HELIOS just
        // tracks the outage and resolves it when the node returns to Ready (no failing Camunda action).
        handleNodeOutageRecovery(incident, status);

        // Disk/Memory pressure → escalation email (can't auto-delete files)
        if ("node-pressure".equals(status.namespace())) {
            triggerPressureEscalation(incident, status);
        }

        // DNS failure → escalation email + attempt CoreDNS rollout restart
        if ("cluster".equals(status.namespace()) && "coredns".equals(status.app())) {
            triggerDnsEscalation(incident, status);
        }

        // Notify (email + SSE) for any newly created infra incident
        try {
            notificationService.notifyNewIncident(incident);
        } catch (Exception ex) {
            log.warn("Failed to send new-incident notification for {}/{}: {}", status.namespace(), status.app(), ex.getMessage());
        }

        log.warn("Incident created - infrastructure component DOWN: {}/{} - {}",
                status.namespace(), status.app(), status.detail());
        return true;
    }

    private void triggerPressureEscalation(Incident incident, ComponentStatus status) {
        String reason = status.detail()
                + " — Action requise : libérer de l'espace disque sur le nœud "
                + status.app().replaceFirst("-DiskPressure$", "").replaceFirst("-MemoryPressure$", "");
        try {
            notificationService.notifyRemediationFailed(incident, reason);
        } catch (Exception ex) {
            log.warn("Pressure escalation notification failed: {}", ex.getMessage());
        }
        log.warn("Node pressure escalated to super admin: {}", reason);
    }

    private void triggerDnsEscalation(Incident incident, ComponentStatus status) {
        String reason = status.detail()
                + " — Vérifier CoreDNS (kubectl get pods -n kube-system) "
                + "et restaurer la configuration DNS (kubectl apply configmap coredns)";
        try {
            notificationService.notifyRemediationFailed(incident, reason);
        } catch (Exception ex) {
            log.warn("DNS escalation notification failed: {}", ex.getMessage());
        }
        log.warn("DNS failure escalated to super admin: {}", reason);
    }

    private String incidentTypeFor(ComponentStatus status) {
        return switch (status.namespace()) {
            case "node-pressure" -> status.app().contains("Disk") ? "NODE_DISK_PRESSURE" : "NODE_MEMORY_PRESSURE";
            case "cluster"       -> "DNS_FAILURE";
            default              -> "VM_DOWN";
        };
    }

    private String titleFor(ComponentStatus status) {
        return switch (status.namespace()) {
            case "node-pressure" -> "Node Pressure: " + status.app() + " detected";
            case "cluster"       -> "DNS Failure: in-cluster resolution broken";
            default              -> "Infrastructure DOWN: " + status.namespace() + "/" + status.app();
        };
    }

    /**
     * Pour un nœud WORKER tombé : crée une action RESTART_VM (AUTO) et déclenche le
     * workflow Camunda qui appelle l'agent hôte pour rallumer la VM. C'est Camunda qui
     * orchestre la récupération du nœud. Master exclu (jamais rallumé automatiquement).
     */
    /**
     * Worker node NotReady. A node power-on CANNOT be done from inside the cluster (the VM is dead) and
     * k8s-nodes is intentionally NOT in the remediation allowlist — so we do NOT launch a Camunda
     * RESTART_VM that would only fail "not allowlisted" and park the incident RESOLUTION_FAILED. The real
     * recovery is the HOST watchdog (vmrun power-on when NotReady >= 60s). HELIOS records the outage as a
     * clean escalation IN_PROGRESS and {@link #resolveIncidentIfExists} closes it when the node returns to
     * Ready (the source of truth) — giving a coherent trace: OPEN -> IN_PROGRESS (watchdog) -> RESOLVED.
     */
    private void handleNodeOutageRecovery(Incident incident, ComponentStatus status) {
        String node = status.app();
        if (!"k8s-nodes".equals(status.namespace()) || node == null
                || !node.toLowerCase().startsWith("k8s-worker")) {
            return; // seulement les noeuds worker du cluster
        }
        try {
            // A dead node has NO kubectl fix (the VM is unreachable) — the recommended action is to
            // ESCALATE the outage; the HOST watchdog (vmrun) powers the VM back on. We still orchestrate
            // this via Camunda for a coherent, traceable workflow: an ESCALATION action is a successful
            // no-op in the executor (no allowlist check, no RESTART_VM failure), then the verify step polls
            // the NODE's Ready status (node-aware) and resolves the incident when it rejoins.
            RemediationPlan plan = new RemediationPlan(
                    ActionType.ESCALATION, AutomationMode.AUTO,
                    false, true, false, false, false,
                    "k8s-nodes", RemediationTargetKind.UNKNOWN, node,
                    "Panne noeud " + node + " — escalade + power-on delegue au watchdog hote (vmrun)"
            );
            var action = actionService.createAction(
                    incident, ActionType.ESCALATION,
                    "Escalade de la panne noeud " + node + " — power-on de la VM via le watchdog hote",
                    "AI-SYSTEM", "k8s-nodes", RemediationTargetKind.UNKNOWN, node,
                    AutomationMode.AUTO
            );
            WorkflowInstance workflow = workflowService.startWorkflow(
                    incident, "Node-Remediation-Workflow", "k8s-nodes", node);
            AIPredictResponse pred = new AIPredictResponse();
            pred.setIncidentType("NODE_NOT_READY");
            pred.setRecommendedAction("ESCALATE_NODE_OUTAGE_AND_REBALANCE");
            camundaWorkflowService.startWorkflow(workflow, action, plan, pred);
            incident.setStatus(IncidentStatus.IN_PROGRESS);
            incidentRepository.save(incident);
            historyService.record(incident, IncidentStatus.IN_PROGRESS,
                    "Escalade Camunda — récupération du nœud " + node + " déléguée au watchdog hôte (vmrun); "
                    + "surveillance du retour du nœud à l'état Ready");
            log.warn("Node {} NotReady — Camunda escalation workflow started (recovery via host watchdog)", node);
        } catch (Exception ex) {
            log.error("Node outage handling failed for {}: {}", node, ex.getMessage());
        }
    }

    private void enrichIncidentWithAi(Incident incident, ComponentStatus status) {
        try {
            AIPredictRequest req = new AIPredictRequest();
            req.setNamespace(status.namespace());
            req.setApp(status.app());
            req.setWindowMinutes(10);

            // NŒUD/VM MORT : le nœud lui-même ne produit plus de métriques, mais l'IA peut ANALYSER le
            // CONTEXTE de la panne (nœud injoignable, durée, rayon d'impact, charge des nœuds survivants,
            // pression avant bascule + cause racine via Prometheus .160). On lui fournit ce vecteur : le
            // classifieur route alors sur le chemin NODE (NODE_NOT_READY) et l'explication/action/sévérité
            // deviennent réelles au lieu d'un NO_INCIDENT sur un espace de noms « k8s-nodes » vide.
            if ("k8s-nodes".equals(status.namespace())) {
                java.util.Map<String, Double> nodeFeatures =
                        signalEnrichmentService.collectNodeOutageFeatures(status.app());
                nodeFeatures.put("node_flap_count", (double) recentNodeFlapCount(status.namespace(), status.app()));
                req.setFeatureOverrides(nodeFeatures);
                req.getRemediationContext().put("scenario", "node_outage");
                req.getRemediationContext().put("node", status.app());
            }

            AIPredictResponse prediction = aiOpsClientService.predict(req);
            if (prediction == null) return;

            String aiExplanation = prediction.getDecisionExplanation();
            if (aiExplanation == null || aiExplanation.isBlank()) {
                aiExplanation = prediction.getSummary();
            }
            if (aiExplanation != null && !aiExplanation.isBlank()) {
                incident.setExplanation(aiExplanation);
            }

            // CLASSIFICATION PAR L'IA pour les nœuds (master + workers), dans le cycle HELIOS : le TYPE
            // et la SÉVÉRITÉ de l'incident nœud proviennent désormais de l'IA (plus de type figé en dur).
            // Le VM_DOWN posé par incidentTypeFor ne sert QUE de repli si l'IA est injoignable ou muette
            // (résilience : on ne perd jamais l'incident d'un nœud réellement tombé — la DÉTECTION reste
            // sûre/déterministe, seule la CLASSIFICATION est confiée à l'IA). Les autres composants infra
            // (VM monitoring/IA, DNS, pressions nœud) gardent leur type déterministe (non concernés ici).
            String aiType = prediction.getIncidentType();
            boolean usableAiClass = prediction.isIncident()
                    && aiType != null && !aiType.isBlank() && !"NO_INCIDENT".equalsIgnoreCase(aiType);
            if ("k8s-nodes".equals(status.namespace()) && usableAiClass) {
                incident.setIncidentType(aiType);
                IncidentSeverity aiSeverity = mapSeverity(prediction.getSeverity());
                if (aiSeverity != null) {
                    incident.setSeverity(aiSeverity);
                }
                incident.setTitle("Panne nœud (IA: " + aiType + ") — " + status.app());
                log.info("Incident {} CLASSÉ PAR L'IA (nœud {}): type={} severity={} action={} confidence={}",
                        incident.getId(), status.app(), aiType, prediction.getSeverity(),
                        prediction.getRecommendedAction(), prediction.getConfidence());
            }

            // Rich description with the AI metadata, same format the AIOps pipeline uses so the UI shows
            // action / executionMode / confidence. Severity shown = the incident's severity field, which
            // for a NODE incident now reflects the AI classification set just above (and for the other
            // infra components stays the deterministic severityFor) — single source of truth either way.
            String authoritativeSeverity = incident.getSeverity() != null
                    ? incident.getSeverity().name()
                    : safe(prediction.getSeverity());
            String enrichedDesc = String.format(
                    "%s | Type: %s | Severity: %s (confidence %.0f%%) | Recommended action: %s | Mode: %s",
                    safe(buildDescription(status)),
                    safe(prediction.getIncidentType()),
                    authoritativeSeverity,
                    (prediction.getConfidence() != null ? prediction.getConfidence() : 0.0) * 100.0,
                    safe(prediction.getRecommendedAction()),
                    safe(prediction.getExecutionMode())
            );
            if (enrichedDesc.length() > 1000) {
                enrichedDesc = enrichedDesc.substring(0, 997) + "...";
            }
            incident.setDescription(enrichedDesc);

            incidentRepository.save(incident);

            // Pas d'action INVESTIGATION ici : la remédiation du noeud est l'action
            // RESTART_VM (AUTO) creee + executee via Camunda dans triggerNodePowerOnViaCamunda.

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

    /** Mappe la sévérité renvoyée par l'IA (LOW/MEDIUM/HIGH/CRITICAL) vers l'enum ; null si inexploitable. */
    private IncidentSeverity mapSeverity(String s) {
        if (s == null || s.isBlank()) return null;
        try {
            return IncidentSeverity.valueOf(s.trim().toUpperCase(java.util.Locale.ROOT));
        } catch (IllegalArgumentException ex) {
            return null;
        }
    }

    /**
     * Nombre d'incidents ouverts pour CE nœud sur les 24 dernières heures = signal de "flapping"
     * (nœud instable qui retombe). Fourni à l'IA (feature node_flap_count) pour qu'elle module sa
     * sévérité/analyse : un nœud qui claque plusieurs fois évoque un défaut matériel/hôte persistant
     * plutôt qu'un reboot transitoire. Dégradation propre à 0 en cas d'erreur.
     */
    private long recentNodeFlapCount(String namespace, String app) {
        try {
            return incidentRepository.findBySource(IncidentSource.AI).stream()
                    .filter(i -> namespace.equals(i.getNamespace()) && app.equals(i.getApp()))
                    .filter(i -> i.getCreatedAt() != null
                            && i.getCreatedAt().isAfter(java.time.LocalDateTime.now().minusHours(24)))
                    .count();
        } catch (Exception e) {
            log.debug("recentNodeFlapCount indisponible pour {}/{}: {}", namespace, app, e.getMessage());
            return 0L;
        }
    }

    private boolean resolveIncidentIfExists(ComponentStatus status) {
        // The component (node / infra service) is back UP — that IS the source of truth, so we clear its
        // incident even if the automated remediation had been parked RESOLUTION_FAILED (e.g. the Camunda
        // RESTART_VM workflow timed out while the HOST watchdog actually powered the VM back on). Without
        // this, a recovered worker would keep showing "Infrastructure DOWN … RESOLUTION_FAILED" forever.
        List<Incident> active = incidentRepository.findBySource(IncidentSource.AI).stream()
                .filter(i -> i.getStatus() != IncidentStatus.RESOLVED
                          && i.getStatus() != IncidentStatus.CLOSED
                          && i.getStatus() != IncidentStatus.ARCHIVED)
                .filter(i -> status.namespace().equals(i.getNamespace()))
                .filter(i -> status.app().equals(i.getApp()))
                .toList();

        if (active.isEmpty()) return false;

        for (Incident incident : active) {
            incident.setStatus(IncidentStatus.RESOLVED);
            incidentRepository.save(incident);
            historyService.record(incident, IncidentStatus.RESOLVED,
                    "Infrastructure component is back UP: " + status.detail());
            try {
                notificationService.notifyIncidentResolved(incident);
            } catch (Exception ex) {
                log.warn("Failed to send resolved notification for {}/{}: {}", status.namespace(), status.app(), ex.getMessage());
            }
            log.info("Incident auto-resolved - {}/{} is back UP", status.namespace(), status.app());
        }
        return true;
    }

    private IncidentSeverity severityFor(ComponentStatus status) {
        if ("k8s-nodes".equals(status.namespace())) return IncidentSeverity.CRITICAL;
        if ("node-pressure".equals(status.namespace())) return IncidentSeverity.HIGH;
        if ("cluster".equals(status.namespace())) return IncidentSeverity.HIGH;
        return IncidentSeverity.HIGH;
    }

    private String buildDescription(ComponentStatus status) {
        return switch (status.namespace()) {
            case "k8s-nodes" ->
                    "Kubernetes node '" + status.app() + "' is NotReady. " +
                    "Workloads on this node may be unavailable. Detail: " + status.detail();
            case "node-pressure" ->
                    "Node resource pressure detected: " + status.detail() +
                    ". Kubelet may evict pods. Free disk/memory on the affected node.";
            case "cluster" ->
                    "In-cluster DNS resolution failure detected. All service-to-service " +
                    "communication using hostnames is impacted. Detail: " + status.detail();
            case "infra" ->
                    "Infrastructure component '" + status.app() + "' is unreachable. " +
                    "This may impact the AIOps pipeline and cluster observability. Detail: " + status.detail();
            default ->
                    "Component " + status.namespace() + "/" + status.app() +
                    " is DOWN. Detail: " + status.detail();
        };
    }
}
