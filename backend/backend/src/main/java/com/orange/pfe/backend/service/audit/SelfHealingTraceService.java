package com.orange.pfe.backend.service.audit;

import com.fasterxml.jackson.databind.JsonNode;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.ActionInfo;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.ActionLogs;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Event;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.EvidenceInfo;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.IncidentSummary;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Item;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Listing;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.LogLine;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Trace;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.VerificationInfo;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.repository.IncidentHistoryRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.IncidentTerminalEvidenceRepository;
import com.orange.pfe.backend.repository.IncidentVerificationRepository;
import com.orange.pfe.backend.repository.ReportDataRepository;
import com.orange.pfe.backend.repository.SelfHealingReadRepository;
import com.orange.pfe.backend.service.org.OrgAccessService;
import com.orange.pfe.backend.service.org.OrgPermission;
import com.orange.pfe.backend.service.supervision.LogMeaningCatalog;
import com.orange.pfe.backend.service.supervision.SupervisionClient;
import com.orange.pfe.backend.service.supervision.SupervisionClient.UnavailableException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;

/**
 * Trace d'auto-remédiation pour l'écran Audit-trail : DÉRIVÉE et en LECTURE SEULE. Elle relit l'historique d'incident, les
 * actions de remédiation, les vérifications et les relevés pris sur la VM, puis, à la demande, les VRAIS logs de la VM ou
 * du pod concerné (Loki) autour de l'exécution d'une action. Aucune écriture : ni dans le journal d'audit (aucun
 * {@code AuditAction} ni {@code AuditModule} créé, la consultation n'est pas tracée), ni sur les VM.
 *
 * <p>Réservée aux utilisateurs ayant {@code AUDIT_VIEW_ALL}. Tout est borné (100 incidents, 100 lignes de logs, 200
 * transitions, 50 relevés) et un Loki arrêté donne une réponse « indisponible », jamais une erreur serveur.</p>
 */
@Service
public class SelfHealingTraceService {

    static final int LIST_MAX = 100;
    static final int LOG_LINES_MAX = 100;
    static final int HISTORY_MAX = 200;
    static final int EVIDENCE_MAX = 50;
    static final long WINDOW_MINUTES = 5;
    private static final long MAX_SPAN_DAYS = 31;

    /** Nœud HELIOS → étiquette {@code host} dans Loki ; un nœud absent n'est pas collecté. */
    private static final Map<String, String> LOKI_HOSTS = Map.of(
            "k8s-master", "master", "k8s-worker1", "worker1", "k8s-worker2", "worker2",
            "k8s-monitoring", "k8s-monitoring", "k8s-aicamunda", "k8s-aiCamunda",
            "master", "master", "worker1", "worker1", "worker2", "worker2");
    private static final Pattern K8S_NAME = Pattern.compile("^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$");
    private static final Pattern ERROR_LINE = Pattern.compile(
            "(?i)(level=(error|fatal|crit)|\\berror\\b|\\bfatal\\b|\\bpanic\\b|\\bfailed\\b)");

    private final SelfHealingReadRepository read;
    private final ReportDataRepository reportData;
    private final IncidentRepository incidents;
    private final IncidentHistoryRepository history;
    private final IncidentVerificationRepository verifications;
    private final IncidentTerminalEvidenceRepository evidence;
    private final SupervisionClient loki;
    private final OrgAccessService access;

    public SelfHealingTraceService(SelfHealingReadRepository read, ReportDataRepository reportData, IncidentRepository incidents,
                                   IncidentHistoryRepository history, IncidentVerificationRepository verifications,
                                   IncidentTerminalEvidenceRepository evidence, SupervisionClient loki, OrgAccessService access) {
        this.read = read;
        this.reportData = reportData;
        this.incidents = incidents;
        this.history = history;
        this.verifications = verifications;
        this.evidence = evidence;
        this.loki = loki;
        this.access = access;
    }

