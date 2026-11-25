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
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.orange.pfe.backend.service.remediation.RemediationPolicyService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Locale;
import java.util.Set;

/**
 * Raccord déterministe pour les incidents MÉMOIRE des workers autorisés — MODÈLE CPU.
 *
 * <p>La remédiation RAM automatique réelle (plafond mémoire d'un service de charge de LABORATOIRE via le
 * Bridge, à l'image de {@code HOST_SERVICE_CPU_QUOTA}) n'est activée que lorsque TOUTES les préconditions
 * de sûreté sont réunies : {@code HOST_MEMORY_REMEDIATION_ENABLED=true}, cible dans l'allowlist des workers,
 * Bridge actif, action autorisée et politique satisfaite. Dans ce cas, l'action est créée et le workflow
 * Camunda est lancé ; la vérification mesurée ({@link com.orange.pfe.backend.service.remediation.HostMemoryVerificationService},
 * deux observations saines consécutives) décide seule du RESOLVED.
 *
 * <p><b>Fail-closed :</b> si le drapeau est faux, si la cible n'est pas autorisée ou si la politique ne
 * permet pas une exécution automatique sûre, AUCUNE action n'est créée et AUCUN Bridge n'est appelé —
 * l'incident est ESCALADÉ de façon déterministe et tracée, et reste OUVERT pour intervention humaine.
 * Aucun succès n'est jamais fabriqué. Jamais de {@code drop_caches}/{@code pkill}/éviction générique,
 * jamais de contournement de PDB, jamais de redémarrage d'un composant critique (PG primaire, etcd,
 * control-plane, backend) au seul motif d'une mémoire élevée.
 */
@Service
public class HostMemoryIncidentRemediationOrchestrator {

    private static final Logger log =
            LoggerFactory.getLogger(HostMemoryIncidentRemediationOrchestrator.class);

    private static final Set<String> MEMORY_TYPES =
            Set.of("NODE_HIGH_MEMORY", "NODE_MEMORY_PRESSURE");

    private static final Set<String> ALLOWED_TARGETS =
            Set.of("k8s-worker1", "k8s-worker2");

    private final RemediationProperties properties;
    private final RemediationPolicyService policy;
    private final ActionService actionService;
    private final WorkflowService workflowService;
    private final CamundaWorkflowService camundaWorkflowService;
    private final IncidentHistoryService historyService;
    private final RemediationLaunchFailureService launchFailureService;
    private final IncidentRepository incidentRepository;

