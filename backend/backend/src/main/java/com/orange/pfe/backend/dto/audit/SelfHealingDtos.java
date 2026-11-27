package com.orange.pfe.backend.dto.audit;

import java.time.LocalDateTime;
import java.util.List;

/**
 * Trace d'auto-remédiation DÉRIVÉE (lecture seule) : relue à la demande dans l'historique d'incident, les actions de
 * remédiation, les vérifications, les relevés pris sur la VM et les logs Loki. Rien n'est écrit dans le journal d'audit.
 */
public final class SelfHealingDtos {

    private SelfHealingDtos() {
    }

    // ── Liste (période) ───────────────────────────────────────────────────────
    public record Item(Long incidentId, String title, String type, String resource, String status, LocalDateTime createdAt,
                       int actions, int failedActions, String lastActionType, String lastActionStatus, LocalDateTime lastActionAt) {}

    public record Listing(LocalDateTime from, LocalDateTime to, boolean truncated, List<Item> items) {}

    // ── Trace d'un incident ───────────────────────────────────────────────────
    public record IncidentSummary(Long id, String title, String type, String namespace, String app, String severity,
                                  String status, LocalDateTime createdAt) {}

    /** {@code kind} : INCIDENT, HISTORY, ACTION, VERIFICATION ou EVIDENCE. */
    public record Event(LocalDateTime at, String kind, String title, String detail) {}

    /** {@code logSource} : où chercher les logs de cette action (« VM worker1 », « pods pfe/backend »), ou null. */
    public record ActionInfo(Long id, String type, String status, String mode, int attempts, String executedBy,
                             LocalDateTime createdAt, LocalDateTime executedAt, String target, String failedReason,
                             boolean remediation, String logSource) {}

    public record VerificationInfo(String criteria, String result, boolean available, LocalDateTime at, String reason) {}

    /** Métadonnées d'un relevé pris sur la VM : la sortie des commandes n'est volontairement pas reprise ici. */
    public record EvidenceInfo(String vm, String phase, String command, Integer exitCode, boolean available, LocalDateTime at) {}

    public record Trace(IncidentSummary incident, List<Event> timeline, List<ActionInfo> actions,
                        List<VerificationInfo> verifications, List<EvidenceInfo> evidence) {}

    // ── Logs autour d'une action ──────────────────────────────────────────────
    public record LogLine(LocalDateTime at, String text, boolean error, String category, String label) {}

    public record ActionLogs(boolean available, String reason, String source, LocalDateTime from, LocalDateTime to,
                             boolean truncated, List<LogLine> lines) {}

    // ── Alertes liées à l'incident (Prometheus, Alertmanager, Grafana, Loki) ──
    /** {@code status} : OK, UNAVAILABLE (outil en panne) ou NOT_CONFIGURED (adresse ou jeton absent, ou rien de configuré). */
    public record AlertItem(String name, String severity, String summary, java.util.Map<String, String> labels,
                            LocalDateTime firstFiring, LocalDateTime lastFiring, boolean activeNow, String ruleState,
                            String ruleIncidentType, boolean typeMatches) {}

    public record ToolAlerts(String tool, String status, String message, List<AlertItem> alerts) {}

    public record LokiRules(String status, String message, List<String> rules) {}

    public record Alerts(LocalDateTime from, LocalDateTime to, boolean truncated, ToolAlerts prometheus, ToolAlerts alertmanager,
                         ToolAlerts grafana, LokiRules loki) {}

    // ── Signaux de logs (équivalent d'alertes Loki, dérivé du catalogue de significations) ──
    public record LogSignal(String category, String label, String meaning, String advice, long count, LocalDateTime first,
                            LocalDateTime last, String example) {}

    public record LogSignals(boolean available, String reason, String source, LocalDateTime from, LocalDateTime to,
                             boolean truncated, int linesRead, List<LogSignal> signals) {}

    // ── Trace Camunda de l'incident ───────────────────────────────────────────
    public record CamundaStepInfo(String name, String type, String state, LocalDateTime start, LocalDateTime end, Long durationMs,
                                  boolean incident) {}

    public record CamundaIncidentInfo(String errorType, String message, String element, LocalDateTime createdAt) {}

    public record WorkflowLogLine(LocalDateTime at, String message) {}

    /** Une exécution de workflow de l'incident ; les champs Camunda sont nuls si l'instance n'a pas pu être lue. */
    public record CamundaRun(Long workflowId, String workflowName, String status, String processInstanceKey, String definitionId,
                             String state, LocalDateTime start, LocalDateTime end, boolean hasIncident, String note,
                             List<CamundaStepInfo> steps, List<CamundaIncidentInfo> incidents, List<WorkflowLogLine> logs) {}

    public record CamundaTrace(boolean available, String error, List<CamundaRun> runs) {}
}
