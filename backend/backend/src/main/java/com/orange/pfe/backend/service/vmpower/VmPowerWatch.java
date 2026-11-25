package com.orange.pfe.backend.service.vmpower;

import com.orange.pfe.backend.config.VmPowerProperties;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.exception.ActionException;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.ActionService;
import com.orange.pfe.backend.service.CamundaWorkflowService;
import com.orange.pfe.backend.service.IncidentHistoryService;
import com.orange.pfe.backend.service.LeaderElectionService;
import com.orange.pfe.backend.service.NotificationService;
import com.orange.pfe.backend.service.IncidentCardService;
import com.orange.pfe.backend.service.WorkflowService;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.orange.pfe.backend.service.remediation.NodeReadinessObservation;
import com.orange.pfe.backend.service.remediation.RemediationExecutor;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.Decision;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.Entry;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.PowerState;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.Request;
import com.orange.pfe.backend.service.vmpower.VmRecoveryVerifier.PassResult;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.VmState;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * VMDOWN-POLICY-20261105 — surveillance de la PUISSANCE des VM et décision de redémarrage.
 *
 * <p>C'est le SEUL composant qui décide d'un redémarrage. Il ne contacte JAMAIS Prometheus, le détecteur, le Bridge
 * ni .160 pour décider : sa détection repose sur (1) l'état d'alimentation PROUVÉ par {@code vmrun list} (via le
 * watchdog) et (2) des sondes réseau parallèles à délai borné ({@link VmProbes}). Chaîne unique :
 * <b>backend → watchdog hôte → {@code vmrun start} → preuves de santé → résultat au backend</b>.
 *
 * <ul>
 *   <li>AUTO, route CAMUNDA ({@code k8s-worker1}, {@code k8s-worker2}, {@code k8s-lb2} et, depuis la consigne du 2026-11-17,
 *       {@code k8s-master} — plan de contrôle, même flux que les workers) : un redémarrage n'est demandé
 *       que si {@link VmPowerPolicy#authorize} l'autorise (VM éteinte confirmée, sondes en échec, hors maintenance,
 *       hors épisode RESOLUTION_FAILED ouvert, hors cooldown, aucune action pour cet incident) ET que la VM est
 *       activée ({@code enabled && !dryRun && activeVms}). Exécution par la chaîne Camunda existante
 *       (incident-remediation → execute-remediation → watchdog hôte). Sinon : MODE OMBRE — rien n'est demandé.
 *       Depuis le 2026-11-21, si la détection Kubernetes n'a pas créé l'incident NODE_NOT_READY d'un nœud PROUVÉ éteint,
 *       le backend le crée lui-même (voir {@code workerFlow}).</li>
 *   <li>DETECT_ONLY ({@code k8s-monitoring}, .160 — consigne finale du 2026-11-06 : VM de supervision, donc PAS
 *       d'auto-rallumage) : incident VM_DOWN créé, interface seulement (aucun e-mail), rappel d'historique espacé ; AUCUNE
 *       action, AUCUN workflow Camunda, AUCUN appel du watchdog, même si le nom figurait dans {@code activeVms}.
 *       Clôture par preuves de récupération quand l'administrateur l'a rallumée.</li>
 *   <li>Route BACKEND_DIRECT (DORMANTE : aucune VM de la table ne l'utilise ; validée par un essai réel sur .160 le
 *       2026-11-06 13:28 avant le retour de .160 en détection seule) : mêmes autorisations et MÊMES verrous que la route
 *       Camunda, mais le backend exécute lui-même (action RESTART_VM persistée, UN SEUL POST au watchdog, jamais rejoué) et
 *       vérifie la récupération par preuves, sans workflow Camunda ni e-mail.</li>
 *   <li>DENY_RESTART : mode conservé (aucune VM de la table ne l'utilise depuis le 2026-11-17, {@code k8s-master} est AUTO) :
 *       ignoré ici (la détection NODE_NOT_READY existante est conservée) ; JAMAIS une cible.</li>
 * </ul>
 *
 * <p>État en mémoire (leader unique) : un changement de leader repart de zéro et doit reconfirmer l'arrêt
 * (≥ 2 observations espacées d'au moins 30 s) avant toute décision — fail-safe.
 */
@Service
public class VmPowerWatch {

    private static final Logger log = LoggerFactory.getLogger(VmPowerWatch.class);

    private static final List<IncidentStatus> CLOSED_STATUSES =
            List.of(IncidentStatus.RESOLVED, IncidentStatus.CLOSED, IncidentStatus.ARCHIVED);
    /** Cycles consécutifs « VM allumée mais muette » avant d'ouvrir un VM_UNREACHABLE (détection seule). */
    static final int SILENT_CYCLES_BEFORE_UNREACHABLE = 4;
    static final String UNREACHABLE_TYPE = "VM_UNREACHABLE";
    private static final String SYSTEM_USER = "AI-SYSTEM";

    private final VmPowerProperties props;
    private final WatchdogClient watchdog;
    private final VmProbes probes;
    private final IncidentRepository incidents;
    private final IncidentActionRepository actions;
    private final IncidentHistoryService history;
    private final ActionService actionService;
    private final WorkflowService workflowService;
    private final CamundaWorkflowService camunda;
    private final NotificationService notifications;
    private final LeaderElectionService leader;
    private final IncidentCardService card;
    private final VmRecoveryVerifier verifier;
    private final RemediationExecutor nodeReadiness;

    private final Map<Long, Recovery> recoveries = new ConcurrentHashMap<>();
    private final Map<String, Track> tracks = new ConcurrentHashMap<>();
    private final Map<String, Long> lastNote = new ConcurrentHashMap<>();

    public VmPowerWatch(VmPowerProperties props, WatchdogClient watchdog, VmProbes probes,
                        IncidentRepository incidents, IncidentActionRepository actions,
                        IncidentHistoryService history, ActionService actionService,
                        WorkflowService workflowService, CamundaWorkflowService camunda,
                        NotificationService notifications, LeaderElectionService leader,
                        IncidentCardService card, VmRecoveryVerifier verifier, RemediationExecutor nodeReadiness) {
        this.props = props;
        this.watchdog = watchdog;
        this.probes = probes;
        this.incidents = incidents;
        this.actions = actions;
        this.history = history;
        this.actionService = actionService;
        this.workflowService = workflowService;
        this.camunda = camunda;
        this.notifications = notifications;
        this.leader = leader;
        this.card = card;
        this.verifier = verifier;
        this.nodeReadiness = nodeReadiness;
    }

    /** Observations consécutives d'une VM (état + sondes). */
    static final class Track {
        VmState lastState;
        long fetchedAtMs;
        boolean watchdogReachable;
        int offCount;
        long firstOffMs;
        long lastOffMs;
        int allProbesFailedCycles;
        int silentRunningCycles;
        boolean lastAllProbesFailed;

        void update(VmState state, boolean reachable, VmProbes.Liveness live, long nowMs) {
            lastState = state;
            fetchedAtMs = nowMs;
            watchdogReachable = reachable;
            PowerState ps = state == null ? PowerState.UNKNOWN : state.powerState();
            boolean fresh = state != null && state.evidence() != null && state.evidence().isFresh();
            if (ps == PowerState.POWERED_OFF && fresh) {
                if (offCount == 0) {
                    firstOffMs = nowMs;
                }
                offCount++;
                lastOffMs = nowMs;
            } else {
                offCount = 0;  // RUNNING, UNKNOWN ou preuve périmée : la série de confirmations est rompue
            }
            lastAllProbesFailed = live != null && live.allFailed();
            allProbesFailedCycles = lastAllProbesFailed ? allProbesFailedCycles + 1 : 0;
            silentRunningCycles = (ps == PowerState.RUNNING && lastAllProbesFailed) ? silentRunningCycles + 1 : 0;
        }

        long offSpanMs() {
            return offCount > 0 ? lastOffMs - firstOffMs : 0;
        }

        boolean confirmedOff() {
            return offCount >= VmPowerPolicy.CONFIRMATION_MIN_OBSERVATIONS
                    && offSpanMs() >= VmPowerPolicy.CONFIRMATION_MIN_SPAN_MS
                    && lastAllProbesFailed;
        }
    }

    /** Série de passes de récupération d'un incident (en mémoire ; la preuve, elle, est en base). */
    static final class Recovery {
        int sample;
        int streak;
    }

    // ── Planification ────────────────────────────────────────────────────────────────────────────

    @Scheduled(fixedDelayString = "${helios.vmpower.cycle-seconds:15}000",
            initialDelayString = "${helios.vmpower.initial-delay-seconds:30}000")
    public void cycle() {
        if (!props.isEnabled()) {
            return;
        }
        if (!leader.isLeader()) {
            return;
        }
        try {
            runCycle(System.currentTimeMillis());
        } catch (Exception ex) {
            log.error("VmPowerWatch: cycle en erreur ({}) — aucune décision prise", ex.toString());
        }
    }

    /** Un cycle complet ; retourne le journal des événements (utilisé par les tests et le diagnostic). */
    public List<String> runCycle(long nowMs) {
        List<String> events = new ArrayList<>();
        WatchdogClient.Result<Map<String, VmState>> states = watchdog.states();
        if (!states.ok()) {
            events.add("WATCHDOG_UNREACHABLE");
            log.warn("VmPowerWatch: watchdog hôte injoignable ({}) — état d'alimentation non prouvé, aucun démarrage",
                    states.error());
        }
        Map<String, VmProbes.Liveness> live = probes.livenessAll(VmPowerPolicy.entries());
        for (Entry e : VmPowerPolicy.entries()) {
            VmState s = states.ok() ? states.value().get(e.name()) : null;
            tracks.computeIfAbsent(e.name(), k -> new Track()).update(s, states.ok(), live.get(e.name()), nowMs);
        }
        for (Entry e : VmPowerPolicy.entries()) {
            Track t = tracks.get(e.name());
            try {
                switch (e.vmClass()) {
                    // .153 (plan de contrôle) : AUTO depuis le 2026-11-17 — même flux que les workers (incident NODE_NOT_READY de la
                    // détection Kubernetes, ou créé ici dès l'arrêt confirmé depuis le 2026-11-21 ; décision de power-on ici) ; un mode
                    // non AUTO (DENY_RESTART conservé) est refusé par la politique.
                    case K8S_WORKER, K8S_CONTROL_PLANE -> workerFlow(e, t, nowMs, events);
                    case INFRA_LB, INFRA_MONITORING -> infraAutoFlow(e, t, nowMs, events);
                }
            } catch (Exception ex) {
                events.add("ERROR:" + e.name() + ":" + ex.getClass().getSimpleName());
                log.error("VmPowerWatch: erreur sur {} ({})", e.name(), ex.toString());
            }
        }
        Map<String, VmState> known = states.ok() ? states.value() : null;
        for (Entry e : VmPowerPolicy.entries()) {
            try {
                closeIfRecovered(e, known, nowMs, events);
            } catch (Exception ex) {
                events.add("ERROR_CLOSE:" + e.name() + ":" + ex.getClass().getSimpleName());
                log.error("VmPowerWatch: clôture par preuves impossible pour {} ({})", e.name(), ex.toString());
            }
        }
        return events;
    }

    // ── Flux par classe de VM ────────────────────────────────────────────────────────────────────

    /**
     * Workers et plan de contrôle : l'incident NODE_NOT_READY vient en principe de la détection Kubernetes ; ici on décide
     * du power-on.
     *
     * <p>2026-11-21 (« rien ne se passe » lors d'un arrêt de .158) : si Kubernetes n'a PAS (encore) créé l'incident alors que
     * la VM est PROUVÉE éteinte (vmrun list ×2 espacés d'au moins 30 s ET toutes les sondes réseau en échec), le backend le
     * crée LUI-MÊME, comme pour .162, au lieu d'attendre. Sinon un nœud qui portait le bail du controller-manager (60 s) puis
     * la période de grâce des nœuds (40 s) restait invisible ≈ 2 min et l'arrêt n'était jamais traité s'il durait moins.
     * Déduplication : recherche préalable + index unique {@code ux_incident_open_dedup} (namespace, app, type) ; la détection
     * Kubernetes, si elle passe plus tard, voit l'incident ouvert et n'en crée pas. Jamais pour un mode non AUTO.
     */
    private void workerFlow(Entry e, Track t, long nowMs, List<String> events) {
        Optional<Incident> found = latestNotClosed(e, e.incidentType());
        if (found.isEmpty()) {
            if (e.mode() != VmPowerPolicy.Mode.AUTO || !t.confirmedOff()) {
                return;
            }
            Incident created = createIncident(e, e.incidentType(), IncidentSeverity.CRITICAL,
                    "Infrastructure DOWN: " + e.incidentNamespace() + "/" + e.name(),
                    "VM '" + e.name() + "' (" + e.ip() + ") éteinte : arrêt confirmé par vmrun list (" + t.offCount
                            + " observations sur " + (t.offSpanMs() / 1000) + " s) et toutes les sondes réseau en échec. "
                            + "Incident créé par la surveillance de puissance sans attendre la détection Kubernetes (NotReady).",
                    false);
            if (created == null) {
                return;   // doublon : un incident est déjà ouvert pour ce nœud (index unique)
            }
            events.add("INCIDENT_CREATED:" + e.name());
            considerLaunch(e, t, created, nowMs, events);
            return;
        }
        Incident i = found.get();
        if (i.getStatus() == IncidentStatus.RESOLUTION_FAILED) {
            episodeReminder(e, t, i, nowMs, events);
            return;
        }
        if (i.getStatus() == IncidentStatus.IN_PROGRESS || i.getStatus() == IncidentStatus.WAITING_FOR_APPROVAL) {
            return;  // une remédiation est déjà en cours pour cet incident
        }
        considerLaunch(e, t, i, nowMs, events);
    }

    /**
     * VM d'infrastructure (.162 AUTO route CAMUNDA ; .160 DETECT_ONLY) : détection INDÉPENDANTE de .160 et de
     * NODE_NOT_READY — incident VM_DOWN / infra / nom de la VM. Les canaux de notification suivent la table de politique
     * (.160 : interface seulement, jamais d'e-mail). Une VM {@link VmPowerPolicy.Mode#DETECT_ONLY} (.160) a son incident
     * créé et rappelé dans l'historique, mais AUCUNE demande de démarrage n'est jamais préparée : ni action, ni workflow
     * Camunda, ni appel du watchdog (même si son nom figurait dans {@code activeVms}).
     */
    private void infraAutoFlow(Entry e, Track t, long nowMs, List<String> events) {
        boolean uiOnly = e.notifications() == VmPowerPolicy.Notifications.UI_ONLY;
        boolean detectOnly = e.mode() == VmPowerPolicy.Mode.DETECT_ONLY;
        Optional<Incident> found = latestNotClosed(e, e.incidentType());
        if (found.isPresent()) {
            Incident i = found.get();
            if (i.getStatus() == IncidentStatus.RESOLUTION_FAILED) {
                episodeReminder(e, t, i, nowMs, events);
            } else if (i.getStatus() == IncidentStatus.NEW || i.getStatus() == IncidentStatus.OPEN) {
                if (detectOnly) {
                    detectionOnlyReminder(e, t, i, nowMs, events);
                } else {
                    considerLaunch(e, t, i, nowMs, events);
                }
            }
            // IN_PROGRESS : la chaîne Camunda (route CAMUNDA) ou la vérification directe (route BACKEND_DIRECT,
            // voir closeIfRecovered) porte la suite.
            return;
        }
        if (t.confirmedOff()) {
            Incident i = createIncident(e, e.incidentType(),
                    uiOnly ? IncidentSeverity.HIGH : IncidentSeverity.CRITICAL,
                    "Infrastructure DOWN: " + e.incidentNamespace() + "/" + e.name(),
                    "VM '" + e.name() + "' (" + e.ip() + ") éteinte : arrêt confirmé par vmrun list (" + t.offCount
                            + " observations sur " + (t.offSpanMs() / 1000) + " s) et toutes les sondes réseau en échec."
                            + (detectOnly ? " DÉTECTION SEULE : aucun rallumage automatique pour cette VM (VM de "
                            + "supervision) — rallumage par l'administrateur ; interface seulement, aucun e-mail, "
                            + "aucune escalade Camunda."
                            : uiOnly ? " Rallumage automatique par le backend (chemin direct) : interface seulement, "
                            + "aucun e-mail, aucune escalade Camunda." : ""),
                    uiOnly);
            if (i != null) {
                events.add("INCIDENT_CREATED:" + e.name());
                if (detectOnly) {
                    detectionOnlyReminder(e, t, i, nowMs, events);
                } else {
                    considerLaunch(e, t, i, nowMs, events);
                }
            }
        } else if (t.silentRunningCycles >= SILENT_CYCLES_BEFORE_UNREACHABLE) {
            openUnreachable(e, t, uiOnly, events);
        }
    }

    /**
     * VM en détection seule (.160) : le rappel d'historique est espacé (une ligne par {@link #reminderMs()}), jamais un
     * e-mail, jamais une action. La clôture, elle, vient des preuves de récupération ({@link #closeIfRecovered}).
     */
    private void detectionOnlyReminder(Entry e, Track t, Incident i, long nowMs, List<String> events) {
        if (due("detectonly:" + i.getId(), nowMs, reminderMs())) {
            history.record(i, i.getStatus(), "DÉTECTION SEULE — " + e.name() + " est "
                    + (t.confirmedOff() ? "éteinte (confirmée)" : "signalée en panne")
                    + " ; politique VM_DOWN : aucun rallumage automatique pour cette VM (aucune action, aucun e-mail, "
                    + "aucune escalade Camunda, aucun appel du watchdog). Rallumage par l'administrateur ; clôture "
                    + "uniquement après deux passes de preuves de récupération.");
            events.add("DETECT_ONLY:" + e.name());
        }
    }

    // ── Décision ─────────────────────────────────────────────────────────────────────────────────

    private void considerLaunch(Entry e, Track t, Incident i, long nowMs, List<String> events) {
        Decision d = VmPowerPolicy.authorize(facts(e, t, i, nowMs));
        if (!d.allowed()) {
            if (due("refusal:" + i.getId() + ":" + d.refusalCodes(), nowMs, reminderMs())) {
                history.record(i, i.getStatus(), "Redémarrage de " + e.name() + " NON autorisé pour l'instant : "
                        + d.refusalCodes());
            }
            events.add("REFUSED:" + e.name() + ":" + d.refusalCodes());
            return;
        }
        boolean real = props.isEnabled() && !props.isDryRun() && props.getActiveVms().contains(e.name());
        if (!real) {
            if (due("shadow:" + i.getId(), nowMs, reminderMs())) {
                history.record(i, i.getStatus(), "MODE OMBRE — redémarrage de " + e.name() + " AUTORISÉ par la politique "
                        + "VM_DOWN (VM éteinte confirmée, sondes en échec, hors maintenance/cooldown/épisode) mais NON "
                        + "demandé (dry-run=" + props.isDryRun() + ", activée=" + props.getActiveVms().contains(e.name())
                        + ").");
            }
            events.add("SHADOW_WOULD_RESTART:" + e.name());
            return;
        }
        if (e.route() == VmPowerPolicy.Route.BACKEND_DIRECT) {
            launchDirect(e, i, nowMs, events);
        } else {
            launch(e, i, events);
        }
    }

    /** Faits de l'autorisation : tout fait non établi est {@code null} (donc refusé, fail-closed). */
    private Request facts(Entry e, Track t, Incident i, long nowMs) {
        VmState s = t.lastState;
        Long age = (s == null || s.evidence() == null) ? null : s.evidence().ageMs() + Math.max(0, nowMs - t.fetchedAtMs);
        Request.Builder b = Request.builder()
                .vm(e.name())
                .incident(i.getNamespace(), i.getApp(), i.getIncidentType())
                .powerState(s == null ? PowerState.UNKNOWN : s.powerState())
                .evidenceAgeMs(age)
                .poweredOff(t.offCount, t.offSpanMs())
                .probesFailing(t.lastAllProbesFailed)
                .maintenance(s == null || s.watchdog() == null ? null : s.watchdog().maintenance());
        b.failedEpisodeOpen(otherFailedEpisode(e, i));
        b.msSinceLastRestartAction(msSinceLastRestart(e, nowMs));
        b.restartActionsForIncident((int) actions.countByIncidentIdAndType(i.getId(), ActionType.RESTART_VM));
        return b.build();
    }

    private boolean otherFailedEpisode(Entry e, Incident current) {
        return incidents.findByStatusAndNamespaceAndApp(IncidentStatus.RESOLUTION_FAILED, e.incidentNamespace(),
                        e.incidentApp()).stream()
                .anyMatch(x -> !x.getId().equals(current.getId()) && e.incidentType().equals(x.getIncidentType()));
    }

    private long msSinceLastRestart(Entry e, long nowMs) {
        // Tout est dérivé de nowMs (horloge du cycle) : jamais de mélange avec LocalDateTime.now().
        // Fenêtre de recherche = MAX(cooldown de la politique, budget de preuves du rallumage DIRECT). Elle valait le cooldown seul quand
        // celui-ci était de 30 min (> budget de 6 min) ; à 5 min (2026-11-18) elle serait plus COURTE que le budget : une action de
        // 5 min 30 s serait vue comme « aucune action » (NEVER) et ferait échouer le budget du rallumage direct trop tôt.
        long windowMs = Math.max(VmPowerPolicy.RESTART_COOLDOWN.toMillis(),
                Math.max(1, props.getDirectVerifyBudgetMinutes()) * 60_000L);
        LocalDateTime cutoff = LocalDateTime.ofInstant(Instant.ofEpochMilli(nowMs - windowMs), ZoneId.systemDefault());
        List<IncidentAction> recent = actions.findRecentActionsForTarget(
                e.incidentNamespace(), e.name(), ActionType.RESTART_VM, cutoff);
        if (recent == null || recent.isEmpty() || recent.get(0).getCreatedAt() == null) {
            return VmPowerPolicy.NEVER;
        }
        long createdMs = recent.get(0).getCreatedAt().atZone(ZoneId.systemDefault()).toInstant().toEpochMilli();
        return Math.max(0, nowMs - createdMs);
    }

    // ── Exécution (chaîne Camunda existante) ─────────────────────────────────────────────────────

    private void launch(Entry e, Incident i, List<String> events) {
        if (!camunda.isReachable()) {
            events.add("DEFERRED_CAMUNDA_UNREACHABLE:" + e.name());
            log.warn("VmPowerWatch: Camunda injoignable — redémarrage de {} différé (incident #{} reste ouvert)",
                    e.name(), i.getId());
            return;
        }
        IncidentAction action;
        try {
            action = actionService.createAction(i, ActionType.RESTART_VM,
                    "Rallumage automatique de la VM " + e.name() + " via watchdog hôte (politique VM_DOWN : VM "
                            + "éteinte confirmée par vmrun list, sondes en échec)",
                    SYSTEM_USER, e.incidentNamespace(), RemediationTargetKind.UNKNOWN, e.name(), AutomationMode.AUTO);
        } catch (ActionException refused) {
            events.add("ACTION_REFUSED:" + e.name() + ":" + refused.getMessage());
            return;
        }
        try {
            WorkflowInstance workflow = workflowService.startWorkflow(
                    i, "Node-Remediation-Workflow", e.incidentNamespace(), e.name());
            RemediationPlan plan = new RemediationPlan(
                    ActionType.RESTART_VM, AutomationMode.AUTO, false, true, false, false, false,
                    e.incidentNamespace(), RemediationTargetKind.UNKNOWN, e.name(),
                    "Panne VM " + e.name() + " — rallumage automatique (RESTART_VM) via watchdog hôte",
                    i.getIncidentType(), i.getId());
            AIPredictResponse pred = new AIPredictResponse();
            pred.setIncidentType(i.getIncidentType());
            pred.setRecommendedAction("RESTART_NODE_VM");
            camunda.startWorkflow(workflow, action, plan, pred);
            i.setStatus(IncidentStatus.IN_PROGRESS);
            incidents.save(i);
            history.record(i, IncidentStatus.IN_PROGRESS, "Rallumage automatique (RESTART_VM) de " + e.name()
                    + " via Camunda (incident-remediation → execute-remediation → watchdog hôte) ; résolution "
                    + "UNIQUEMENT après preuves de récupération persistées.");
            events.add("LAUNCHED:" + e.name());
            log.warn("VmPowerWatch: {} éteinte (confirmée) — workflow RESTART_VM lancé pour l'incident #{}",
                    e.name(), i.getId());
        } catch (Exception ex) {
            String reason = "Lancement du workflow de redémarrage impossible : " + ex.getClass().getSimpleName();
            actionService.markActionFailed(action.getId(), reason, SYSTEM_USER);
            i.setStatus(IncidentStatus.RESOLUTION_FAILED);
            incidents.save(i);
            history.record(i, IncidentStatus.RESOLUTION_FAILED, reason);
            try {
                notifications.notifyRemediationFailed(i, reason);
            } catch (Exception notifyEx) {
                log.warn("VmPowerWatch: notification d'échec impossible ({})", notifyEx.toString());
            }
            events.add("LAUNCH_FAILED:" + e.name());
        }
    }

    // ── Exécution DIRECTE (route BACKEND_DIRECT : .160, sans Camunda) ────────────────────────────

    /**
     * .160 : le backend décide ET exécute, sans workflow Camunda (donc sans escalade Camunda) ni e-mail.
     * Ordre : (1) la DEMANDE est persistée (action RESTART_VM unique par incident, sous verrou de ligne) et l'incident
     * passe IN_PROGRESS AVANT tout appel à l'hyperviseur ; (2) UN SEUL {@code POST /restart} au watchdog, jamais rejoué ;
     * (3) la récupération est prouvée par {@link #closeIfRecovered} (deux passes persistées) dans un budget borné
     * ({@code directVerifyBudgetMinutes}) ; sinon RESOLUTION_FAILED explicite (jamais de boucle de démarrage).
     */
    private void launchDirect(Entry e, Incident i, long nowMs, List<String> events) {
        IncidentAction action;
        try {
            action = actionService.createAction(i, ActionType.RESTART_VM,
                    "Rallumage automatique de la VM " + e.name() + " par le chemin DIRECT du backend (sans Camunda) via "
                            + "watchdog hôte (politique VM_DOWN : VM éteinte confirmée par vmrun list, sondes en échec)",
                    SYSTEM_USER, e.incidentNamespace(), RemediationTargetKind.UNKNOWN, e.name(), AutomationMode.AUTO);
        } catch (ActionException refused) {
            events.add("ACTION_REFUSED:" + e.name() + ":" + refused.getMessage());
            return;
        }
        i.setStatus(IncidentStatus.IN_PROGRESS);
        incidents.save(i);
        history.record(i, IncidentStatus.IN_PROGRESS, "Demande de rallumage de " + e.name() + " PERSISTÉE (action #"
                + action.getId() + ") ; appel du watchdog hôte : un seul POST, jamais rejoué — chemin direct du backend, "
                + "sans Camunda, interface seulement.");
        WatchdogClient.RestartOutcome outcome;
        try {
            outcome = watchdog.restart(e.name(), i.getId(), null);
        } catch (RuntimeException ex) {
            failDirect(e, i, action, "appel du watchdog impossible (" + ex.getClass().getSimpleName() + ")", events);
            return;
        }
        if (outcome.noFailure()) {
            actionService.markActionCompleted(action.getId(), SYSTEM_USER, outcome.describe());
            history.record(i, IncidentStatus.IN_PROGRESS, "Watchdog hôte : " + outcome.describe() + ". Un code 200 n'est "
                    + "PAS une preuve de récupération : résolution UNIQUEMENT après deux passes de preuves persistées "
                    + "(SSH, services, etcd).");
            events.add("LAUNCHED_DIRECT:" + e.name() + ":" + outcome.kind());
            log.warn("VmPowerWatch: {} éteinte (confirmée) — rallumage DIRECT demandé pour l'incident #{} ({})",
                    e.name(), i.getId(), outcome.kind());
        } else {
            failDirect(e, i, action, "watchdog hôte " + outcome.describe(), events);
        }
    }

    /** Échec explicite de la route directe : action échouée, RESOLUTION_FAILED, notification INTERFACE seulement. */
    private void failDirect(Entry e, Incident i, IncidentAction action, String reason, List<String> events) {
        if (action != null) {
            actionService.markActionFailed(action.getId(), reason, SYSTEM_USER);
        }
        i.setStatus(IncidentStatus.RESOLUTION_FAILED);
        incidents.save(i);
        history.record(i, IncidentStatus.RESOLUTION_FAILED, "Rallumage automatique de " + e.name() + " NON abouti : "
                + reason + ". État explicite conservé : aucun nouveau démarrage dans cet épisode ; clôture par preuves de "
                + "récupération, ou archivage par l'administrateur.");
        try {
            notifications.notifyRemediationFailedUiOnly(i, reason);
        } catch (Exception ex) {
            log.warn("VmPowerWatch: notification d'échec (interface) impossible pour {} ({})", e.name(), ex.toString());
        }
        events.add("DIRECT_RESTART_FAILED:" + e.name());
        log.warn("VmPowerWatch: rallumage direct de {} en échec pour l'incident #{} : {}", e.name(), i.getId(), reason);
    }

    /** Budget de preuves dépassé pour un rallumage direct accepté : RESOLUTION_FAILED explicite. */
    private void expireDirectIfLate(Entry e, Incident i, long nowMs, List<String> events) {
        long elapsed = msSinceLastRestart(e, nowMs);
        long budgetMs = Math.max(1, props.getDirectVerifyBudgetMinutes()) * 60_000L;
        if (elapsed != VmPowerPolicy.NEVER && elapsed <= budgetMs) {
            return;   // encore dans le budget
        }
        // NEVER : l'action date de plus que la fenêtre de recherche (≥ budget et ≥ cooldown) ou n'existe plus ⇒ le budget est dépassé a fortiori.
        failDirect(e, i, null, "preuves de récupération non obtenues dans le budget de " + (budgetMs / 60_000L) + " min",
                events);
    }

    // ── Incidents ────────────────────────────────────────────────────────────────────────────────

    private Optional<Incident> latestNotClosed(Entry e, String type) {
        return incidents.findFirstByNamespaceAndAppAndIncidentTypeAndStatusNotInOrderByCreatedAtDesc(
                e.incidentNamespace(), e.incidentApp(), type, CLOSED_STATUSES);
    }

    private void openUnreachable(Entry e, Track t, boolean uiOnly, List<String> events) {
        if (latestNotClosed(e, UNREACHABLE_TYPE).isPresent()) {
            return;
        }
        Incident i = createIncident(e, UNREACHABLE_TYPE, IncidentSeverity.HIGH,
                "Infrastructure muette: " + e.incidentNamespace() + "/" + e.name(),
                "VM '" + e.name() + "' (" + e.ip() + ") allumée selon vmrun mais TOUTES les sondes réseau échouent "
                        + "depuis " + t.silentRunningCycles + " cycles. DÉTECTION SEULE : aucun redémarrage automatique.",
                uiOnly);
        if (i != null) {
            events.add("UNREACHABLE_INCIDENT:" + e.name());
        }
    }

    private Incident createIncident(Entry e, String type, IncidentSeverity severity, String title,
                                    String description, boolean uiOnly) {
        Incident incident = new Incident();
        incident.setSource(IncidentSource.OTHER);
        incident.setStatus(IncidentStatus.OPEN);
        incident.setNamespace(e.incidentNamespace());
        incident.setApp(e.incidentApp());
        incident.setIncidentType(type);
        incident.setSeverity(severity);
        incident.setTitle(title);
        incident.setDescription(description.length() > 1000 ? description.substring(0, 997) + "..." : description);
        try {
            incident = incidents.save(incident);
        } catch (DataIntegrityViolationException duplicate) {
            log.debug("VmPowerWatch: incident déjà ouvert pour {}/{} ({}) — pas de doublon", e.incidentNamespace(),
                    e.incidentApp(), type);
            return null;
        }
        history.record(incident, IncidentStatus.OPEN, description);
        try {
            if (uiOnly) {
                notifications.notifyNewIncidentUiOnly(incident);
            } else {
                notifications.notifyNewIncident(incident);
            }
        } catch (Exception ex) {
            log.warn("VmPowerWatch: notification impossible pour {} ({})", e.name(), ex.toString());
        }
        log.warn("VmPowerWatch: incident {} créé pour {} ({})", type, e.name(),
                uiOnly ? "interface seulement" : "notifications complètes");
        return incident;
    }

    /** Épisode RESOLUTION_FAILED ouvert : aucun nouvel incident, aucune action, aucun e-mail — rappel d'historique espacé. */
    private void episodeReminder(Entry e, Track t, Incident failed, long nowMs, List<String> events) {
        if (!t.confirmedOff()) {
            return;
        }
        if (due("episode:" + failed.getId(), nowMs, reminderMs())) {
            history.record(failed, IncidentStatus.RESOLUTION_FAILED, "Épisode ouvert : " + e.name() + " est toujours "
                    + "éteinte. Aucun nouveau redémarrage tant que cet incident n'est pas clos par des preuves de "
                    + "récupération ou archivé par l'administrateur.");
            events.add("EPISODE_REMINDER:" + e.name());
        }
    }

    // ── Clôture par PREUVES (R5) ─────────────────────────────────────────────────────────────────

    /**
     * Ferme un incident de la famille VM dont aucun workflow Camunda n'est en cours (détection seule, mode ombre,
     * épisode RESOLUTION_FAILED, retour spontané) UNIQUEMENT après DEUX passes de récupération consécutives
     * PERSISTÉES (espacées d'au moins 15 s) puis un verdict PASS relu en base. Aucune passe n'est écrite tant que la
     * VM est en panne (pas de bruit en base) ; un échec qui suit une réussite est écrit pour casser la série.
     */
    private void closeIfRecovered(Entry e, Map<String, VmState> states, long nowMs, List<String> events) {
        List<String> types = new ArrayList<>();
        types.add(e.incidentType());
        if (e.vmClass() == VmPowerPolicy.VmClass.INFRA_LB || e.vmClass() == VmPowerPolicy.VmClass.INFRA_MONITORING) {
            types.add(UNREACHABLE_TYPE);
        }
        boolean direct = e.route() == VmPowerPolicy.Route.BACKEND_DIRECT;
        for (String type : types) {
            Optional<Incident> found = latestNotClosed(e, type);
            if (found.isEmpty()) {
                continue;
            }
            Incident i = found.get();
            boolean inFlight = i.getStatus() == IncidentStatus.IN_PROGRESS
                    || i.getStatus() == IncidentStatus.WAITING_FOR_APPROVAL;
            // Route CAMUNDA : le workflow de cet incident porte lui-même la vérification. Route DIRECTE : pas de
            // workflow, c'est CE cycle qui prouve la récupération (et borne le délai).
            boolean directInFlight = direct && inFlight && type.equals(e.incidentType());
            if (inFlight && !directInFlight) {
                continue;
            }
            PassResult pass = recoveryPass(e, states);
            boolean resolved = pass != null && recordPass(e, i, type, pass, nowMs, events);
            if (!resolved && directInFlight) {
                expireDirectIfLate(e, i, nowMs, events);
            }
        }
    }

    /** Enregistre UNE passe ; vrai si l'incident vient d'être résolu (deux passes consécutives + PASS relu). */
    private boolean recordPass(Entry e, Incident i, String type, PassResult pass, long nowMs, List<String> events) {
        Recovery run = recoveries.computeIfAbsent(i.getId(), k -> new Recovery());
        LocalDateTime at = LocalDateTime.ofInstant(Instant.ofEpochMilli(nowMs), ZoneId.systemDefault());
        if (!pass.passed()) {
            if (run.streak > 0) {
                card.recordVmRecoveryEvidence(i.getId(), e.name(), ++run.sample, at, false, pass.criteriaJson());
                run.streak = 0;
            }
            return false;
        }
        card.recordVmRecoveryEvidence(i.getId(), e.name(), ++run.sample, at, true, pass.criteriaJson());
        run.streak++;
        if (run.streak >= 2 && card.hasTwoConsecutiveVmRecoveryEvidence(i.getId(), e.name())) {
            card.recordVmRecoveryVerification(i.getId(), e.name(), pass.summary());
            i.setStatus(IncidentStatus.RESOLVED);
            incidents.save(i);
            history.record(i, IncidentStatus.RESOLVED,
                    "Récupération confirmée par deux passes consécutives persistées : " + pass.summary());
            try {
                if (e.notifications() == VmPowerPolicy.Notifications.UI_ONLY) {
                    notifications.notifyIncidentResolvedUiOnly(i);
                } else {
                    notifications.notifyIncidentResolved(i);
                }
            } catch (Exception ex) {
                log.warn("VmPowerWatch: notification de clôture impossible pour {} ({})", e.name(), ex.toString());
            }
            recoveries.remove(i.getId());
            events.add("RESOLVED_WITH_PROOF:" + e.name() + ":" + type);
            return true;
        }
        return false;
    }

    /** UNE passe de récupération ; {@code null} si elle n'est pas évaluable (jamais un succès par défaut). */
    private PassResult recoveryPass(Entry e, Map<String, VmState> states) {
        if (e.vmClass() == VmPowerPolicy.VmClass.K8S_WORKER || e.vmClass() == VmPowerPolicy.VmClass.K8S_CONTROL_PLANE) {
            NodeReadinessObservation o = nodeReadiness.inspectNodeReadiness(e.name());
            if (o == null) {
                return null;
            }
            // Nœuds Kubernetes cibles de rallumage (.155, .158 et, depuis le 2026-11-17, .153 plan de contrôle) : Ready ne
            // suffit pas — kubelet, SSH, etcd, CNPG et backend aussi (+ apiserver 6443 pour le plan de contrôle).
            return verifier.verifyWorker(e, o);
        }
        return states == null ? null : verifier.verifyInfra(e, states);
    }

    // ── Vue pour l'interface ─────────────────────────────────────────────────────────────────────

    /** Politique statique (aucune observation) : nom, adresse, mode, classe — pour l'interface. */
    public static List<Map<String, Object>> policyOnly() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Entry e : VmPowerPolicy.entries()) {
            Map<String, Object> m = new java.util.LinkedHashMap<>();
            m.put("name", e.name());
            m.put("ip", e.ip());
            m.put("mode", e.mode().name());
            m.put("vmClass", e.vmClass().name());
            m.put("restartRoute", e.route().name());
            m.put("notifications", e.notifications().name());
            out.add(m);
        }
        return out;
    }

    /** Politique + dernier état d'alimentation observé (jamais d'appel réseau ici). */
    public List<Map<String, Object>> snapshot() {
        List<Map<String, Object>> out = policyOnly();
        for (Map<String, Object> m : out) {
            Track t = tracks.get(String.valueOf(m.get("name")));
            VmState s = t == null ? null : t.lastState;
            m.put("powerState", s == null ? PowerState.UNKNOWN.name() : s.powerState().name());
            m.put("stateReason", s == null ? "NON_OBSERVEE" : s.stateReason());
            m.put("offConfirmed", t != null && t.confirmedOff());
            m.put("maintenance", s != null && s.watchdog() != null && s.watchdog().maintenance());
            m.put("watchdogReachable", t != null && t.watchdogReachable);
        }
        return out;
    }

    // ── Outils ───────────────────────────────────────────────────────────────────────────────────

    private long reminderMs() {
        return Math.max(1, props.getEpisodeReminderMinutes()) * 60_000L;
    }

    /** Vrai (et mémorise) si {@code key} n'a pas été annoncé depuis {@code periodMs}. */
    private boolean due(String key, long nowMs, long periodMs) {
        Long last = lastNote.get(key);
        if (last != null && nowMs - last < periodMs) {
            return false;
        }
        lastNote.put(key, nowMs);
        return true;
    }
}