    public HostMemoryIncidentRemediationOrchestrator(
            RemediationProperties properties,
            RemediationPolicyService policy,
            ActionService actionService,
            WorkflowService workflowService,
            CamundaWorkflowService camundaWorkflowService,
            IncidentHistoryService historyService,
            RemediationLaunchFailureService launchFailureService,
            IncidentRepository incidentRepository) {
        this.properties = properties;
        this.policy = policy;
        this.actionService = actionService;
        this.workflowService = workflowService;
        this.camundaWorkflowService = camundaWorkflowService;
        this.historyService = historyService;
        this.launchFailureService = launchFailureService;
        this.incidentRepository = incidentRepository;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void startIfEligible(Incident sourceIncident) {
        if (sourceIncident == null
                || sourceIncident.getId() == null
                || sourceIncident.getIncidentType() == null) {
            return;
        }

        Incident incident = incidentRepository.findById(sourceIncident.getId()).orElse(null);
        if (incident == null) {
            log.warn("RAM remediation skipped: persisted incident not found");
            return;
        }

        String type = incident.getIncidentType().trim().toUpperCase(Locale.ROOT);
        if (!MEMORY_TYPES.contains(type)) {
            return;
        }

        if (!"k8s-nodes".equalsIgnoreCase(incident.getNamespace())) {
            escalate(incident, "namespace cible hors du perimetre approuve");
            return;
        }

        String rawTarget = incident.getApp();
        String target = rawTarget == null ? null : ALLOWED_TARGETS.stream()
                .filter(candidate -> candidate.equalsIgnoreCase(rawTarget.trim()))
                .findFirst()
                .orElse(null);
        if (target == null) {
            escalate(incident, "cible hors de l'allowlist des workers");
            return;
        }

        // Fail-closed : tant que la cible labo memoire + l'action memoire du Bridge ne sont pas
        // provisionnees+deployees, le drapeau reste faux => ESCALADE (jamais d'action fabriquee).
        if (!properties.isHostMemoryRemediationEnabled()) {
            escalate(incident, "remediation RAM desactivee (HOST_MEMORY_REMEDIATION_ENABLED=false ; "
                    + "cible labo memoire/action Bridge non provisionnees)");
            return;
        }

        // Contrat interne partage avec les chemins deterministes ; aucune requete IA.
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
        prediction.setRecommendedAction(ActionType.HOST_SERVICE_MEMORY_LIMIT.name());
        prediction.setFinalExecutableAction(ActionType.HOST_SERVICE_MEMORY_LIMIT.name());
        prediction.setExecutionMode("AUTO");
        prediction.setDecisionExplanation(
                "Signal memoire du detecteur deterministe ; evaluation par la politique de remediation existante.");

        RemediationPlan plan = policy.buildPlan(request, prediction);

        if (plan == null
                || plan.actionType() != ActionType.HOST_SERVICE_MEMORY_LIMIT
                || !plan.autoExecutable()
                || plan.approvalRequired()
                || plan.manualFollowUp()) {
            String reason = plan == null ? "plan de remediation absent" : plan.rationale();
            escalate(incident, "politique non satisfaite (" + reason + ")");
            return;
        }

        WorkflowInstance workflow = workflowService.startWorkflow(
                incident, "Incident-Remediation-Workflow", "k8s-nodes", target);

        IncidentAction action = actionService.createAction(
                incident,
                ActionType.HOST_SERVICE_MEMORY_LIMIT,
                "Remediation RAM deterministe : plafond memoire borne du service de charge labo via Bridge",
                "HELIOS-DETERMINISTIC",
                plan.targetNamespace(),
                RemediationTargetKind.NODE,
                target,
                AutomationMode.AUTO);

        incident.setStatus(IncidentStatus.IN_PROGRESS);
        incidentRepository.save(incident);
        historyService.record(incident, IncidentStatus.IN_PROGRESS,
                "Remediation RAM preparee ; lancement Camunda apres commit et verification mesuree attendue "
                + "(2 observations saines consecutives avant RESOLVED).");

        Runnable launchWorkflow = () -> {
            final CamundaLaunchOutcome outcome;
            try {
                outcome = camundaWorkflowService.startWorkflowWithOutcome(
                        workflow, action, plan, prediction, false);
            } catch (RuntimeException ex) {
                log.error("RAM remediation Camunda launch failed for incident {}", incident.getId(), ex);
                try {
                    launchFailureService.recordCamundaLaunchFailure(
                            "RAM", incident.getId(), action.getId(), ex);
                } catch (RuntimeException persistenceEx) {
                    log.error("Could not persist RAM Camunda launch failure for incident {}",
                            incident.getId(), persistenceEx);
                }
                return;
            }

            switch (outcome) {
                case CAMUNDA_STARTED ->
                        log.info("RAM remediation workflow confirmed by Camunda for incident {}",
                                incident.getId());
                case LAUNCH_FAILED, NOT_STARTED -> {
                    RuntimeException failure = new IllegalStateException(
                            "Camunda did not start the RAM workflow; direct fallback was disabled. Outcome="
                                    + outcome);
                    try {
                        launchFailureService.recordCamundaLaunchFailure(
                                "RAM", incident.getId(), action.getId(), failure);
                    } catch (RuntimeException persistenceEx) {
                        log.error("Could not persist RAM launch failure for incident {}",
                                incident.getId(), persistenceEx);
                    }
                }
                case PROCESS_ALREADY_ATTACHED, DIRECT_FALLBACK_EXECUTED,
                        DIRECT_FALLBACK_FAILED, MANUAL_FOLLOW_UP_REQUIRED -> {
                    String message = "RAM Camunda outcome requires reconciliation: " + outcome
                            + ". No automatic retry was started to avoid duplicate remediation.";
                    log.warn("RAM remediation requires reconciliation for incident {}: {}",
                            incident.getId(), outcome);
                    try {
                        historyService.record(incident, IncidentStatus.IN_PROGRESS, message);
                    } catch (RuntimeException historyEx) {
                        log.error("Could not persist RAM reconciliation history for incident {}",
                                incident.getId(), historyEx);
                    }
                }
            }
        };

        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(
                    new TransactionSynchronization() {
                        @Override
                        public void afterCommit() {
                            launchWorkflow.run();
                        }
                    });
        } else {
            launchWorkflow.run();
        }
    }

    /**
     * Escalade déterministe fail-closed : trace la raison, laisse l'incident OUVERT, ne crée aucune action
     * et n'appelle aucun Bridge/éviction. C'est le mécanisme de sécurité de dernier recours.
     */
    private void escalate(Incident incident, String reason) {
        historyService.record(incident, IncidentStatus.OPEN,
                "[RAM] Escalade deterministe (fail-closed) : " + reason
                + ". Aucune action auto executee, aucun Bridge/eviction ; incident laisse OUVERT "
                + "pour intervention humaine.");
        log.warn("RAM remediation non demarree (escalade) pour l'incident {} : {}",
                incident.getId(), reason);
    }
}
