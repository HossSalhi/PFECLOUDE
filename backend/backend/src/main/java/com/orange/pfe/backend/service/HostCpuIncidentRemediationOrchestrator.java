package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.orange.pfe.backend.service.remediation.RemediationPolicyService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Locale;
import java.util.Set;

/**
 * Raccord déterministe, limité aux incidents CPU des workers autorisés.
 * La politique existante reste l'autorité pour l'éligibilité automatique.
 */
@Service
public class HostCpuIncidentRemediationOrchestrator {

    private static final Logger log =
            LoggerFactory.getLogger(HostCpuIncidentRemediationOrchestrator.class);

    private static final Set<String> CPU_TYPES =
            Set.of("NODE_HIGH_CPU", "NODE_CPU_SATURATION");

    // CPU-MASTER-20261029 : k8s-master ajouté. La remédiation CPU ne plafonne QUE le service
    // de laboratoire helios-cpuload.service (résolu depuis l'allowlist du Bridge, jamais depuis
    // la requête) ; etcd/kubelet/kube-apiserver restent protégés par QUOTA_SERVICES_INTERDITS côté
    // Bridge ET par CPUWeight=10 du générateur (qui cède le CPU au control-plane sous contention).
    private static final Set<String> ALLOWED_TARGETS =
            Set.of("k8s-worker1", "k8s-worker2", "k8s-master");

    private final RemediationProperties properties;
    private final RemediationPolicyService policy;
    private final ActionService actionService;
    private final WorkflowService workflowService;
    private final CamundaWorkflowService camundaWorkflowService;
    private final IncidentHistoryService historyService;
    private final RemediationLaunchFailureService launchFailureService;
    private final com.orange.pfe.backend.repository.IncidentRepository incidentRepository;

    public HostCpuIncidentRemediationOrchestrator(
            RemediationProperties properties,
            RemediationPolicyService policy,
            ActionService actionService,
            WorkflowService workflowService,
            CamundaWorkflowService camundaWorkflowService,
            IncidentHistoryService historyService,
            RemediationLaunchFailureService launchFailureService,
            com.orange.pfe.backend.repository.IncidentRepository incidentRepository) {
        this.properties = properties;
        this.policy = policy;
        this.actionService = actionService;
        this.workflowService = workflowService;
        this.camundaWorkflowService = camundaWorkflowService;
        this.historyService = historyService;
        this.launchFailureService = launchFailureService;
        this.incidentRepository = incidentRepository;
    }

