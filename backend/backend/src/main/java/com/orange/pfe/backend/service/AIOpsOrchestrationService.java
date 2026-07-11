package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIOpsAnalyzeResponse;
import com.orange.pfe.backend.dto.AIPredictEvidence;
import com.orange.pfe.backend.dto.AIPredictRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.exception.IncidentNotFoundException;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import org.springframework.data.domain.PageRequest;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.decision.DecisionSource;
import com.orange.pfe.backend.model.decision.DecisionType;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.WorkflowRepository;
import com.orange.pfe.backend.service.remediation.RemediationMetricsService;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.orange.pfe.backend.service.remediation.RemediationPolicyService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;

@Service
public class AIOpsOrchestrationService {

    private static final Logger log = LoggerFactory.getLogger(AIOpsOrchestrationService.class);

    // RESOLUTION_FAILED est inclus VOLONTAIREMENT : un incident dont la remediation a echoue reste
    // "non resolu" -> autoResolveIfCleared doit pouvoir le CLORE automatiquement des que l'app
    // redevient saine (predict() = plus d'incident). Sinon un RESOLUTION_FAILED resterait ouvert a vie
    // ET (cote scheduler) un nouvel incident du meme type reapparaitrait a chaque fenetre de dedup
    // = BOUCLE de fausses resolutions. Avec RESOLUTION_FAILED ici + dans AIOpsPollingScheduler : 1 seul
    // incident par panne persistante (jamais de boucle), auto-clos des la guerison (aucune action manuelle).
    private static final List<IncidentStatus> ACTIVE_STATUSES = List.of(
            IncidentStatus.NEW,
            IncidentStatus.OPEN,
            IncidentStatus.IN_PROGRESS,
            IncidentStatus.WAITING_FOR_APPROVAL,
            IncidentStatus.RESOLUTION_FAILED
    );

    private final AIOpsClientService aiOpsClientService;
    private final IncidentRepository incidentRepository;
    private final IncidentActionRepository incidentActionRepository;
    private final WorkflowRepository workflowRepository;
    private final WorkflowService workflowService;
    private final ActionService actionService;
    private final IncidentHistoryService historyService;
    private final IncidentDecisionService decisionService;
    private final RemediationPolicyService remediationPolicyService;
    private final CamundaWorkflowService camundaWorkflowService;
    private final RemediationMetricsService remediationMetricsService;
    private final KubernetesSignalEnrichmentService kubernetesSignalEnrichmentService;
    private final RemediationHistoryContextService remediationHistoryContextService;
    private final NotificationService notificationService;
    private final TransactionTemplate transactionTemplate;

    public AIOpsOrchestrationService(
            AIOpsClientService aiOpsClientService,
            IncidentRepository incidentRepository,
            IncidentActionRepository incidentActionRepository,
            WorkflowRepository workflowRepository,
            WorkflowService workflowService,
            ActionService actionService,
            IncidentHistoryService historyService,
            IncidentDecisionService decisionService,
            RemediationPolicyService remediationPolicyService,
            CamundaWorkflowService camundaWorkflowService,
            RemediationMetricsService remediationMetricsService,
            KubernetesSignalEnrichmentService kubernetesSignalEnrichmentService,
            RemediationHistoryContextService remediationHistoryContextService,
            NotificationService notificationService,
            PlatformTransactionManager transactionManager
    ) {
        this.aiOpsClientService = aiOpsClientService;
        this.incidentRepository = incidentRepository;
        this.incidentActionRepository = incidentActionRepository;
        this.workflowRepository = workflowRepository;
        this.workflowService = workflowService;
        this.actionService = actionService;
        this.historyService = historyService;
        this.decisionService = decisionService;
        this.remediationPolicyService = remediationPolicyService;
        this.camundaWorkflowService = camundaWorkflowService;
        this.remediationMetricsService = remediationMetricsService;
        this.kubernetesSignalEnrichmentService = kubernetesSignalEnrichmentService;
        this.remediationHistoryContextService = remediationHistoryContextService;
        this.notificationService = notificationService;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
    }