    // ── Liste ─────────────────────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public Listing list(com.orange.pfe.backend.model.user.AppUser actor, LocalDateTime from, LocalDateTime to, int limit) {
        access.require(actor, OrgPermission.AUDIT_VIEW_ALL);
        LocalDateTime end = to == null ? LocalDateTime.now() : to;
        LocalDateTime start = from == null ? end.minusHours(24) : from;
        if (!start.isBefore(end)) {
            throw new IllegalArgumentException("La période est invalide : le début doit précéder la fin");
        }
        if (java.time.Duration.between(start, end).toDays() > MAX_SPAN_DAYS) {
            throw new IllegalArgumentException("La période ne peut pas dépasser " + MAX_SPAN_DAYS + " jours");
        }
        int n = Math.max(1, Math.min(limit, LIST_MAX));
        List<Incident> found = read.incidentsWithRemediationBetween(start, end, n + 1);
        boolean truncated = found.size() > n;
        List<Incident> shown = truncated ? found.subList(0, n) : found;
        Map<Long, List<IncidentAction>> actions = new java.util.LinkedHashMap<>();
        if (!shown.isEmpty()) {
            for (IncidentAction a : reportData.actionsOfIncidents(shown.stream().map(Incident::getId).toList())) {
                if (!SelfHealingReadRepository.NOT_REMEDIATION.contains(a.getType())) {
                    actions.computeIfAbsent(a.getIncident().getId(), k -> new ArrayList<>()).add(a);
                }
            }
        }
        List<Item> items = new ArrayList<>();
        for (Incident i : shown) {
            List<IncidentAction> acts = actions.getOrDefault(i.getId(), List.of());
            IncidentAction last = acts.isEmpty() ? null : acts.get(acts.size() - 1);
            items.add(new Item(i.getId(), i.getTitle(), i.getIncidentType(), resource(i), name(i.getStatus()), i.getCreatedAt(),
                    acts.size(), (int) acts.stream().filter(a -> a.getStatus() == ActionStatus.FAILED).count(),
                    last == null ? null : last.getType().name(), last == null ? null : name(last.getStatus()),
                    last == null ? null : when(last)));
        }
        return new Listing(start, end, truncated, items);
    }

    // ── Trace d'un incident ───────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public Trace trace(com.orange.pfe.backend.model.user.AppUser actor, Long incidentId) {
        access.require(actor, OrgPermission.AUDIT_VIEW_ALL);
        Incident i = requireIncident(incidentId);
        List<Event> events = new ArrayList<>();
        events.add(new Event(i.getCreatedAt(), "INCIDENT", "Incident détecté", i.getTitle()));
        history.findByIncidentId(incidentId).stream().limit(HISTORY_MAX)
                .forEach(h -> events.add(new Event(h.getCreatedAt(), "HISTORY", "Statut : " + name(h.getStatus()), h.getComment())));

        List<ActionInfo> actions = new ArrayList<>();
        List<IncidentAction> list = reportData.actionsOfIncidents(List.of(incidentId));
        for (IncidentAction a : list) {
            boolean remediation = !SelfHealingReadRepository.NOT_REMEDIATION.contains(a.getType());
            actions.add(new ActionInfo(a.getId(), a.getType().name(), name(a.getStatus()), name(a.getAutomationMode()),
                    a.getAttemptCount(), a.getExecutedBy(), a.getCreatedAt(), a.getExecutedAt(), a.getTargetName(),
                    a.getFailedReason(), remediation, source(i, a).map(Source::label).orElse(null)));
            events.add(new Event(a.getCreatedAt(), "ACTION", "Action lancée : " + a.getType() + " (" + name(a.getAutomationMode()) + ")",
                    a.getDescription()));
            if (a.getExecutedAt() != null) {
                events.add(new Event(a.getExecutedAt(), "ACTION", "Action exécutée : " + a.getType() + " → " + name(a.getStatus()),
                        a.getFailedReason()));
            }
        }
        List<VerificationInfo> checks = new ArrayList<>();
        verifications.findByIncidentIdOrderByVerifiedAtDesc(incidentId).forEach(v -> {
            checks.add(new VerificationInfo(v.getCriteria(), v.getResult(), v.isAvailable(), v.getVerifiedAt(), v.getReason()));
            events.add(new Event(v.getVerifiedAt(), "VERIFICATION", "Vérification : " + v.getResult(), v.getCriteria()));
        });
        List<EvidenceInfo> proofs = new ArrayList<>();
        evidence.findByIncidentIdOrderByCapturedAtAsc(incidentId).stream().limit(EVIDENCE_MAX).forEach(e -> {
            proofs.add(new EvidenceInfo(e.getVm(), name(e.getPhase()), e.getCommand(), e.getExitCode(), e.isAvailable(), e.getCapturedAt()));
            events.add(new Event(e.getCapturedAt(), "EVIDENCE", "Relevé VM " + e.getVm() + " (" + name(e.getPhase()) + ")", e.getCommand()));
        });
        events.sort(Comparator.comparing(Event::at, Comparator.nullsLast(Comparator.naturalOrder())));
        return new Trace(new IncidentSummary(i.getId(), i.getTitle(), i.getIncidentType(), i.getNamespace(), i.getApp(),
                name(i.getSeverity()), name(i.getStatus()), i.getCreatedAt()), events, actions, checks, proofs);
    }

    // ── Logs de la VM / du pod autour d'une action ────────────────────────────

    @Transactional(readOnly = true)
    public ActionLogs actionLogs(com.orange.pfe.backend.model.user.AppUser actor, Long incidentId, Long actionId) {
        access.require(actor, OrgPermission.AUDIT_VIEW_ALL);
        Incident i = requireIncident(incidentId);
        IncidentAction action = reportData.actionsOfIncidents(List.of(incidentId)).stream()
                .filter(a -> a.getId().equals(actionId)).findFirst()
                .orElseThrow(() -> new ResourceNotFoundException("Action introuvable pour cet incident : " + actionId));
        LocalDateTime at = when(action);
        LocalDateTime from = at.minusMinutes(WINDOW_MINUTES);
        LocalDateTime to = at.plusMinutes(WINDOW_MINUTES);
        Optional<Source> source = source(i, action);
        if (source.isEmpty()) {
            return new ActionLogs(false, "Les logs de cette ressource ne sont pas collectés par Loki", null, from, to, false, List.of());
        }
        try {
            ZoneId zone = ZoneId.systemDefault();
            JsonNode streams = loki.lokiRange(source.get().logql(), from.atZone(zone).toEpochSecond(),
                    to.atZone(zone).toEpochSecond(), 0, LOG_LINES_MAX);
            List<LogLine> lines = new ArrayList<>();
            for (JsonNode s : streams) {
                for (JsonNode v : s.path("values")) {
                    String text = v.path(1).asText("");
                    // erreur : mot-clé de niveau OU motif connu du catalogue (ex. « connection refused »)
                    LogMeaningCatalog.Meaning known = LogMeaningCatalog.classify(text);
                    boolean error = ERROR_LINE.matcher(text).find() || known != LogMeaningCatalog.OTHER;
                    LogMeaningCatalog.Meaning meaning = error ? known : null;
                    long nanos = Long.parseLong(v.path(0).asText("0"));
                    lines.add(new LogLine(LocalDateTime.ofInstant(Instant.ofEpochSecond(nanos / 1_000_000_000L), zone),
                            text.length() > 600 ? text.substring(0, 599) + "…" : text, error,
                            meaning == null ? null : meaning.category(), meaning == null ? null : meaning.label()));
                }
            }
            lines.sort(Comparator.comparing(LogLine::at));
            boolean truncated = lines.size() >= LOG_LINES_MAX;
            return new ActionLogs(true, null, source.get().label(), from, to, truncated,
                    lines.size() > LOG_LINES_MAX ? lines.subList(0, LOG_LINES_MAX) : lines);
        } catch (UnavailableException e) {
            return new ActionLogs(false, e.getMessage(), source.get().label(), from, to, false, List.of());
        }
    }

    // ── Règles internes ───────────────────────────────────────────────────────

    /** Où lire les logs : requête LogQL fixe et libellé lisible. Jamais construite à partir d'une valeur non validée. */
    record Source(String label, String logql) {}

    static Optional<Source> source(Incident i, IncidentAction a) {
        boolean node = "k8s-nodes".equals(i.getNamespace())
                || a.getTargetKind() == com.orange.pfe.backend.model.action.RemediationTargetKind.NODE;
        if (node) {
            String name = firstNonBlank(i.getApp(), a.getTargetName());
            String host = name == null ? null : LOKI_HOSTS.get(name.toLowerCase(Locale.ROOT));
            return host == null ? Optional.empty()
                    : Optional.of(new Source("VM " + host, "{job=~\"vm-syslog|node-syslog\", host=\"" + host + "\"}"));
        }
        String ns = firstNonBlank(i.getNamespace(), a.getTargetNamespace());
        String app = firstNonBlank(i.getApp(), a.getTargetName());
        if (ns == null || app == null || !K8S_NAME.matcher(ns).matches() || !K8S_NAME.matcher(app).matches()) {
            return Optional.empty();
        }
        return Optional.of(new Source("pods " + ns + "/" + app,
                "{job=\"k8s-containers\", filename=~\".*_" + ns + "_.*" + app + ".*\"}"));
    }

    private static String firstNonBlank(String a, String b) {
        return a != null && !a.isBlank() ? a.trim() : (b != null && !b.isBlank() ? b.trim() : null);
    }

    private static LocalDateTime when(IncidentAction a) {
        return a.getExecutedAt() != null ? a.getExecutedAt() : a.getCreatedAt();
    }

    private static String resource(Incident i) {
        return i.getNamespace() == null ? i.getApp() : i.getNamespace() + "/" + i.getApp();
    }

    private static String name(Enum<?> e) {
        return e == null ? null : e.name();
    }

    private Incident requireIncident(Long id) {
        return incidents.findById(id).orElseThrow(() -> new ResourceNotFoundException("Incident introuvable : " + id));
    }
}