    @org.springframework.transaction.annotation.Transactional(propagation = org.springframework.transaction.annotation.Propagation.REQUIRES_NEW)
    public void startIfEligible(Incident sourceIncident) {
        if (sourceIncident == null || sourceIncident.getId() == null
                || sourceIncident.getIncidentType() == null) {
            return;
        }
        Incident incident = incidentRepository.findById(sourceIncident.getId()).orElse(null);
        if (incident == null) {
            log.warn("CPU remediation skipped: persisted incident not found");
            return;
        }

        String type = incident.getIncidentType().trim().toUpperCase(Locale.ROOT);
        if (!CPU_TYPES.contains(type)) {
            return;
        }

        String namespace = incident.getNamespace();
        if (!"k8s-nodes".equalsIgnoreCase(namespace)) {
            log.warn("CPU auto-remediation ignoree : namespace cible non autorise");
            return;
        }

        String rawTarget = incident.getApp();
        String target = rawTarget == null ? null : ALLOWED_TARGETS.stream()
                .filter(candidate -> candidate.equalsIgnoreCase(rawTarget.trim()))
                .findFirst()
                .orElse(null);

        if (target == null) {
            historyService.record(incident, IncidentStatus.OPEN,
                    "CPU auto-remediation non demarree : cible hors allowlist des workers.");
            log.warn("CPU auto-remediation refusee : cible hors allowlist");
            return;
        }

        // CPU-02 : garde independante. Lorsque Camunda v25 devient proprietaire unique
        // du scenario CPU, l operateur passe HOST_CPU_REMEDIATION_ENABLED=false : le
        // backend cesse alors de CREER une action ou de demarrer un workflow concurrent.
        // Defaut true => comportement historique inchange tant que non desactive.
        if (!properties.isHostCpuRemediationEnabled()) {
            historyService.record(incident, IncidentStatus.OPEN,
                    "CPU auto-remediation non demarree : HOST_CPU_REMEDIATION_ENABLED=false "
                    + "(proprietaire = Camunda v25).");
            log.info("CPU auto-remediation desactivee par configuration pour l incident {}",
                    incident.getId());
            return;
        }

        // DTO utilisé comme contrat interne : aucune requête IA n'est effectuée.
        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("k8s-nodes");
        request.setApp(target);
        request.setCreateIncident(false);

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType(type);
        prediction.setSeverity(
                incident.getSeverity() == null ? "MEDIUM" : incident.getSeverity().name());
        prediction.setConfidence(1.0d);
        prediction.setRecommendedAction("HOST_SERVICE_CPU_QUOTA");
        prediction.setFinalExecutableAction("HOST_SERVICE_CPU_QUOTA");
        prediction.setExecutionMode("AUTO");
        prediction.setDecisionExplanation(
                "Signal CPU du detecteur deterministe ; evaluation par la politique de remediation existante.");

        RemediationPlan plan = policy.buildPlan(request, prediction);

        if (plan == null
                || plan.actionType() != ActionType.HOST_SERVICE_CPU_QUOTA
                || !plan.autoExecutable()
                || plan.approvalRequired()
                || plan.manualFollowUp()) {
            String reason = plan == null
                    ? "plan de remediation absent"
                    : plan.rationale();

            historyService.record(incident, IncidentStatus.OPEN,
                    "CPU auto-remediation non demarree par la politique : " + reason);
            log.warn("CPU auto-remediation non demarree : politique non satisfaite");
            return;
        }

        WorkflowInstance workflow = workflowService.startWorkflow(
                incident, "Incident-Remediation-Workflow", "k8s-nodes", target);

        IncidentAction action = actionService.createAction(
                incident,
                ActionType.HOST_SERVICE_CPU_QUOTA,
                "Remediation CPU deterministe : quota de service borne via Bridge",
                "HELIOS-DETERMINISTIC",
                plan.targetNamespace(),
                RemediationTargetKind.NODE,
                target,
                AutomationMode.AUTO);

        incident.setStatus(IncidentStatus.IN_PROGRESS);
        incidentRepository.save(incident);
        historyService.record(incident, IncidentStatus.IN_PROGRESS,
                "Remediation CPU preparee ; lancement Camunda apres commit et verification attendue.");

        Runnable launchWorkflow = () -> {
            final CamundaLaunchOutcome outcome;
            try {
                outcome = camundaWorkflowService.startWorkflowWithOutcome(
                        workflow, action, plan, prediction, false);
            } catch (RuntimeException ex) {
                log.error("CPU remediation Camunda launch failed for incident {}",
                        incident.getId(), ex);
                try {
                    launchFailureService.recordCamundaLaunchFailure(
                            "CPU", incident.getId(), action.getId(), ex);
                } catch (RuntimeException persistenceEx) {
                    log.error("Could not persist CPU Camunda launch failure for incident {}",
                            incident.getId(), persistenceEx);
                }
                return;
            }

            switch (outcome) {
                case CAMUNDA_STARTED ->
                        log.info("CPU remediation workflow confirmed by Camunda for incident {}",
                                incident.getId());
                case LAUNCH_FAILED, NOT_STARTED -> {
                    RuntimeException failure = new IllegalStateException(
                            "Camunda did not start the CPU workflow; direct fallback was disabled. Outcome="
                                    + outcome);
                    try {
                        launchFailureService.recordCamundaLaunchFailure(
                                "CPU", incident.getId(), action.getId(), failure);
                    } catch (RuntimeException persistenceEx) {
                        log.error("Could not persist CPU launch failure for incident {}",
                                incident.getId(), persistenceEx);
                    }
                }
                case PROCESS_ALREADY_ATTACHED, DIRECT_FALLBACK_EXECUTED,
                        DIRECT_FALLBACK_FAILED, MANUAL_FOLLOW_UP_REQUIRED -> {
                    String message = "CPU Camunda outcome requires reconciliation: " + outcome
                            + ". No automatic retry was started to avoid duplicate remediation.";
                    log.warn("CPU remediation requires reconciliation for incident {}: {}",
                            incident.getId(), outcome);
                    try {
                        historyService.record(incident, IncidentStatus.IN_PROGRESS, message);
                    } catch (RuntimeException historyEx) {
                        log.error("Could not persist CPU reconciliation history for incident {}",
                                incident.getId(), historyEx);
                    }
                }
            }
        };

        if (org.springframework.transaction.support.TransactionSynchronizationManager
                .isSynchronizationActive()) {
            org.springframework.transaction.support.TransactionSynchronizationManager
                    .registerSynchronization(
                            new org.springframework.transaction.support.TransactionSynchronization() {
                                @Override
                                public void afterCommit() {
                                    launchWorkflow.run();
                                }
                            });
        } else {
            // Support des tests unitaires qui instancient directement le service,
            // sans proxy transactionnel Spring.
            launchWorkflow.run();
        }
    }
}