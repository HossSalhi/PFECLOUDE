package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.InfraHealthProperties;
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
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy;
import com.orange.pfe.backend.service.vmpower.VmPowerWatch;
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

    /**
     * VMDOWN-POLICY-20261105 — composants hébergés par .160 (DETECT_ONLY) : incident + historique + notification
     * INTERFACE seulement ; jamais de workflow Camunda, d'action ni d'e-mail.
     */
    private static final java.util.Set<String> DETECTION_ONLY_APPS = java.util.Set.of("prometheus", "grafana", "loki");

    private static boolean isDetectionOnly(ComponentStatus status) {
        return "infra".equals(status.namespace()) && DETECTION_ONLY_APPS.contains(status.app());
    }

    private final InfraHealthProperties props;
    private final InfrastructureHealthService healthService;
    private final IncidentRepository incidentRepository;
    private final IncidentHistoryService historyService;
    private final ActionService actionService;
    private final LeaderElectionService leaderElectionService;
    private final WorkflowService workflowService;
    private final CamundaWorkflowService camundaWorkflowService;
    private final NotificationService notificationService;
    // "camunda" = Camunda orchestre AUSSI la création des incidents infra/nœud (via le process
    // incident-detection → worker ai-classify, branche infra déterministe). "backend" = création directe.
    @org.springframework.beans.factory.annotation.Value("${helios.detection.pipeline:backend}")
    private String detectionPipeline;

    public InfrastructureHealthScheduler(
            InfraHealthProperties props,
            InfrastructureHealthService healthService,
            IncidentRepository incidentRepository,
            IncidentHistoryService historyService,
            ActionService actionService,
            LeaderElectionService leaderElectionService,
            WorkflowService workflowService,
            CamundaWorkflowService camundaWorkflowService,
            NotificationService notificationService
    ) {
        this.props = props;
        this.healthService = healthService;
        this.incidentRepository = incidentRepository;
        this.historyService = historyService;
        this.actionService = actionService;
        this.leaderElectionService = leaderElectionService;
        this.workflowService = workflowService;
        this.camundaWorkflowService = camundaWorkflowService;
        this.notificationService = notificationService;
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
                if (isDetectionOnly(status)) {
                    // .160 : détection et historique uniquement (aucun workflow Camunda, aucun e-mail).
                    if (openIncidentIfNeeded(status)) {
                        newIncidents++;
                    }
                } else if (viaCamunda) {
                    // Camunda orchestre AUSSI la création des incidents infra/nœud : on lui délègue
                    // (sauf si un incident est déjà ouvert -> évite de démarrer un process par cycle).
                    boolean alreadyOpen = hasActiveInfrastructureIncident(status);
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
        boolean alreadyOpen = hasActiveInfrastructureIncident(status);
        if (alreadyOpen) {
            log.debug("Incident already open for {}/{}, skipping", status.namespace(), status.app());
            return false;
        }
        // Anti-doublon (flapping) : si un incident pour CE composant a deja ete cree il y a moins
        // de 5 min (meme deja resolu), on n'en recree pas -> evite la rafale quand une VM infra
        // clignote (down/up/down) sur plusieurs cycles de detection.
        if (hasRecentInfrastructureIncident(status)) {
            log.debug("Incident recent (<5min) pour {}/{} — skip (anti-doublon)", status.namespace(), status.app());
            return false;
        }

        Incident incident = new Incident();
        incident.setSource(IncidentSource.OTHER);
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
        enrichIncidentDeterministically(incident, status);

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

        // Notify (email + SSE) for any newly created infra incident — .160 : interface seulement.
        try {
            if (isDetectionOnly(status)) {
                notificationService.notifyNewIncidentUiOnly(incident);
            } else {
                notificationService.notifyNewIncident(incident);
            }
        } catch (Exception ex) {
            log.warn("Failed to send new-incident notification for {}/{}: {}", status.namespace(), status.app(), ex.getMessage());
        }

        log.warn("Incident created - infrastructure component DOWN: {}/{} - {}",
                status.namespace(), status.app(), status.detail());
        return true;
    }

    private boolean hasActiveInfrastructureIncident(ComponentStatus status) {
        return hasActiveInfrastructureIncident(status, IncidentSource.OTHER)
                || hasActiveInfrastructureIncident(status, IncidentSource.AI);
    }

    private boolean hasActiveInfrastructureIncident(ComponentStatus status, IncidentSource source) {
        return incidentRepository.existsByStatusInAndSourceAndNamespaceAndApp(
                ACTIVE_STATUSES, source, status.namespace(), status.app()
        );
    }

    private boolean hasRecentInfrastructureIncident(ComponentStatus status) {
        java.time.LocalDateTime cutoff = java.time.LocalDateTime.now().minusMinutes(5);

        return hasRecentInfrastructureIncident(status, IncidentSource.OTHER, cutoff)
                || hasRecentInfrastructureIncident(status, IncidentSource.AI, cutoff);
    }

    private boolean hasRecentInfrastructureIncident(
            ComponentStatus status,
            IncidentSource source,
            java.time.LocalDateTime cutoff
    ) {
        return incidentRepository.existsBySourceAndNamespaceAndAppAndCreatedAtAfter(
                source, status.namespace(), status.app(), cutoff
        );
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

    /**
     * Explication INFRA déterministe pour les composants NON-nœud (VM monitoring/IA/Camunda -> VM_DOWN,
     * pression nœud, DNS). Une VM/un service mort n'a pas de métriques niveau-pod : on ne propose donc
     * AUCUNE action Kubernetes (ce serait absurde), on décrit la vraie récupération (intervention infra).
     */
    private String deterministicInfraExplanation(String type, ComponentStatus status) {
        String comp = status.namespace() + "/" + status.app();

        return switch (type == null ? "" : type) {
            case "NODE_NOT_READY" ->
                    "Kubernetes node " + status.app() + " is NotReady (" + comp
                    + "). Check the host watchdog recovery path and confirm node Ready "
                    + "and cluster health before resolving.";
            case "VM_DOWN", "VM_POWERED_OFF" ->
                    "Infrastructure component is unreachable (" + comp
                    + "). Recovery must be confirmed by the infrastructure health check.";
            case "DNS_FAILURE" ->
                    "In-cluster DNS resolution failure (" + comp
                    + "). Verify CoreDNS and confirm DNS recovery.";
            case "NODE_DISK_PRESSURE" ->
                    "Disk pressure detected (" + comp
                    + "). Check available filesystem space and inode capacity.";
            case "NODE_MEMORY_PRESSURE" ->
                    "Memory pressure detected (" + comp
                    + "). Check node memory usage and reclaimable resources.";
            default ->
                    "Infrastructure incident (" + comp
                    + "). Confirm recovery using the relevant technical health signal.";
        };
    }

    private String incidentTypeFor(ComponentStatus status) {
        return switch (status.namespace()) {
            case "k8s-nodes" -> "NODE_NOT_READY";
            case "node-pressure" ->
                    status.app().contains("Disk")
                            ? "NODE_DISK_PRESSURE"
                            : "NODE_MEMORY_PRESSURE";
            case "cluster" -> "DNS_FAILURE";
            default -> "VM_DOWN";
        };
    }
    private String titleFor(ComponentStatus status) {
        return switch (status.namespace()) {
            case "k8s-nodes" ->
                    "Kubernetes node NotReady: " + status.app();
            case "node-pressure" ->
                    "Node Pressure: " + status.app() + " detected";
            case "cluster" ->
                    "DNS Failure: in-cluster resolution broken";
            default ->
                    "Infrastructure DOWN: " + status.namespace() + "/" + status.app();
        };
    }
    /**
     * VMDOWN-POLICY-20261105 — le scheduler ne déclenche PLUS aucun redémarrage. Il détecte (NODE_NOT_READY via
     * l'API Kubernetes), crée l'incident et notifie, puis laisse la DÉCISION à {@link VmPowerWatch} :
     * un power-on n'est demandé que si la VM est prouvée éteinte (vmrun list, 2 observations espacées de 30 s),
     * les sondes réseau en échec, hors maintenance/cooldown/épisode, et si la politique l'autorise.
     * Cibles : k8s-worker1/k8s-worker2 et k8s-master (.153, depuis la consigne du 2026-11-17) = AUTO ; toute autre VM ou
     * tout mode non AUTO = aucune autorisation (détection seule).
     */
    private void handleNodeOutageRecovery(Incident incident, ComponentStatus status) {
        if (!"k8s-nodes".equals(status.namespace())) {
            return;
        }
        var entry = VmPowerPolicy.find(status.app());
        if (entry.isEmpty()) {
            return;  // pas une VM de la politique : rien à décider ici
        }
        try {
            if (entry.get().mode() == VmPowerPolicy.Mode.AUTO) {
                historyService.record(incident, incident.getStatus(),
                        "Politique VM_DOWN : le rallumage éventuel de " + status.app() + " est décidé par la "
                        + "surveillance de puissance (VM éteinte confirmée par vmrun, sondes en échec, hors "
                        + "maintenance/cooldown) ; aucun démarrage tant que l'arrêt n'est pas prouvé.");
            } else {
                historyService.record(incident, incident.getStatus(),
                        "Politique VM_DOWN : " + status.app() + " n'a AUCUNE autorisation de "
                        + "redémarrage automatique — détection et notification uniquement ; "
                        + "redémarrage laissé à l'administrateur.");
            }
        } catch (Exception ex) {
            log.error("Node outage history failed for {}: {}", status.app(), ex.getMessage());
        }
    }

    private void enrichIncidentDeterministically(Incident incident, ComponentStatus status) {
        String type = incident.getIncidentType();
        incident.setExplanation(deterministicInfraExplanation(type, status));

        String severity = incident.getSeverity() == null
                ? "UNKNOWN"
                : incident.getSeverity().name();

        String description = String.format(
                "%s | Type: %s | Severity: %s | Decision: deterministic infrastructure policy",
                safe(buildDescription(status)),
                safe(type),
                severity
        );

        if (description.length() > 1000) {
            description = description.substring(0, 997) + "...";
        }

        incident.setDescription(description);
        incidentRepository.save(incident);

        historyService.record(
                incident,
                incident.getStatus(),
                "Deterministic infrastructure classification: type=" + safe(type)
        );

        log.info(
                "Incident {} enriched deterministically: type={} target={}/{}",
                incident.getId(), type, status.namespace(), status.app()
        );
    }
    private String safe(String value) {
        return value == null ? "unknown" : value;
    }

    private boolean resolveIncidentIfExists(ComponentStatus status) {

        // VM_DOWN : le retour du noeud à UP ne suffit pas pour clôturer l'incident.
        // La résolution doit être confirmée par le worker Camunda verify-remediation.
        if ("k8s-nodes".equalsIgnoreCase(status.namespace())) {
            log.info(
                    "Node {} is UP; incident resolution remains delegated to Camunda verification (no scheduler auto-resolution).",
                    status.app()
            );
            return false;
        }

        // The component (node / infra service) is back UP — that IS the source of truth, so we clear its
        // incident even if the automated remediation had been parked RESOLUTION_FAILED (e.g. the Camunda
        // RESTART_VM workflow timed out while the HOST watchdog actually powered the VM back on). Without
        // this, a recovered worker would keep showing "Infrastructure DOWN … RESOLUTION_FAILED" forever.
        List<Incident> active = java.util.stream.Stream.concat(
                    incidentRepository.findBySource(IncidentSource.AI).stream(),
                    incidentRepository.findBySource(IncidentSource.OTHER).stream())
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
                if (isDetectionOnly(status)) {
                    notificationService.notifyIncidentResolvedUiOnly(incident);
                } else {
                    notificationService.notifyIncidentResolved(incident);
                }
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