    public AIOpsAnalyzeResponse analyze(AIOpsAnalyzeRequest request) {
        MDC.put("namespace", request.getNamespace());
        MDC.put("app", request.getApp());
        try {
            log.info("AIOps analyze started ns={} app={}", request.getNamespace(), request.getApp());

            // Camunda = ORCHESTRATEUR CENTRAL du projet : AUCUNE détection (création d'incident) si
            // Camunda est injoignable. Point de contrôle UNIQUE couvrant TOUS les déclencheurs qui
            // passent par analyze() — le polling scheduler ET le watcher K8s de déploiements ET le
            // webhook d'alerte. Camunda arrêté => 0 incident détecté (l'IA n'est même pas sollicitée).
            if (request.isCreateIncident() && !camundaWorkflowService.isReachable()) {
                log.warn("Détection ignorée pour {}/{} — Camunda injoignable (orchestrateur central requis)",
                        request.getNamespace(), request.getApp());
                AIOpsAnalyzeResponse camundaDownResp = new AIOpsAnalyzeResponse();
                camundaDownResp.setIncidentCreated(false);
                camundaDownResp.setMessage("Camunda injoignable — détection suspendue (orchestrateur central)");
                return camundaDownResp;
            }
            // DÉTECTION = CAMUNDA + MONITORING (pas l'IA). L'IA est le CERVEAU qui RECOMMANDE, pas la
            // garde de détection : si elle est injoignable, on NE bloque PAS. La détection continue et la
            // classification bascule en repli DÉTERMINISTE (marqueurs d'état K8s ci-dessous). Camunda
            // (orchestrateur central) reste la SEULE garde bloquante (vérifiée juste au-dessus).
            final boolean aiReachable = aiOpsClientService.isReachable();
            if (!aiReachable) {
                log.warn("Service IA injoignable pour {}/{} — Camunda détecte quand même, classification en repli DÉTERMINISTE (marqueurs K8s)",
                        request.getNamespace(), request.getApp());
            }

            AIPredictRequest predictRequest = new AIPredictRequest();
            predictRequest.setNamespace(request.getNamespace());
            predictRequest.setApp(request.getApp());
            predictRequest.setWindowMinutes(request.getWindowMinutes());
            predictRequest.setEndTime(request.getEndTime());
            java.util.Map<String, Double> featureOverrides =
                    kubernetesSignalEnrichmentService.collectFeatureOverrides(request.getNamespace(), request.getApp());
            // Extract markers before sending to AI (the AI doesn't know these keys).
            final boolean forceRollback = featureOverrides.remove("image_pull_backoff_detected") != null;
            final boolean forceCrashLoop = featureOverrides.remove("crash_loop_backoff_detected") != null;
            final boolean forceUnavailable = featureOverrides.remove("service_unreachable_detected") != null;
            final boolean forceNetwork = featureOverrides.remove("blocking_network_policy_detected") != null;
            final boolean forceDns = featureOverrides.remove("dns_blocked") != null;
            final boolean forceDb = featureOverrides.remove("db_blocked") != null;
            final boolean forcePartition = featureOverrides.remove("network_partition") != null;
            final boolean forceOOM = featureOverrides.remove("oom_killed_detected") != null;
            final boolean forceDisk = featureOverrides.remove("disk_pressure_detected") != null;
            final boolean forceHttpError = featureOverrides.remove("http_error_storm_detected") != null;
            final boolean forceLatency = featureOverrides.remove("http_latency_detected") != null;
            final boolean forceConfig = featureOverrides.remove("config_error_detected") != null;
            final boolean forceInit = featureOverrides.remove("init_container_failure_detected") != null;
            final boolean forcePending = featureOverrides.remove("pod_pending_unschedulable_detected") != null;
            final boolean forceSaturation = featureOverrides.remove("replica_saturation_detected") != null;
            final boolean forceStuckTerminating = featureOverrides.remove("pod_stuck_terminating_detected") != null;
            final boolean forceDeployUnavail = featureOverrides.remove("deployment_unavailable_detected") != null;
            final boolean transientRollout = featureOverrides.remove("transient_rollout_detected") != null;
            final boolean autoscaleEnabled = featureOverrides.remove("autoscale_enabled") != null;
            final boolean nodeOutage = featureOverrides.remove("node_outage_detected") != null;
            // Belt-and-suspenders: even if the fast outage flag hasn't fired yet, a non-zero
            // node_not_ready_count means a node hosting pods is already down — any per-app fault right
            // now is collateral. (Peeked, NOT removed: it stays a feature for the AI prediction.)
            final boolean nodeDownNow = featureOverrides.getOrDefault("node_not_ready_count", 0.0) > 0.0;

            // Un fait de panne K8s CERTAIN (marqueur d'état) est-il présent ? (indépendant de l'IA).
            // Sert UNIQUEMENT au repli d'ESCALADE quand l'IA est injoignable (voir plus bas) : PLUS de
            // remédiation déterministe fabriquée. Le garde-fou force* n'agit QUE lorsque l'IA est joignable
            // (corrections de faits qu'elle ne voit pas : NetworkPolicy, finalizer). IA muette => escalade.
            final boolean anyDeterministicFault = forceRollback || forceCrashLoop || forceUnavailable
                    || forceNetwork || forceOOM || forceDisk || forceHttpError || forceLatency
                    || forceConfig || forceInit || forcePending || forceSaturation
                    || forceStuckTerminating || forceDeployUnavail;

            // NODE OUTAGE in progress: a worker is NotReady, so EVERY app that had a pod on it is
            // rescheduling and surfaces as DEPLOYMENT_UNAVAILABLE / POD_CRASH_LOOP churn. Those per-app
            // incidents are pure COLLATERAL of the node down (the single k8s-nodes incident already
            // tracks it). Pause per-app incident creation until the node recovers — otherwise a single
            // worker-down floods the table with N per-app duplicates. (Analysis-only calls still run.)
            if ((nodeOutage || nodeDownNow) && request.isCreateIncident()) {
                log.info("Node outage in progress — pausing per-app incident creation for {}/{} "
                        + "(reschedule churn is collateral of the node-level incident)",
                        request.getNamespace(), request.getApp());
                AIOpsAnalyzeResponse outageResp = new AIOpsAnalyzeResponse();
                outageResp.setIncidentCreated(false);
                outageResp.setMessage("Node outage in progress — per-app detection paused (collateral of node down)");
                return outageResp;
            }
            // http_5xx_ratio / http_latency_p95_seconds are REAL measured values (kept in features for the title).
            predictRequest.setFeatureOverrides(featureOverrides);
            predictRequest.setRemediationContext(
                    remediationHistoryContextService.buildContext(request.getNamespace(), request.getApp())
            );

            // L'IA (cerveau) CLASSE/RECOMMANDE quand elle est joignable. Sinon, repli DÉTERMINISTE :
            // prédiction NEUTRE que SEULS les marqueurs force* (état K8s certain, ci-dessous) rempliront
            // -> incident classé + remédié SANS IA. Aucun marqueur => reste non-incident (rien à classer,
            // ex. CPU_STRESS/AUTOSCALING qui exigent le cerveau : on n'invente pas de classification).
            AIPredictResponse prediction;
            if (aiReachable) {
                prediction = aiOpsClientService.predict(predictRequest);
            } else {
                prediction = new AIPredictResponse();
                prediction.setIncident(false);
                prediction.setConfidence(0.0);
                prediction.setFeatures(new java.util.HashMap<>());
            }
            // L'IA DÉCIDE EN PREMIER (dynamique, intelligent) : si elle est joignable ET propose une action
            // EXÉCUTABLE sûre, HELIOS garde SA décision telle quelle — plus d'override déterministe visible.
            // Le force* ne sert plus qu'en SECOURS (IA down/muette/sans action exécutable). EXCEPTIONS :
            // forceNetwork (NetworkPolicy bloquante) + forceStuckTerminating (finalizer) = faits K8s que l'IA
            // NE VOIT PAS (absents de Prometheus) -> ils priment (sûreté), sinon rollback futile.
            final String aiExecPref = safe(prediction.getFinalExecutableAction());
            final boolean aiDecides = aiReachable && prediction.isIncident()
                    && ("ROLLBACK_DEPLOYMENT".equals(aiExecPref) || "RESTART_POD".equals(aiExecPref)
                        || "SCALE_DEPLOYMENT".equals(aiExecPref) || "SCALE_DOWN".equals(aiExecPref)
                        || "DELETE_CRASHLOOPING_PODS".equals(aiExecPref));
            // For ImagePullBackOff, override the AI recommendation regardless of confidence:
            // the K8s state is unambiguous and ROLLBACK_DEPLOYMENT is the correct deterministic fix.
            if (!aiReachable) {
                // IA INJOIGNABLE : le garde-fou déterministe force* est EFFACÉ (exigence produit :
                // « jamais de remédiation déterministe »). On n'invente AUCUNE action codée en dur.
                // Si un fait de panne K8s réel est présent, on ESCALADE vers l'astreinte (webhook +
                // email, action ESCALATION) au lieu de deviner ; sinon on ne classe rien (on défère
                // au retour de l'IA — le cerveau du système).
                if (anyDeterministicFault) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.0);
                    prediction.setIncidentType("AI_UNAVAILABLE");
                    prediction.setRecommendedAction("ESCALATE_AI_UNAVAILABLE");
                    prediction.setFinalExecutableAction(null);
                    log.warn("IA injoignable + panne K8s pour {}/{} — ESCALADE humaine (aucune remédiation déterministe)",
                            request.getNamespace(), request.getApp());
                }
            } else if (forceStuckTerminating) {
                // Pod bloqué en Terminating (finalizer non libéré) : la remédiation PRO = suppression
                // forcée (retrait du finalizer). Type + action déterministes, plan construit en bypass.
                prediction.setIncidentType("POD_STUCK_TERMINATING");
                prediction.setRecommendedAction("FORCE_DELETE_POD");
                prediction.setFinalExecutableAction("FORCE_DELETE_POD");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("MEDIUM");
                    prediction.setConfidence(0.93);
                }
            } else if (forceNetwork) {
                // A recent DENY NetworkPolicy is the ROOT CAUSE (highest priority) — whether it shows as
                // unreachable or as NotReady/crash. The concrete fix is to REMOVE that policy, never a
                // rollback/restart (which would be futile while the policy persists). A DNS-blocking
                // policy (egress without port 53) gets the distinct DNS_FAILURE type.
                prediction.setIncidentType(forceDns ? "DNS_FAILURE"
                        : forceDb ? "DATABASE_CONNECTIVITY"
                        : forcePartition ? "NETWORK_PARTITION" : "SERVICE_UNAVAILABLE");
                prediction.setRecommendedAction("REMOVE_NETWORK_POLICY");
                prediction.setFinalExecutableAction("REMOVE_NETWORK_POLICY"); // wins over AI action in buildPlan
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.92);
                }
            } else if (aiDecides) {
                // L'IA a proposé une action EXÉCUTABLE sûre -> on GARDE sa décision (dynamique) : action
                // + explication viennent de l'IA. MAIS le TYPE affiché doit dire la VÉRITÉ. Un marqueur
                // d'état conteneur K8s (reason kubelet = fait CERTAIN que l'IA ne voit pas dans ses
                // features) CORRIGE le type quand l'IA l'a mal classé — cas prouvé : init / pending /
                // latence s'effondrent en POD_IMAGE_PULL_BACKOFF (ML feature-dominé). Le kubelet ne se
                // trompe pas sur SON état ; l'ACTION (rollback/delete...) reste la décision de l'IA.
                // Même logique d'exception que forceNetwork/forceStuckTerminating (faits invisibles à l'IA).
                final String aiType = prediction.getIncidentType();
                final String groundType = groundTruthContainerType(forceOOM, forceDisk, forceConfig,
                        forceInit, forcePending, forceSaturation, forceDeployUnavail, forceRollback, forceCrashLoop);
                if (groundType != null) {
                    if (!groundType.equals(aiType)) {
                        log.info("TYPE corrigé par la VÉRITÉ TERRAIN K8s pour {}/{} : IA='{}' -> '{}' "
                                + "(action IA conservée : {})", request.getNamespace(), request.getApp(),
                                aiType, groundType, aiExecPref);
                        prediction.setIncidentType(groundType);
                    }
                } else if (isUncorroboratedK8sType(aiType)) {
                    // L'IA nomme un état conteneur précis (ImagePullBackOff / init) que le cluster ne
                    // MONTRE PAS (aucun marqueur K8s correspondant, ex. latence sans métrique .160) ->
                    // on reste FIDÈLE aux faits : répliques indisponibles => DEPLOYMENT_UNAVAILABLE
                    // (remédié par rollback), sinon signal ambigu => GENERAL_DEGRADATION (non persisté).
                    double unavail = feat(prediction.getFeatures(), "unavailable_replicas");
                    String corrected = unavail >= 1 ? "DEPLOYMENT_UNAVAILABLE" : "GENERAL_DEGRADATION";
                    log.info("Type IA NON corroboré pour {}/{} : '{}' -> '{}' (aucun marqueur K8s correspondant)",
                            request.getNamespace(), request.getApp(), aiType, corrected);
                    prediction.setIncidentType(corrected);
                }
                log.info("Action décidée par l'IA (DYNAMIQUE) pour {}/{}: type={} action={} confiance={}",
                        request.getNamespace(), request.getApp(),
                        prediction.getIncidentType(), aiExecPref, prediction.getConfidence());
            } else if (forceOOM) {
                // OOMKilled (memory test): revert the change that lowered the memory limit -> rollback.
                prediction.setIncidentType("MEMORY_STRESS");
                prediction.setRecommendedAction("ROLLBACK_DEPLOYMENT");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.93);
                }
            } else if (forceDisk) {
                // Pod evicted by disk/ephemeral-storage pressure (disk test). K8s already self-heals the
                // eviction (recreates a fresh pod), leaving a Succeeded/Evicted pod behind that keeps
                // re-firing the signal. The real, fast fix = DELETE that evicted pod (clears the signal,
                // the healthy pod stays). A single delete completes the Camunda job immediately — unlike
                // FORCE_RECREATE whose rollout churns and times out on the constrained single-node Zeebe.
                prediction.setIncidentType("NODE_DISK_PRESSURE");
                prediction.setRecommendedAction("DELETE_CRASHLOOPING_PODS");
                prediction.setFinalExecutableAction("DELETE_CRASHLOOPING_PODS");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.91);
                }
            } else if (forceHttpError) {
                // HTTP 5xx storm measured by the active /metrics scrape (http503 test). The cause is a
                // bad revision (e.g. podinfo --random-error) -> rollback to the healthy revision restores
                // 2xx responses (a restart alone would recreate the same faulty spec).
                prediction.setIncidentType("HIGH_ERROR_RATE");
                prediction.setRecommendedAction("ROLLBACK_DEPLOYMENT");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.90);
                }
            } else if (forceLatency) {
                // High request latency measured via the active /metrics scrape (latency test). Cause = bad
                // revision (e.g. podinfo --random-delay) -> rollback restores normal response times.
                prediction.setIncidentType("LATENCY_DEGRADATION");
                prediction.setRecommendedAction("ROLLBACK_DEPLOYMENT");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("MEDIUM");
                    prediction.setConfidence(0.88);
                }
            } else if (forceConfig) {
                // Container config error (missing ConfigMap/Secret): deterministic K8s state -> rollback.
                prediction.setIncidentType("POD_CONFIG_ERROR");
                prediction.setRecommendedAction("ROLLBACK_DEPLOYMENT");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.94);
                }
            } else if (forceInit) {
                // Init container failure: the init phase fails so the app never starts -> rollback.
                prediction.setIncidentType("POD_INIT_CONTAINER_FAILURE");
                prediction.setRecommendedAction("ROLLBACK_DEPLOYMENT");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.94);
                }
            } else if (forceSaturation) {
                // SATURATION DE REPLICAS : beaucoup de pods Pending car TROP de replicas pour la capacité.
                // La bonne remédiation = SCALE_DOWN (réduire au nombre planifiable), PAS un rollback (qui
                // ne change pas le nombre de replicas). Plan SCALE_DOWN construit en bypass (voir plus bas).
                prediction.setIncidentType("REPLICA_SATURATION");
                prediction.setRecommendedAction("SCALE_DOWN");
                prediction.setFinalExecutableAction("SCALE_DOWN");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.92);
                }
            } else if (forcePending) {
                // Pod stuck Pending/Unschedulable (oversized resource request) -> rollback reverts the request.
                prediction.setIncidentType("POD_PENDING_INSUFFICIENT_RESOURCES");
                prediction.setRecommendedAction("ROLLBACK_DEPLOYMENT");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.93);
                }
            } else if (forceDeployUnavail) {
                // Running but persistently NotReady (broken readiness probe) -> rollback reverts the probe.
                prediction.setIncidentType("DEPLOYMENT_UNAVAILABLE");
                prediction.setRecommendedAction("ROLLBACK_DEPLOYMENT");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.92);
                }
            } else if (forceRollback) {
                prediction.setIncidentType("POD_IMAGE_PULL_BACKOFF");
                prediction.setRecommendedAction("ROLLBACK_DEPLOYMENT");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.95);
                }
            } else if (forceCrashLoop) {
                // CrashLoopBackOff is unambiguous from the K8s state: classify POD_CRASH_LOOP and
                // roll back to the last working revision (the bad image/command/config is the cause;
                // deleting the pod just recreates the same broken spec).
                prediction.setIncidentType("POD_CRASH_LOOP");
                prediction.setRecommendedAction("ROLLBACK_DEPLOYMENT");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.95);
                }
            } else if (forceUnavailable) {
                // Active probe found the pod unreachable (network partition): cAdvisor still reports
                // normal metrics so the AI alone would miss it. The ROOT CAUSE is a blocking NetworkPolicy,
                // so the concrete remediation is to REMOVE that policy (kubectl delete networkpolicy) —
                // a restart would be futile. The executor only removes a recently-applied policy that
                // selects this app, else it escalates honestly.
                // Unreachable but NO blocking NetworkPolicy exists (forceNetwork already handled that
                // case above with higher priority). This is most often TRANSIENT churn (a pod kill makes
                // recreating pods briefly fail the TCP probe). A rolling restart is the safe concrete fix
                // — NOT REMOVE_NETWORK_POLICY (there is no policy to remove).
                prediction.setIncidentType("DEPLOYMENT_UNAVAILABLE");
                prediction.setRecommendedAction("FORCE_RECREATE_DEPLOYMENT");
                prediction.setFinalExecutableAction("FORCE_RECREATE_DEPLOYMENT");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("HIGH");
                    prediction.setConfidence(0.90);
                }
            } else {
                // Coherence guard: on a weak/ambiguous signal the AI sometimes labels a Kubernetes
                // state-specific type (e.g. POD_IMAGE_PULL_BACKOFF) that the cluster does NOT actually
                // show — the "Why" then even admits "cause not identified". When such a label is not
                // corroborated by the K8s enrichment, normalize it to GENERAL_DEGRADATION so the type
                // stays faithful to the evidence.
                String t = prediction.getIncidentType();
                if (isUncorroboratedK8sType(t)) {
                    log.info("Reclassifying uncorroborated AI type '{}' -> GENERAL_DEGRADATION (no matching K8s signal)", t);
                    prediction.setIncidentType("GENERAL_DEGRADATION");
                    if (prediction.getRecommendedAction() == null || prediction.getRecommendedAction().isBlank()) {
                        prediction.setRecommendedAction("CAPTURE_DIAGNOSTICS_AND_INVESTIGATE");
                    }
                }
            }
            // HELIOS-as-autoscaler (HPA scenario): an app that opted into autoscaling (annotation
            // helios.io/autoscale=true) under CPU saturation is a MONTÉE EN CHARGE, not a runaway process
            // to restart — HELIOS adds capacity (SCALE_DEPLOYMENT, real `kubectl scale`) so the load is
            // spread. Reclassify the CPU signal to AUTOSCALING (wins over the AI's CPU_STRESS / restart).
            if (aiReachable && autoscaleEnabled && !forceNetwork && !forceOOM && !forceDisk && !forceConfig && !forceInit
                    && !forcePending && !forceCrashLoop && !forceRollback
                    && ("CPU_STRESS".equals(prediction.getIncidentType())
                        || feat(prediction.getFeatures(), "cpu_usage_cores") >= 0.20)) {
                log.info("Autoscale opt-in + CPU load for {}/{} -> AUTOSCALING (scale up)", request.getNamespace(), request.getApp());
                prediction.setIncidentType("AUTOSCALING");
                prediction.setRecommendedAction("SCALE_DEPLOYMENT");
                prediction.setFinalExecutableAction("SCALE_DEPLOYMENT");
                if (!prediction.isIncident()) {
                    prediction.setIncident(true);
                    prediction.setSeverity("MEDIUM");
                    prediction.setConfidence(0.88);
                }
            }
            // No passive INVESTIGATE anywhere: any residual investigate recommendation becomes a concrete
            // rolling restart, so the detection trace and the executed action stay consistent (the
            // executor never receives INVESTIGATION for an app incident).
            String ra = prediction.getRecommendedAction();
            if (ra != null && ra.toUpperCase(java.util.Locale.ROOT).contains("INVESTIGAT")
                    && !"NODE_NOT_READY".equals(prediction.getIncidentType())) {
                prediction.setRecommendedAction("FORCE_RECREATE_DEPLOYMENT");
                prediction.setFinalExecutableAction("FORCE_RECREATE_DEPLOYMENT");
            }
            log.info("AI prediction: incident={} type={} severity={} confidence={} action={}",
                    prediction.isIncident(), prediction.getIncidentType(), prediction.getSeverity(),
                    prediction.getConfidence(), prediction.getRecommendedAction());

            AIOpsAnalyzeResponse response = new AIOpsAnalyzeResponse();
            response.setPrediction(prediction);

            if (!request.isCreateIncident()) {
                response.setIncidentCreated(false);
                response.setMessage("AI prediction computed only (incident creation disabled)");
                return response;
            }

            if (!prediction.isIncident()) {
                log.info("AI indicates no incident — skipping creation");
                response.setIncidentCreated(false);
                response.setMessage("AI indicates no incident");
                return response;
            }

            // GENERAL_DEGRADATION is the ambiguous/residual fallback (post-remediation metric tails,
            // weak uncorroborated signals). Every REAL fault maps to a specific marker-based type.
            // Per requirement, the incident table must only show specific, actionable types — so we
            // never persist a GENERAL_DEGRADATION incident; we treat it as inconclusive (no incident).
            if ("GENERAL_DEGRADATION".equals(prediction.getIncidentType())) {
                log.info("Suppressing GENERAL_DEGRADATION (ambiguous/residual signal) — not creating an incident");
                response.setIncidentCreated(false);
                response.setMessage("Ambiguous/residual signal (GENERAL_DEGRADATION) — suppressed, no incident created");
                return response;
            }

            // NODE_NOT_READY on an APP target is COLLATERAL of a node outage, not an app fault: when a
            // worker dies, EVERY app that had a pod on it gets classified NODE_NOT_READY, flooding the
            // table with one near-identical incident per app ("Nœud Kubernetes indisponible — helios-X").
            // The single node-level incident (k8s-nodes/<node>, owned by InfrastructureHealthScheduler)
            // is the source of truth for a down node and drives the RESTART_VM recovery. Suppress the
            // per-app duplicates so a worker-down shows ONE clean node incident, not N.
            if ("NODE_NOT_READY".equals(prediction.getIncidentType())) {
                log.info("Suppressing per-app NODE_NOT_READY for {}/{} — node outage is covered by the "
                        + "node-level incident (k8s-nodes), not a per-app fault",
                        request.getNamespace(), request.getApp());
                response.setIncidentCreated(false);
                response.setMessage("Node-outage collateral — handled by the node-level incident; no per-app incident");
                return response;
            }

            // Transient rollout guard: a fault marker would have set a specific type above. If none did and
            // a pod is merely STARTING (young, not-ready), a generic DEPLOYMENT_UNAVAILABLE/AVAILABILITY_DROP
            // is just the rolling-update tail — suppress so the NEXT cycle classifies the real state
            // (e.g. HTTP 5xx storm once the new pod is Ready) instead of locking a wrong type via dedup.
            boolean genericUnavailable = "DEPLOYMENT_UNAVAILABLE".equals(prediction.getIncidentType())
                    || "AVAILABILITY_DROP".equals(prediction.getIncidentType());
            if (transientRollout && !forceDeployUnavail && genericUnavailable) {
                log.info("Suppressing transient rollout DEPLOYMENT_UNAVAILABLE for {}/{} — re-evaluate next cycle",
                        request.getNamespace(), request.getApp());
                response.setIncidentCreated(false);
                response.setMessage("Transient rolling update — classification deferred to next cycle");
                return response;
            }

            // Roll back when the Kubernetes state DETERMINISTICALLY confirms a bad-revision fault
            // (ImagePullBackOff / ACTIVE CrashLoopBackOff -> the force* markers above). PLUS: whenever the
            // incident TYPE itself is POD_CRASH_LOOP or DEPLOYMENT_UNAVAILABLE, force ROLLBACK even if the
            // deterministic marker did not fire at this exact poll (the crash loop can be caught early,
            // mid-rollout, before the kubelet sets CrashLoopBackOff -> the AI then picks a FUTILE action:
            // DELETE_CRASHLOOPING_PODS or FORCE_RECREATE_DEPLOYMENT just recreates the SAME bad spec ->
            // RESOLUTION_FAILED). ROLLBACK is the only root-cause fix for "pods can't stay up", and it
            // targets a healthy ReplicaSet, so it is safe even if the label was an early/over-read.
            final String predictedType = safe(prediction.getIncidentType());
            final boolean rollbackByType = "POD_CRASH_LOOP".equals(predictedType)
                    || "DEPLOYMENT_UNAVAILABLE".equals(predictedType);
            // L'IA fait AUTORITÉ sur l'action : si elle recommande (executable) un ROLLBACK ou un
            // SCALE_DOWN, HELIOS l'exécute via le même bypass que les marqueurs déterministes. Les
            // marqueurs force* deviennent un FILET (ils confirment/rattrapent quand l'IA a manqué le cas
            // ou est muette). => « l'IA recommande, Camunda exécute », plus de déterministe visible.
            final String aiExec = safe(prediction.getFinalExecutableAction());
            final boolean aiWantsScaleDown = "SCALE_DOWN".equals(aiExec);
            final boolean aiWantsRollback = "ROLLBACK_DEPLOYMENT".equals(aiExec);
            // Saturation de replicas : SCALE_DOWN, jamais rollback (le rollback ne réduit pas les replicas).
            // forcePending est aussi vrai (des pods sont Pending) -> on EXCLUT la saturation du rollback.
            // Pod bloqué en Terminating : suppression forcée (retrait finalizer) en bypass — jamais rollback.
            // Gardés par aiReachable : IA injoignable => AUCUN plan déterministe de bypass (le force* est
            // effacé dans ce cas, on escalade). IA joignable => comportement prouvé inchangé.
            final boolean useStuckDeletePlan = aiReachable && forceStuckTerminating;
            final boolean useScaleDownPlan = aiReachable && !forceStuckTerminating && (forceSaturation || (aiWantsScaleDown && !forceNetwork));
            final boolean useRollbackPlan = aiReachable && !forceStuckTerminating && !forceNetwork && !forceSaturation && !aiWantsScaleDown
                    && (forceRollback || forceCrashLoop || forceOOM
                    || forceHttpError || forceLatency || forceConfig || forceInit || forcePending
                    || forceDeployUnavail || rollbackByType || aiWantsRollback);

            AIOpsAnalyzeResponse result =
                    transactionTemplate.execute(status -> analyzeWithIncidentCreation(request, prediction, response, useRollbackPlan, useScaleDownPlan, useStuckDeletePlan));
            // Email "incident détecté" AFTER commit (avoids holding the DB tx during SMTP).
            if (result != null && result.isIncidentCreated() && result.getIncidentId() != null) {
                try {
                    incidentRepository.findById(result.getIncidentId())
                            .ifPresent(notificationService::notifyNewIncident);
                } catch (Exception ex) {
                    log.warn("Incident-detected notification failed: {}", ex.getMessage());
                }
            }
            return result;
        } finally {
            MDC.remove("namespace");
            MDC.remove("app");
            MDC.remove("incidentId");
        }
    }

    /**
     * ALARME BRUTE (déterministe, SANS IA) : cette app présente-t-elle un signal de panne ?
     * Utilisé par le pipeline « Camunda orchestre monitoring -> IA » : le backend/monitoring lève
     * l'alarme ici (sans classifier), PUIS Camunda (process incident-detection) appelle l'IA pour
     * classifier. On se base sur les marqueurs déterministes d'état Kubernetes (crashloop, OOM,
     * imagepull, config, init, pending, deployment-unavailable, network, dns, db, disk + 5xx/latence)
     * et la charge CPU soutenue (CPU_STRESS / AUTOSCALING). Les marqueurs NŒUD sont ignorés
     * (l'outage nœud est géré par InfrastructureHealthScheduler, pas par le per-app).
     */
    public boolean hasRawAlarm(String namespace, String app) {
        try {
            Map<String, Double> ov =
                    kubernetesSignalEnrichmentService.collectFeatureOverrides(namespace, app);
            for (String key : ov.keySet()) {
                if (key.contains("node")) {
                    continue;
                }
                if (key.endsWith("_detected")
                        || key.equals("dns_blocked")
                        || key.equals("db_blocked")
                        || key.equals("network_partition")) {
                    return true;
                }
            }
            // Pannes CPU (CPU_STRESS / AUTOSCALING) : AUCUN marqueur d'état K8s. La charge CPU est une
            // MÉTRIQUE (metrics-server) -> phase Monitor. Seuil 0.30 core : bien au-dessus de l'idle
            // (~1-10m) et sous le hog (~1000m). C'est ce qui manquait pour détecter cpu/hpa via Camunda.
            double cpuCores = kubernetesSignalEnrichmentService.maxPodCpuCores(namespace, app);
            return cpuCores >= 0.30;
        } catch (Exception ex) {
            log.debug("hasRawAlarm: lecture des signaux impossible pour {}/{}: {}", namespace, app, ex.getMessage());
            return false;
        }
    }

    /**
     * Types Kubernetes "état conteneur" qui n'ont de sens QUE si le cluster les montre réellement
     * (ImagePullBackOff, échec d'init container). Si on arrive ici, l'enrichissement K8s n'a PAS
     * corroboré d'erreur d'image (forceRollback=false) — donc une telle étiquette est une
     * sur-interprétation de l'IA sur un signal faible. On la ramène à un type honnête.
     */
    private boolean isUncorroboratedK8sType(String type) {
        if (type == null) {
            return false;
        }
        return switch (type) {
            case "POD_IMAGE_PULL_BACKOFF", "POD_INIT_CONTAINER_FAILURE" -> true;
            default -> false;
        };
    }

    /**
     * Type CANONIQUE déduit du marqueur d'état conteneur K8s (reason kubelet = VÉRITÉ TERRAIN),
     * ou null si aucun état conteneur précis n'est présent. Sert à CORRIGER le type affiché quand
     * l'IA décide (aiDecides) mais a mal classé un état que le kubelet identifie sans ambiguïté
     * (ex. init/pending -> POD_IMAGE_PULL_BACKOFF). L'ACTION reste la décision de l'IA.
     * Priorité = ordre de détection de KubernetesSignalEnrichmentService (un seul marqueur d'état
     * conteneur par incident ; saturation/deployUnavail en dernier recours).
     */
    private static String groundTruthContainerType(boolean oom, boolean disk, boolean config, boolean init,
            boolean pending, boolean saturation, boolean deployUnavail, boolean imagePull, boolean crashLoop) {
        if (oom)           return "POD_OOM_KILLED";
        if (disk)          return "POD_EVICTED";
        if (imagePull)     return "POD_IMAGE_PULL_BACKOFF";
        if (crashLoop)     return "POD_CRASH_LOOP";
        if (config)        return "POD_CONFIG_ERROR";
        if (init)          return "POD_INIT_CONTAINER_FAILURE";
        if (saturation)    return "REPLICA_SATURATION";
        if (pending)       return "POD_PENDING_INSUFFICIENT_RESOURCES";
        if (deployUnavail) return "DEPLOYMENT_UNAVAILABLE";
        return null;
    }

    @Transactional
    protected AIOpsAnalyzeResponse analyzeWithIncidentCreation(
            AIOpsAnalyzeRequest request,
            AIPredictResponse prediction,
            AIOpsAnalyzeResponse response,
            boolean useRollbackPlan,
            boolean useScaleDownPlan,
            boolean useStuckDeletePlan
    ) {
        Incident incident = createIncidentFromPrediction(prediction, request.getNamespace(), request.getApp());
        MDC.put("incidentId", String.valueOf(incident.getId()));
        log.info("Incident created id={} severity={} type={}", incident.getId(),
                incident.getSeverity(), incident.getIncidentType());

        historyService.record(incident, IncidentStatus.NEW, "Incident created from AI prediction");

        decisionService.recordDecision(
                incident,
                DecisionType.DETECT_INCIDENT,
                DecisionSource.SYSTEM,
                buildDetectionComment(prediction)
        );

        WorkflowInstance workflow = workflowService.startWorkflow(
                incident,
                "Incident-Remediation-Workflow",
                request.getNamespace(),
                request.getApp()
        );
        remediationMetricsService.workflowStarted();

        RemediationPlan plan;
        if (useRollbackPlan) {
            // ImagePullBackOff OR crash loop: bypass normal policy checks — rolling back to the last
            // valid revision is the correct deterministic ROOT-CAUSE fix and safe to auto-execute.
            // For a crash loop this reverts the bad image/command/config; for ImagePullBackOff it
            // restores the pullable image. (rollbackDeployment escalates honestly if no prev revision.)
            String rollbackReason = switch (safe(prediction.getIncidentType())) {
                case "POD_CRASH_LOOP" -> "Crash loop detecte — rollback auto vers la derniere revision valide (annule le changement fautif)";
                case "MEMORY_STRESS", "MEMORY_LEAK", "POD_OOM_KILLED" -> "OOMKilled / limite memoire trop basse — rollback auto vers la revision precedente";
                case "HIGH_ERROR_RATE", "APPLICATION_ERROR_STORM" -> "Pic d'erreurs 5xx — rollback auto vers la revision saine (resert des reponses 2xx)";
                case "HIGH_RESPONSE_TIME", "LATENCY_DEGRADATION" -> "Latence elevee — rollback auto vers la revision saine";
                case "POD_CONFIG_ERROR" -> "Erreur de config conteneur (ConfigMap/Secret manquant) — rollback auto vers la revision valide";
                case "POD_INIT_CONTAINER_FAILURE" -> "Echec du conteneur d'init — rollback auto vers la revision valide";
                case "POD_PENDING_INSUFFICIENT_RESOURCES" -> "Pod non planifiable (ressources) — rollback auto pour restaurer une demande viable";
                case "DEPLOYMENT_UNAVAILABLE", "AVAILABILITY_DROP" -> "Repliques indisponibles (sonde KO) — rollback auto vers la revision saine";
                default -> "ImagePullBackOff detecte — rollback auto vers la derniere revision valide";
            };
            plan = new com.orange.pfe.backend.service.remediation.RemediationPlan(
                    ActionType.ROLLBACK_DEPLOYMENT,
                    com.orange.pfe.backend.model.action.AutomationMode.AUTO,
                    false, true, false, false, false,
                    request.getNamespace(),
                    com.orange.pfe.backend.model.action.RemediationTargetKind.DEPLOYMENT,
                    request.getApp(),
                    rollbackReason
            );
        } else if (useScaleDownPlan) {
            // SATURATION de replicas : SCALE_DOWN construit en bypass (même mécanisme que le rollback) ->
            // auto-exécutable sans passer par l'allowlist. L'exécuteur réduit au nombre de pods planifiables.
            plan = new com.orange.pfe.backend.service.remediation.RemediationPlan(
                    ActionType.SCALE_DOWN,
                    com.orange.pfe.backend.model.action.AutomationMode.AUTO,
                    false, true, false, false, false,
                    request.getNamespace(),
                    com.orange.pfe.backend.model.action.RemediationTargetKind.DEPLOYMENT,
                    request.getApp(),
                    "Saturation de replicas — SCALE_DOWN auto vers le nombre de pods planifiables"
            );
        } else if (useStuckDeletePlan) {
            // Pod bloqué en Terminating : suppression forcée (retrait du finalizer) construite en bypass
            // -> auto-exécutable. L'exécuteur retire les finalizers du/des pod(s) coincé(s) de l'app.
            plan = new com.orange.pfe.backend.service.remediation.RemediationPlan(
                    ActionType.FORCE_DELETE_POD,
                    com.orange.pfe.backend.model.action.AutomationMode.AUTO,
                    false, true, false, false, false,
                    request.getNamespace(),
                    com.orange.pfe.backend.model.action.RemediationTargetKind.DEPLOYMENT,
                    request.getApp(),
                    "Pod bloqué en Terminating — suppression forcée (retrait du finalizer)"
            );
        } else {
            plan = remediationPolicyService.buildPlan(request, prediction);
        }
        ActionType actionType = plan.actionType();
        log.info("Remediation plan: action={} approvalRequired={} rationale={}",
                actionType, plan.approvalRequired(), plan.rationale());

        decisionService.recordDecision(
                incident,
                DecisionType.RECOMMEND_ACTION,
                DecisionSource.SYSTEM,
                "AI recommended action: " + safe(prediction.getRecommendedAction()) + " | " + plan.rationale()
        );

        var action = actionType == null
                ? null
                : actionService.createAction(
                        incident,
                        actionType,
                        buildActionDescription(prediction, plan),
                        "AI-SYSTEM",
                        plan.targetNamespace(),
                        plan.targetKind(),
                        plan.targetName(),
                        plan.automationMode()
                );

        if (actionType == null) {
            incident.setStatus(IncidentStatus.OPEN);
            workflowService.updateOrchestrationState(workflow, WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                    "AI returned recommendation-only guidance");
            historyService.record(incident, IncidentStatus.OPEN, "Incident opened with recommendation-only AI guidance");
            log.info("Incident {} set to OPEN — recommendation-only path", incident.getId());
        } else if (actionType == ActionType.ESCALATION) {
            incident.setStatus(IncidentStatus.OPEN);
            workflowService.updateOrchestrationState(workflow, WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                    "ESCALATION — critical incident requires immediate human attention");
            historyService.record(incident, IncidentStatus.OPEN, "ESCALATION — forwarded to on-call team");
            notificationService.notifyEscalation(incident);
            log.warn("Incident {} ESCALATED — webhook + email triggered for on-call team", incident.getId());
        } else {
            // Autonomous mode: all remediations execute automatically — no human approval step.
            // Override approvalRequired=false so the BPMN gateway routes directly to execution
            // instead of a Human Task (which would block until an operator approves manually).
            // Also force AutomationMode.AUTO so the action record reflects autonomous execution
            // even when buildPlan() returned MANUAL (e.g. cooldown blocked autoExecutable).
            if (action != null) {
                action.setAutomationMode(AutomationMode.AUTO);
            }
            RemediationPlan executionPlan = (plan.approvalRequired() || !plan.autoExecutable())
                    ? new com.orange.pfe.backend.service.remediation.RemediationPlan(
                            plan.actionType(), AutomationMode.AUTO,
                            false, true, plan.manualFollowUp(), plan.cooldownBlocked(), plan.dbRelated(),
                            plan.targetNamespace(), plan.targetKind(), plan.targetName(), plan.rationale())
                    : plan;
            camundaWorkflowService.startWorkflow(workflow, action, executionPlan, prediction);
            incident.setStatus(IncidentStatus.IN_PROGRESS);
            workflowService.updateOrchestrationState(workflow, WorkflowOrchestrationState.EXECUTION_PENDING,
                    "Autonomous remediation initiated — no human approval required");
            historyService.record(incident, IncidentStatus.IN_PROGRESS,
                    "Autonomous AI remediation path initiated (approval bypassed)");
            log.info("Incident {} IN_PROGRESS — autonomous path", incident.getId());
        }

        incident = incidentRepository.save(incident);

        response.setIncidentCreated(true);
        response.setIncidentId(incident.getId());
        response.setWorkflowId(workflow.getId());
        response.setIncidentStatus(incident.getStatus());
        response.setMessage("Incident created from AI prediction");
        return response;
    }

    /** Titre précis et lisible par type d'incident (au lieu du générique "AI Incident: TYPE — app"). */
    private String buildIncidentTitle(AIPredictResponse prediction, String app) {
        String type = safe(prediction.getIncidentType());
        String label = switch (type) {
            case "POD_CRASH_LOOP", "RESTART_LOOP"        -> "Pod en redémarrage en boucle (crash loop)";
            case "CPU_STRESS"                            -> "Saturation CPU";
            case "AUTOSCALING", "SCALING_UNDER_LOAD"     -> "Montée en charge (autoscaling)";
            case "MEMORY_STRESS", "MEMORY_LEAK", "POD_OOM_KILLED" -> "Pression mémoire / OOM";
            case "DEPLOYMENT_UNAVAILABLE"                -> "Déploiement indisponible";
            case "AVAILABILITY_DROP"                     -> "Chute de disponibilité";
            case "HIGH_RESPONSE_TIME", "LATENCY_DEGRADATION" -> "Latence HTTP élevée";
            case "HIGH_ERROR_RATE", "APPLICATION_ERROR_STORM" -> "Pic d'erreurs HTTP (5xx)";
            case "POD_IMAGE_PULL_BACKOFF"                -> "Échec de téléchargement d'image";
            case "POD_ERR_IMAGE_PULL"                    -> "Échec de récupération d'image";
            case "POD_PENDING_INSUFFICIENT_RESOURCES"    -> "Pod non planifiable (ressources)";
            case "POD_INIT_CONTAINER_FAILURE"            -> "Échec du conteneur d'init";
            case "POD_CONFIG_ERROR"                      -> "Erreur de configuration du conteneur";
            case "POD_EVICTED"                           -> "Pod évincé (pression disque / stockage éphémère)";
            case "REPLICA_SATURATION"                    -> "Saturation de répliques";
            case "POD_STUCK_TERMINATING"                 -> "Pod bloqué en Terminating (finalizer)";
            case "AI_UNAVAILABLE"                        -> "Service IA indisponible — escalade astreinte";
            case "DATABASE_CONNECTIVITY", "DB_CONNECTION_FAILURE", "DATABASE_POOL_SATURATION" -> "Base de données injoignable";
            case "NODE_NOT_READY"                        -> "Nœud Kubernetes indisponible";
            case "NODE_DISK_PRESSURE"                    -> "Pression disque sur le nœud";
            case "NODE_MEMORY_PRESSURE"                  -> "Pression mémoire sur le nœud";
            case "SERVICE_UNAVAILABLE"                   -> "Service injoignable";
            case "NETWORK_PARTITION"                     -> "Partition réseau totale";
            case "DNS_FAILURE"                           -> "Panne de résolution DNS";
            case "VM_DOWN"                               -> "Service VM hors ligne";
            case "GENERAL_DEGRADATION"                   -> "Dégradation générale (cause à investiguer)";
            default                                       -> type;
        };
        // Real measured qualifier so two incidents of the SAME type (and GENERAL_DEGRADATION)
        // are distinguished by their ACTUAL signal — never two identical titles for two tests.
        String qualifier = titleQualifier(prediction);
        return label + (qualifier.isEmpty() ? "" : " (" + qualifier + ")") + " — " + app;
    }

    /** Cause + remediation rationale in French, coherent with the action HELIOS actually takes. */
    private String buildCauseRationaleFr(AIPredictResponse prediction) {
        return switch (safe(prediction.getIncidentType())) {
            case "POD_CRASH_LOOP", "RESTART_LOOP" ->
                    "Cause : le conteneur redémarre en boucle (mauvaise image, commande ou configuration). "
                  + "HELIOS revient à la dernière révision saine (rollback) — un simple redémarrage recréerait le même défaut.";
            case "POD_IMAGE_PULL_BACKOFF" ->
                    "Cause : l'image ne peut pas être téléchargée (ImagePullBackOff). "
                  + "HELIOS revient à la dernière révision valide (rollback).";
            case "CPU_STRESS" ->
                    "Cause : le CPU est saturé et ralentit le service. "
                  + "HELIOS redémarre l'ensemble des pods (rollout restart) pour libérer le processus emballé.";
            case "AUTOSCALING", "SCALING_UNDER_LOAD" ->
                    "Cause : montée en charge CPU sur une application éligible à l'autoscaling. "
                  + "HELIOS ajoute de la capacité (scale up, kubectl scale) pour répartir la charge — "
                  + "pas un redémarrage. La charge est ensuite absorbée par les répliques supplémentaires.";
            case "MEMORY_STRESS", "MEMORY_LEAK", "POD_OOM_KILLED" ->
                    "Cause : conteneur tué par dépassement mémoire (OOMKilled). "
                  + "HELIOS revient à la révision précédente (rollback) pour restaurer une limite mémoire viable.";
            case "NODE_DISK_PRESSURE", "NODE_MEMORY_PRESSURE" ->
                    "Cause : pression disque — le pod a été évincé (espace éphémère saturé). Kubernetes a déjà "
                  + "recréé un pod sain ; HELIOS supprime le pod évincé résiduel (kubectl delete pod) pour "
                  + "lever la pression et nettoyer l'état.";
            case "SERVICE_UNAVAILABLE" ->
                    "Cause : la connectivité réseau est bloquée par une NetworkPolicy récente. "
                  + "HELIOS supprime la policy bloquante pour rétablir le service.";
            case "DNS_FAILURE" ->
                    "Cause : la résolution DNS est bloquée par une NetworkPolicy (port 53 fermé). "
                  + "HELIOS supprime la policy bloquante pour rétablir le DNS.";
            case "NETWORK_PARTITION" ->
                    "Cause : partition réseau totale (ingress ET egress bloqués par une NetworkPolicy). "
                  + "HELIOS supprime la policy bloquante pour rétablir la connectivité.";
            case "DEPLOYMENT_UNAVAILABLE", "AVAILABILITY_DROP" ->
                    "Cause : des répliques restent indisponibles (pod en cours d'exécution mais sonde de "
                  + "disponibilité en échec). HELIOS revient à la dernière révision saine (rollback).";
            case "HIGH_ERROR_RATE", "APPLICATION_ERROR_STORM" ->
                    "Cause : pic d'erreurs HTTP 5xx (mesuré via /metrics) provoqué par une mauvaise révision. "
                  + "HELIOS revient à la dernière révision saine (rollback) pour resservir des réponses 2xx.";
            case "HIGH_RESPONSE_TIME", "LATENCY_DEGRADATION" ->
                    "Cause : latence élevée due à une mauvaise révision. HELIOS revient à la dernière révision "
                  + "saine (rollback) pour rétablir des temps de réponse normaux.";
            case "POD_PENDING_INSUFFICIENT_RESOURCES" ->
                    "Cause : le pod ne peut pas être planifié (demande de ressources trop élevée, Unschedulable). "
                  + "HELIOS revient à la dernière révision saine (rollback) pour restaurer une demande viable.";
            case "POD_INIT_CONTAINER_FAILURE" ->
                    "Cause : le conteneur d'initialisation échoue, le conteneur principal ne démarre jamais. "
                  + "HELIOS revient à la dernière révision saine (rollback).";
            case "POD_CONFIG_ERROR" ->
                    "Cause : le conteneur ne peut pas être créé (ConfigMap/Secret manquant ou référence "
                  + "invalide — CreateContainerConfigError). HELIOS revient à la dernière révision saine (rollback).";
            case "POD_ERR_IMAGE_PULL" ->
                    "Cause : l'image du conteneur est introuvable ou non récupérable (ErrImagePull). "
                  + "HELIOS revient à la dernière révision valide (rollback).";
            case "POD_EVICTED" ->
                    "Cause : le pod a été évincé par le kubelet sous pression disque / stockage éphémère "
                  + "saturé. Kubernetes recrée déjà un pod sain ; HELIOS supprime le pod évincé résiduel "
                  + "(kubectl delete pod) pour lever la pression et nettoyer l'état.";
            case "REPLICA_SATURATION" ->
                    "Cause : trop de répliques demandées pour la capacité du cluster (plusieurs pods restent "
                  + "Pending/Unschedulable). HELIOS réduit le nombre de répliques (scale down) au nombre "
                  + "réellement planifiable — un rollback ne changerait pas le nombre de répliques.";
            case "POD_STUCK_TERMINATING" ->
                    "Cause : un pod reste bloqué en Terminating (finalizer non libéré bien au-delà de sa grace "
                  + "period). HELIOS force la suppression (retrait du finalizer) pour débloquer le pod.";
            case "AI_UNAVAILABLE" ->
                    "Le service IA est injoignable alors qu'une panne Kubernetes est détectée. HELIOS escalade "
                  + "vers l'astreinte (webhook + email) plutôt que d'appliquer une remédiation déterministe fabriquée.";
            case "DATABASE_CONNECTIVITY", "DB_CONNECTION_FAILURE", "DATABASE_POOL_SATURATION" ->
                    "Cause : l'application ne peut plus joindre sa base de données (port 5432 bloqué par une "
                  + "NetworkPolicy récente — le DNS, lui, fonctionne). HELIOS supprime la policy bloquante "
                  + "pour rétablir la connexion à la base.";
            case "NODE_NOT_READY" ->
                    "Cause : un nœud Kubernetes est indisponible. HELIOS escalade vers l'agent hôte / l'astreinte.";
            case "GENERAL_DEGRADATION" ->
                    "Dégradation détectée sans cause conteneur identifiée. HELIOS applique un redémarrage progressif (rollout restart) en première intention.";
            default ->
                    "HELIOS applique la remédiation adaptée et vérifie le rétablissement.";
        };
    }

    /** Single dominant REAL measured signal, picked by type, for the incident title. */
    private String titleQualifier(AIPredictResponse prediction) {
        Map<String, Double> f = prediction.getFeatures();
        if (f == null || f.isEmpty()) return "";
        double restarts = feat(f, "pod_restarts_total");
        double cpu      = feat(f, "cpu_usage_cores");
        double mem      = feat(f, "memory_working_set_bytes");
        double unavail  = feat(f, "unavailable_replicas");
        double nodeNR   = feat(f, "node_not_ready_count");
        double p95      = feat(f, "http_latency_p95_seconds");
        double err5xx   = feat(f, "http_5xx_ratio");
        double svc      = feat(f, "service_unreachable_detected");
        double img      = feat(f, "image_pull_backoff_detected");
        double risk     = feat(f, "overall_risk_score");
        return switch (safe(prediction.getIncidentType())) {
            case "POD_CRASH_LOOP", "RESTART_LOOP" ->
                    restarts >= 1 ? (int) restarts + " redémarrages" : "crash répété";
            case "CPU_STRESS" -> String.format(Locale.FRENCH, "%.2f cœurs", cpu);
            case "AUTOSCALING", "SCALING_UNDER_LOAD" -> String.format(Locale.FRENCH, "charge CPU %.2f cœurs — scale up", cpu);
            case "MEMORY_STRESS", "MEMORY_LEAK", "POD_OOM_KILLED" ->
                    mem >= 1e8 ? String.format(Locale.FRENCH, "%.0f Mo", mem / 1e6) : "OOM";
            case "DEPLOYMENT_UNAVAILABLE", "AVAILABILITY_DROP", "SERVICE_UNAVAILABLE" -> {
                if (img >= 1) yield "image non téléchargeable";
                if (svc >= 1) yield (unavail >= 1 ? (int) unavail : 1) + " pods injoignables (TCP)";
                yield unavail >= 1 ? (int) unavail + " replicas indisponibles" : "indisponible";
            }
            case "POD_PENDING_INSUFFICIENT_RESOURCES" -> "ressources insuffisantes (Unschedulable)";
            case "POD_INIT_CONTAINER_FAILURE" ->
                    restarts >= 1 ? "init en échec (" + (int) restarts + " tentatives)" : "init en échec";
            case "POD_CONFIG_ERROR" -> "ConfigMap/Secret manquant";
            case "POD_ERR_IMAGE_PULL" -> "image introuvable (ErrImagePull)";
            case "POD_EVICTED" -> "pod évincé (pression disque)";
            case "REPLICA_SATURATION" -> unavail >= 1 ? (int) unavail + " pods en attente" : "répliques en excès";
            case "POD_STUCK_TERMINATING" -> "finalizer non libéré";
            case "AI_UNAVAILABLE" -> "IA injoignable";
            case "DATABASE_CONNECTIVITY", "DB_CONNECTION_FAILURE", "DATABASE_POOL_SATURATION" -> "connexion DB bloquée (port 5432)";
            case "HIGH_ERROR_RATE", "APPLICATION_ERROR_STORM" ->
                    String.format(Locale.FRENCH, "%.0f%% de 5xx", err5xx * 100);
            case "HIGH_RESPONSE_TIME", "LATENCY_DEGRADATION" ->
                    String.format(Locale.FRENCH, "p95 %.2fs", p95);
            case "NODE_NOT_READY" -> (int) nodeNR + " nœud(s) hors service";
            case "NODE_DISK_PRESSURE", "NODE_MEMORY_PRESSURE" -> "pod évincé (saturation disque)";
            case "DNS_FAILURE" -> "résolution DNS bloquée (port 53)";
            case "NETWORK_PARTITION" -> "ingress + egress bloqués";
            case "POD_IMAGE_PULL_BACKOFF" -> "image non téléchargeable";
            case "GENERAL_DEGRADATION" -> {
                // No dominant trained type: still pick the strongest real signal so each test differs.
                if (restarts >= 1) yield (int) restarts + " redémarrages";
                if (svc >= 1)      yield "pods injoignables (TCP)";
                if (cpu >= 0.20)   yield String.format(Locale.FRENCH, "CPU %.2f cœurs", cpu);
                if (err5xx >= 0.10) yield String.format(Locale.FRENCH, "%.0f%% de 5xx", err5xx * 100);
                if (p95 >= 1.0)    yield String.format(Locale.FRENCH, "p95 %.2fs", p95);
                if (unavail >= 1)  yield (int) unavail + " replicas indisponibles";
                yield risk > 0 ? String.format(Locale.FRENCH, "risque %.2f", risk) : "";
            }
            default -> "";
        };
    }

    /** Full human-readable list of the REAL measured signals (different per incident/test). */
    private String describeMeasuredSignals(AIPredictResponse prediction) {
        Map<String, Double> f = prediction.getFeatures();
        if (f == null || f.isEmpty()) return "";
        List<String> parts = new ArrayList<>();
        double restarts = feat(f, "pod_restarts_total");
        double cpu      = feat(f, "cpu_usage_cores");
        double mem      = feat(f, "memory_working_set_bytes");
        double unavail  = feat(f, "unavailable_replicas");
        double nodeNR   = feat(f, "node_not_ready_count");
        double p95      = feat(f, "http_latency_p95_seconds");
        double err5xx   = feat(f, "http_5xx_ratio");
        double svc      = feat(f, "service_unreachable_detected");
        double img      = feat(f, "image_pull_backoff_detected");
        double dbPend   = feat(f, "db_pool_pending");
        double risk     = feat(f, "overall_risk_score");
        if (restarts >= 1) parts.add((int) restarts + " redémarrage(s) du pod");
        if (cpu >= 0.20)   parts.add(String.format(Locale.FRENCH, "CPU %.2f cœurs", cpu));
        if (mem >= 1e8)    parts.add(String.format(Locale.FRENCH, "mémoire %.0f Mo", mem / 1e6));
        if (img >= 1)      parts.add("image non téléchargeable (ImagePullBackOff)");
        if (svc >= 1)      parts.add("pods Ready injoignables (sonde TCP)");
        if (unavail >= 1)  parts.add((int) unavail + " replica(s) indisponible(s)");
        if (nodeNR >= 1)   parts.add((int) nodeNR + " nœud(s) NotReady");
        if (p95 >= 1.0)    parts.add(String.format(Locale.FRENCH, "latence p95 %.2fs", p95));
        if (err5xx >= 0.10) parts.add(String.format(Locale.FRENCH, "%.0f%% d'erreurs HTTP 5xx", err5xx * 100));
        if (dbPend >= 1)   parts.add((int) dbPend + " connexion(s) DB en attente");
        if (parts.isEmpty() && risk > 0) parts.add(String.format(Locale.FRENCH, "score de risque composite %.2f", risk));
        return String.join(" · ", parts);
    }

    private static double feat(Map<String, Double> f, String key) {
        if (f == null) return 0.0;
        Double v = f.get(key);
        return v == null || v.isNaN() || v.isInfinite() ? 0.0 : v;
    }

    private Incident createIncidentFromPrediction(AIPredictResponse prediction, String namespace, String app) {
        // NOTE: a RESOLUTION_FAILED incident is deliberately LEFT OPEN (status RESOLUTION_FAILED) so a
        // human engineer can review it and intervene later — HELIOS never silently CLOSES it. The
        // polling scheduler stops re-detecting for this app while an open RESOLUTION_FAILED exists
        // (see hasActiveAiIncidentFor), so we never reach this method with a dedup conflict.
        Incident incident = new Incident();
        incident.setTitle(buildIncidentTitle(prediction, app));
        incident.setDescription(buildIncidentDescription(prediction));
        // Explanation = real measured "Constat mesuré" + a French rationale that matches the ACTION
        // HELIOS actually takes. We do NOT reuse the AI's raw decision_explanation here: after a
        // backend override (crash-loop, network, image-pull...) it still carries the AI's original
        // wording ("Recommended action: INVESTIGATE...") which contradicts the real action. The
        // deterministic per-type rationale is always coherent and never mentions INVESTIGATE.
        String signals = describeMeasuredSignals(prediction);
        // Résolution PROFESSIONNELLE de référence issue du catalogue HELIOS des 120 types — calculée sur
        // le TYPE FINAL de l'incident (donc cohérente avec le type affiché, même après un garde-fou
        // force*), avec repli sur la résolution fournie par l'IA. Chaque incident porte ainsi son
        // explication + sa résolution catalogue. L'action reste PROPOSÉE par l'IA, EXÉCUTÉE par Camunda.
        String catalogPro = IncidentCatalog.resolutionFor(prediction.getIncidentType());
        if (catalogPro == null || catalogPro.isBlank()) {
            catalogPro = safe(prediction.getCatalogResolution());
        }
        incident.setExplanation(
                (signals.isEmpty() ? "" : "Constat mesuré : " + signals + ". ")
                        + buildCauseRationaleFr(prediction)
                        + (catalogPro.isEmpty() ? "" : " Résolution (catalogue HELIOS) : " + catalogPro));
        incident.setSeverity(mapSeverity(prediction.getSeverity()));
        incident.setStatus(IncidentStatus.NEW);
        incident.setSource(IncidentSource.AI);
        incident.setIncidentType(prediction.getIncidentType());
        incident.setNamespace(namespace);
        incident.setApp(app);
        return incidentRepository.save(incident);
    }

    private String buildIncidentDescription(AIPredictResponse prediction) {
        // Clean, structured at-a-glance summary. The full cause + Why prose lives in the
        // incident's explanation field ("Explication IA"), so we do NOT repeat it here.
        double conf = prediction.getConfidence() != null ? prediction.getConfidence() : 0.0;
        return String.format(
                "Type: %s | Severity: %s (confidence %.0f%%) | Recommended action: %s | Mode: %s",
                safe(prediction.getIncidentType()),
                safe(prediction.getSeverity()),
                conf * 100.0,
                safe(prediction.getRecommendedAction()),
                safe(prediction.getExecutionMode())
        );
    }

    private String buildDetectionComment(AIPredictResponse prediction) {
        AIPredictEvidence evidence = prediction.getEvidence();
        String scores = evidence == null
                ? "n/a"
                : String.format(Locale.FRENCH,
                "log=%.2f metric=%.2f rule=%.2f model=%s",
                valueOrZero(evidence.getLogScore()),
                valueOrZero(evidence.getMetricScore()),
                valueOrZero(evidence.getRuleScore()),
                evidence.getModelScore() == null ? "n/a" : String.format(Locale.FRENCH, "%.2f", evidence.getModelScore()));

        String signals = describeMeasuredSignals(prediction);
        double conf = prediction.getConfidence() != null ? prediction.getConfidence() : 0.0;
        // French, human-readable AND specific: the measured signals differ per test, so the very
        // first trace step is never identical between two different incidents.
        return String.format(Locale.FRENCH,
                "Détection IA : %s (sévérité %s, confiance %.0f%%). Signaux mesurés : %s. "
                        + "Action recommandée : %s [%s]. Scores du modèle : %s.",
                safe(prediction.getIncidentType()),
                safe(prediction.getSeverity()),
                conf * 100.0,
                signals.isEmpty() ? "score composite uniquement" : signals,
                safe(prediction.getRecommendedAction()),
                safe(prediction.getExecutionMode()),
                scores);
    }

    private String buildActionDescription(AIPredictResponse prediction, RemediationPlan plan) {
        return String.format(
                "AI recommended action: %s | executionMode=%s | chosenPlan=%s | explanation=%s | %s",
                safe(prediction.getRecommendedAction()),
                safe(prediction.getExecutionMode()),
                safe(prediction.getChosenPlanId()),
                safe(prediction.getDecisionExplanation()),
                plan.rationale()
        );
    }

    private double valueOrZero(Double value) {
        return value == null ? 0.0 : value;
    }

    private IncidentSeverity mapSeverity(String severity) {
        if (severity == null || severity.isBlank()) {
            return IncidentSeverity.MEDIUM;
        }
        try {
            return IncidentSeverity.valueOf(severity.toUpperCase());
        } catch (Exception ex) {
            return IncidentSeverity.MEDIUM;
        }
    }

    private String safe(String value) {
        return value == null ? "unknown" : value;
    }

    @Transactional
    public boolean autoResolveIfCleared(String namespace, String app, int windowMinutes) {
        Optional<Incident> openIncidentOpt = incidentRepository
                .findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(
                        ACTIVE_STATUSES, IncidentSource.AI, namespace, app);
        if (openIncidentOpt.isEmpty()) {
            return false;
        }

        // includePodKillSignal=false: an active incident's pod was likely just killed/restarted by
        // remediation; the historical pod-kill signal must not block auto-resolution. ImagePullBackOff
        // (a real ongoing failure on the new pod) is still detected.
        java.util.Map<String, Double> resolveOverrides =
                kubernetesSignalEnrichmentService.collectFeatureOverrides(namespace, app, false);
        boolean imagePullBackOff = resolveOverrides.remove("image_pull_backoff_detected") != null;
        boolean stillUnreachable = resolveOverrides.remove("service_unreachable_detected") != null;

        if (stillUnreachable) {
            // Network partition persists (pod still unreachable) — do NOT auto-resolve.
            log.debug("Auto-resolve check: pod still unreachable for {}/{}", namespace, app);
            return false;
        }

        if (imagePullBackOff) {
            // The active incident was created for a different condition (e.g. GENERAL_DEGRADATION).
            // Force-close it so the next poll cycle can create a fresh incident with ROLLBACK_DEPLOYMENT.
            Incident staleIncident = openIncidentOpt.get();
            staleIncident.setStatus(IncidentStatus.RESOLVED);
            historyService.record(staleIncident, IncidentStatus.RESOLVED,
                    "Auto-closed: ImagePullBackOff detected on new pod — next poll will trigger ROLLBACK_DEPLOYMENT");
            incidentRepository.save(staleIncident);
            log.info("Auto-closed stale incident id={} for {}/{} — ImagePullBackOff on new pod, rollback incoming",
                    staleIncident.getId(), namespace, app);
            return true;
        }

        // GENUINE-RESOLUTION GATE: never auto-resolve an incident whose Camunda remediation is still
        // executing. Transient faults (a CPU hog ending at 300s, intermittent 5xx / latency) make the AI
        // signal clear within seconds; if we resolved here we would abandon the real remediation action
        // in PENDING — the incident would show "resolved" with NO kubectl command in its trace (a fake
        // resolution). We let the execute worker run the real command (logged as "$ kubectl ..."), mark
        // the action COMPLETED, and let the verify step own the resolution. The gate releases as soon as
        // the action reaches a terminal state (COMPLETED/FAILED).
        Incident openIncident = openIncidentOpt.get();
        Long openIncidentId = openIncident.getId();
        // Safety bound: the gate below only applies to FRESH incidents. If Camunda is genuinely stuck
        // (e.g. an orphaned workflow row left RUNNING by a demo reset), we must not block resolution
        // forever — after this window, fall through and let auto-resolve be the last resort.
        boolean withinCamundaWindow = openIncident.getCreatedAt() != null
                && openIncident.getCreatedAt().isAfter(java.time.LocalDateTime.now().minusMinutes(8));
        if (withinCamundaWindow) {
            Optional<IncidentAction> latestAction =
                    incidentActionRepository.findTopByIncidentIdOrderByCreatedAtDesc(openIncidentId);
            if (latestAction.isPresent()) {
                ActionStatus actionStatus = latestAction.get().getStatus();
                if (actionStatus == ActionStatus.PENDING || actionStatus == ActionStatus.IN_PROGRESS) {
                    log.debug("Auto-resolve deferred for {}/{}: remediation action {} still {} — "
                            + "letting Camunda execute the real command first",
                            namespace, app, latestAction.get().getType(), actionStatus);
                    return false;
                }
            }
            // ...and even once the action is COMPLETED, let the Camunda verify step (delay 25s + retries)
            // own the resolution rather than racing it with this 30s poll. Otherwise the incident would read
            // "Auto-resolved: condition cleared" instead of "Post-remediation verification succeeded" — the
            // command ran, but the trace would understate that HELIOS itself drove the fix. Defer while the
            // most recent workflow is still non-terminal.
            List<WorkflowStatus> wfStatuses = workflowRepository.findLatestStatusByIncidentId(openIncidentId);
            if (!wfStatuses.isEmpty()) {
                WorkflowStatus wfStatus = wfStatuses.get(0);
                if (wfStatus != WorkflowStatus.COMPLETED && wfStatus != WorkflowStatus.FAILED
                        && wfStatus != WorkflowStatus.CANCELLED) {
                    log.debug("Auto-resolve deferred for {}/{}: Camunda workflow still {} — "
                            + "verify step will conclude the resolution", namespace, app, wfStatus);
                    return false;
                }
            }
        }

        AIPredictRequest predictRequest = new AIPredictRequest();
        predictRequest.setNamespace(namespace);
        predictRequest.setApp(app);
        predictRequest.setWindowMinutes(windowMinutes);
        predictRequest.setSkipLlm(true);
        predictRequest.setFeatureOverrides(resolveOverrides);

        AIPredictResponse prediction = aiOpsClientService.predict(predictRequest);

        if (prediction.isIncident()) {
            log.debug("Auto-resolve check: condition still present for {}/{}", namespace, app);
            return false;
        }

        Incident incident = openIncidentOpt.get();
        incident.setStatus(IncidentStatus.RESOLVED);
        historyService.record(incident, IncidentStatus.RESOLVED,
                "Auto-resolved: AI monitoring detects condition has cleared");
        incidentRepository.save(incident);
        log.info("Auto-resolved incident id={} for {}/{} — condition cleared", incident.getId(), namespace, app);
        try {
            notificationService.notifyIncidentResolved(incident);
        } catch (Exception ex) {
            log.warn("Incident-resolved notification failed: {}", ex.getMessage());
        }
        return true;
    }

    @Transactional
    public void executeRecommendation(Long incidentId) {
        Incident incident = incidentRepository.findById(incidentId)
                .orElseThrow(() -> new IncidentNotFoundException(incidentId));

        // Garde : ne pas (re)executer un incident deja traite/en cours.
        if (incident.getStatus() == IncidentStatus.RESOLVED
                || incident.getStatus() == IncidentStatus.CLOSED
                || incident.getStatus() == IncidentStatus.ARCHIVED
                || incident.getStatus() == IncidentStatus.IN_PROGRESS) {
            log.info("executeRecommendation: incident {} deja {} - ignore", incidentId, incident.getStatus());
            return;
        }

        String namespace = incident.getNamespace() != null ? incident.getNamespace() : "pfe";
        String app = incident.getApp() != null ? incident.getApp() : "backend";
        String rawAction = parseFieldFromDescription(incident.getDescription(), "action");
        ActionType actionType = remediationPolicyService.mapActionType(rawAction, incident.getIncidentType());
        if (actionType == null || actionType == ActionType.INVESTIGATION || actionType == ActionType.ESCALATION) {
            // Incident noeud -> RESTART_VM (rallumer la VM via l'agent hote) ; sinon RESTART_POD.
            boolean isNode = (incident.getIncidentType() != null
                        && incident.getIncidentType().toUpperCase(java.util.Locale.ROOT).contains("NODE"))
                    || (app != null && app.toLowerCase(java.util.Locale.ROOT).startsWith("k8s-worker"));
            actionType = isNode ? ActionType.RESTART_VM : ActionType.RESTART_POD;
        }

        // Reutiliser l'action PENDING deja recommandee (evite un doublon) ; sinon en creer
        // une nouvelle en mode MANUEL (c'est l'operateur qui declenche via "Demarrer").
        var pendingPage = incidentActionRepository.findByIncidentIdAndStatus(
                incident.getId(), ActionStatus.PENDING, PageRequest.of(0, 1));
        IncidentAction action;
        RemediationTargetKind targetKind;
        if (!pendingPage.isEmpty()) {
            action = pendingPage.getContent().get(0);
            // Si le type existant est EXECUTABLE on le garde ; sinon (INVESTIGATION/ESCALATION =
            // simples conseils, no-op cote executeur) on impose l'action calculee
            // (RESTART_POD/RESTART_VM) pour une VRAIE execution, et on aligne le type de l'action.
            ActionType existing = action.getType();
            if (existing != null && existing != ActionType.INVESTIGATION && existing != ActionType.ESCALATION) {
                actionType = existing;
            } else {
                action.setType(actionType);
            }
            if (action.getTargetNamespace() != null) { namespace = action.getTargetNamespace(); }
            if (action.getTargetName() != null) { app = action.getTargetName(); }
            targetKind = (actionType == ActionType.RESTART_POD
                    || actionType == ActionType.DELETE_CRASHLOOPING_PODS)
                    ? RemediationTargetKind.POD : RemediationTargetKind.DEPLOYMENT;
        } else {
            targetKind = (actionType == ActionType.RESTART_POD
                    || actionType == ActionType.DELETE_CRASHLOOPING_PODS)
                    ? RemediationTargetKind.POD : RemediationTargetKind.DEPLOYMENT;
            action = actionService.createAction(
                    incident, actionType,
                    "Execution declenchee par l'operateur (Demarrer): " + safe(rawAction),
                    "HUMAN-OPERATOR",
                    namespace, targetKind, app, AutomationMode.MANUAL
            );
        }

        WorkflowInstance workflow = workflowService.startWorkflow(
                incident, "Incident-Remediation-Workflow", namespace, app);

        // approvalRequired=false + autoExecutable=true : le clic "Demarrer" de l'operateur
        // VAUT approbation -> Camunda execute directement, sans Human Task d'attente.
        // Le mode de l'action reste celui d'origine (MANUEL = declenche par l'operateur).
        RemediationPlan plan = new RemediationPlan(
                actionType,
                action.getAutomationMode() != null ? action.getAutomationMode() : AutomationMode.MANUAL,
                false, true, false, false, false,
                namespace, targetKind, app, "Execution directe demandee par l'operateur (Demarrer)"
        );

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType(incident.getIncidentType());
        prediction.setSeverity(incident.getSeverity() != null ? incident.getSeverity().name() : "MEDIUM");
        prediction.setRecommendedAction(rawAction);
        prediction.setFinalExecutableAction(rawAction);
        prediction.setExecutionMode("AUTO");
        prediction.setConfidence(1.0);
        prediction.setDecisionExplanation(incident.getExplanation());

        // Camunda execute la remediation directement (le clic Demarrer = approbation).
        camundaWorkflowService.startWorkflow(workflow, action, plan, prediction);

        incident.setStatus(IncidentStatus.IN_PROGRESS);
        workflowService.updateOrchestrationState(workflow, WorkflowOrchestrationState.EXECUTION_PENDING,
                "Execution directe declenchee par l'operateur");
        historyService.record(incident, IncidentStatus.IN_PROGRESS,
                "Remediation declenchee par l'operateur (Demarrer) - Camunda execute directement");
        incidentRepository.save(incident);

        log.info("executeRecommendation: incident={} action={} workflow={}", incidentId, actionType, workflow.getId());
    }

    private String parseFieldFromDescription(String description, String field) {
        if (description == null || field == null) return null;
        String marker = "| " + field + "=";
        int start = description.indexOf(marker);
        if (start < 0) return null;
        start += marker.length();
        int end = description.indexOf(" |", start);
        return end < 0 ? description.substring(start).trim() : description.substring(start, end).trim();
    }
}
