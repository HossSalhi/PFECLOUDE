package com.orange.pfe.backend.service.report;

import com.orange.pfe.backend.dto.maintenance.MaintenanceFilter;
import com.orange.pfe.backend.dto.team.TeamResponses.MemberTaskStats;
import com.orange.pfe.backend.dto.team.TeamResponses.TaskSummaryResponse;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.core.IncidentVerification;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import com.orange.pfe.backend.model.maintenance.Maintenance;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
import com.orange.pfe.backend.model.report.ReportType;
import com.orange.pfe.backend.model.team.TeamTask;
import com.orange.pfe.backend.repository.MaintenanceSearchRepository;
import com.orange.pfe.backend.repository.ReportDataRepository;
import com.orange.pfe.backend.service.team.TaskStats;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.DayOfWeek;
import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumMap;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Predicate;

/**
 * Construit le contenu des rapports À PARTIR DES DONNÉES RÉELLES (journal d'audit, incidents, actions de
 * remédiation, maintenances, tâches, utilisateurs). Aucune valeur n'est simulée : sans donnée, les indicateurs valent
 * zéro et les tableaux sont vides. Avec {@code compare}, les indicateurs sont recalculés sur la période précédente de
 * même durée pour afficher l'écart.
 */
@Service
public class ReportBuilders {

    private static final int TOP = 10;
    private static final int DETAIL_LIMIT = 200;
    private static final int MAINTENANCE_SCAN_LIMIT = 5_000;
    private static final DateTimeFormatter STAMP = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm");

    private static final Set<AuditAction> LOGINS = EnumSet.of(AuditAction.LOGIN, AuditAction.LOGIN_2FA);
    private static final Set<AuditAction> UNSUCCESSFUL = EnumSet.of(AuditAction.LOGIN_FAILED, AuditAction.ACCESS_DENIED);
    private static final Set<AuditAction> NOT_CHANGES = EnumSet.of(AuditAction.LOGIN, AuditAction.LOGIN_2FA,
            AuditAction.LOGIN_FAILED, AuditAction.LOGOUT, AuditAction.ACCESS_DENIED, AuditAction.EXPORT, AuditAction.GENERATE);

    private final ReportDataRepository data;
    private final MaintenanceSearchRepository maintenanceSearch;

    public ReportBuilders(ReportDataRepository data, MaintenanceSearchRepository maintenanceSearch) {
        this.data = data;
        this.maintenanceSearch = maintenanceSearch;
    }

    @Transactional(readOnly = true)
    public ReportContent build(ReportType type, ReportPeriod period, boolean compare) {
        ReportPeriod previous = compare ? period.previous() : null;
        return switch (type) {
            case USER_ACTIVITY -> userActivity(period, previous);
            case MAINTENANCE -> maintenance(period, previous);
            case TEAMS_TASKS -> teamsTasks(period, previous);
            case PLATFORM_ACTIVITY -> platformActivity(period, previous);
            case PERFORMANCE -> performance(period, previous);
            case AUTO_REMEDIATION_CYCLE -> autoRemediationCycle(period, previous);
        };
    }

    // ── Activité des utilisateurs ─────────────────────────────────────────────

    private record UserStats(Map<String, Map<AuditAction, Long>> perUser, long total, long activeUsers,
                             long logins, long failedLogins, long denied) {}

    private UserStats userStats(ReportPeriod p) {
        Map<String, Map<AuditAction, Long>> perUser = new LinkedHashMap<>();
        long total = 0, logins = 0, failed = 0, denied = 0;
        for (Object[] r : data.auditCountsByUserAndAction(p.from(), p.to())) {
            String user = r[0] == null ? "(anonyme / système)" : (String) r[0];
            AuditAction action = (AuditAction) r[1];
            long n = ((Number) r[2]).longValue();
            perUser.computeIfAbsent(user, k -> new EnumMap<>(AuditAction.class)).merge(action, n, Long::sum);
            total += n;
            if (LOGINS.contains(action)) {
                logins += n;
            }
            if (action == AuditAction.LOGIN_FAILED) {
                failed += n;
            }
            if (action == AuditAction.ACCESS_DENIED) {
                denied += n;
            }
        }
        long active = perUser.values().stream()
                .filter(m -> m.keySet().stream().anyMatch(a -> !UNSUCCESSFUL.contains(a))).count();
        return new UserStats(perUser, total, active, logins, failed, denied);
    }

    private ReportContent userActivity(ReportPeriod p, ReportPeriod prev) {
        UserStats cur = userStats(p);
        UserStats old = prev == null ? null : userStats(prev);
        ReportContent c = new ReportContent()
                .metric("Actions enregistrées", cur.total, old == null ? null : old.total, null)
                .metric("Utilisateurs actifs", cur.activeUsers, old == null ? null : old.activeUsers, null)
                .metric("Connexions réussies", cur.logins, old == null ? null : old.logins, null)
                .metric("Échecs de connexion", cur.failedLogins, old == null ? null : old.failedLogins, null)
                .metric("Accès refusés", cur.denied, old == null ? null : old.denied, null);

        List<Map.Entry<String, Map<AuditAction, Long>>> ordered = new ArrayList<>(cur.perUser.entrySet());
        ordered.sort((a, b) -> Long.compare(sum(b.getValue()), sum(a.getValue())));
        List<List<Object>> rows = new ArrayList<>();
        for (Map.Entry<String, Map<AuditAction, Long>> e : ordered.stream().limit(50).toList()) {
            Map<AuditAction, Long> m = e.getValue();
            rows.add(row(e.getKey(), sum(m), sumWhere(m, LOGINS::contains),
                    sumWhere(m, a -> a == AuditAction.LOGIN_FAILED),
                    sumWhere(m, a -> !NOT_CHANGES.contains(a)),
                    sumWhere(m, a -> a == AuditAction.EXPORT || a == AuditAction.GENERATE),
                    sumWhere(m, a -> a == AuditAction.ACCESS_DENIED)));
        }
        c.section("Activité par utilisateur",
                List.of("Utilisateur", "Total", "Connexions", "Échecs de connexion", "Modifications",
                        "Exports et rapports", "Refus d'accès"), rows);

        Map<String, long[]> roles = new LinkedHashMap<>();
        for (Object[] r : data.usersByRoleAndEnabled()) {
            long[] counts = roles.computeIfAbsent(String.valueOf(r[0]), k -> new long[2]);
            counts[Boolean.TRUE.equals(r[1]) ? 0 : 1] += ((Number) r[2]).longValue();
        }
        List<List<Object>> roleRows = new ArrayList<>();
        roles.forEach((role, n) -> roleRows.add(row(role, n[0], n[1])));
        c.section("Comptes par rôle (état actuel)", List.of("Rôle", "Actifs", "Désactivés"), roleRows);

        List<String> labels = new ArrayList<>();
        List<Long> values = new ArrayList<>();
        ordered.stream().limit(TOP).forEach(e -> {
            labels.add(e.getKey());
            values.add(sum(e.getValue()));
        });
        c.chart("bar", "Actions par utilisateur (top " + TOP + ")", labels, Map.of("Actions", values));
        addTimeline(c, "Activité par jour", data.auditTimestamps(p.from(), p.to()), p);
        return c;
    }

    // ── Maintenance ───────────────────────────────────────────────────────────

    private record MaintStats(List<Maintenance> list, Map<MaintenanceStatus, Long> byStatus, double plannedHours,
                              long completed, long completedOnTime, double avgActualHours) {}

    private MaintStats maintStats(ReportPeriod p) {
        List<Maintenance> list = maintenanceSearch.findInRange(MaintenanceFilter.range(p.from(), p.to()),
                MAINTENANCE_SCAN_LIMIT);
        Map<MaintenanceStatus, Long> byStatus = new EnumMap<>(MaintenanceStatus.class);
        for (MaintenanceStatus s : MaintenanceStatus.values()) {
            byStatus.put(s, 0L);
        }
        double planned = 0;
        long completed = 0, onTime = 0;
        double actualSum = 0;
        long actualN = 0;
        for (Maintenance m : list) {
            byStatus.merge(m.getStatus(), 1L, Long::sum);
            planned += overlapHours(m.getStartAt(), m.getEndAt(), p);
            if (m.getStatus() == MaintenanceStatus.COMPLETED) {
                completed++;
                if (m.getActualEndAt() != null && !m.getActualEndAt().isAfter(m.getEndAt())) {
                    onTime++;
                }
                if (m.getActualStartAt() != null && m.getActualEndAt() != null) {
                    actualSum += Duration.between(m.getActualStartAt(), m.getActualEndAt()).toMinutes() / 60.0;
                    actualN++;
                }
            }
        }
        return new MaintStats(list, byStatus, ReportContent.round1(planned), completed, onTime,
                actualN == 0 ? 0.0 : ReportContent.round1(actualSum / actualN));
    }

    /** Suspensions de la période : maintenances distinctes, cause (incident / décision manuelle), durée des reprises. */
    private record Suspensions(long maintenances, long byIncident, long manual, double avgMinutes, List<List<Object>> rows) {}

    private Suspensions suspensions(ReportPeriod p) {
        java.util.Set<Long> all = new java.util.HashSet<>();
        java.util.Set<Long> byIncident = new java.util.HashSet<>();
        java.util.Set<Long> manual = new java.util.HashSet<>();
        List<Long> minutes = new ArrayList<>();
        List<List<Object>> rows = new ArrayList<>();
        for (com.orange.pfe.backend.model.maintenance.MaintenanceHistory h : data.pauseResumeHistory(p.from(), p.to())) {
            com.fasterxml.jackson.databind.JsonNode details = json(h.getDetails());
            Maintenance m = h.getMaintenance();
            if (h.getChangeType() == com.orange.pfe.backend.model.maintenance.MaintenanceChangeType.PAUSED) {
                com.fasterxml.jackson.databind.JsonNode incident = details.path("new").path("incidentId");
                boolean viaIncident = incident.isNumber();
                all.add(m.getId());
                (viaIncident ? byIncident : manual).add(m.getId());
                if (rows.size() < DETAIL_LIMIT) {
                    String reason = details.path("new").path("reason").asText("");
                    rows.add(row(m.getId(), m.getTitle(), m.getResourceName(), STAMP.format(h.getChangedAt()),
                            viaIncident ? "Incident #" + incident.asLong() : "Décision manuelle", reason.isEmpty() ? "—" : reason));
                }
            } else {
                // la durée se lit dans la reprise elle-même : heure de reprise − heure de suspension enregistrée
                LocalDateTime since = parseTime(details.path("old").path("suspendedAt").asText(null));
                if (since != null) {
                    minutes.add(Math.max(0, Duration.between(since, h.getChangedAt()).toMinutes()));
                }
            }
        }
        double avg = minutes.isEmpty() ? 0.0 : ReportContent.round1(minutes.stream().mapToLong(Long::longValue).average().orElse(0));
        return new Suspensions(all.size(), byIncident.size(), manual.size(), avg, rows);
    }

    private static final com.fasterxml.jackson.databind.ObjectMapper JSON = new com.fasterxml.jackson.databind.ObjectMapper();

    private static com.fasterxml.jackson.databind.JsonNode json(String text) {
        try {
            return text == null || text.isBlank() ? JSON.createObjectNode() : JSON.readTree(text);
        } catch (Exception e) {
            return JSON.createObjectNode();
        }
    }

    private static LocalDateTime parseTime(String iso) {
        try {
            return iso == null || iso.isBlank() ? null : LocalDateTime.parse(iso);
        } catch (RuntimeException e) {
            return null;
        }
    }

    private ReportContent maintenance(ReportPeriod p, ReportPeriod prev) {
        MaintStats cur = maintStats(p);
        MaintStats old = prev == null ? null : maintStats(prev);
        Suspensions susp = suspensions(p);
        Suspensions oldSusp = prev == null ? null : suspensions(prev);
        ReportContent c = new ReportContent()
                .metric("Maintenances de la période", cur.list.size(), old == null ? null : old.list.size(), null)
                .metric("Terminées", cur.byStatus.get(MaintenanceStatus.COMPLETED),
                        old == null ? null : old.byStatus.get(MaintenanceStatus.COMPLETED), null)
                .metric("Annulées", cur.byStatus.get(MaintenanceStatus.CANCELLED),
                        old == null ? null : old.byStatus.get(MaintenanceStatus.CANCELLED), null)
                .metric("Heures de maintenance planifiées", cur.plannedHours, old == null ? null : old.plannedHours, "h")
                .metric("Terminées dans les temps", cur.completedOnTime, old == null ? null : old.completedOnTime, null)
                .metric("Durée réelle moyenne", cur.avgActualHours, old == null ? null : old.avgActualHours, "h")
                .metric("Maintenances suspendues", susp.maintenances, oldSusp == null ? null : oldSusp.maintenances, null)
                .metric("Suspendues à cause d'un incident", susp.byIncident, oldSusp == null ? null : oldSusp.byIncident, null)
                .metric("Suspendues par décision manuelle", susp.manual, oldSusp == null ? null : oldSusp.manual, null)
                .metric("Durée moyenne de suspension", susp.avgMinutes, oldSusp == null ? null : oldSusp.avgMinutes, "min")
                .metric("Maintenances suspendues en ce moment (état actuel)", data.suspendedMaintenanceCount(), null, null);

        Map<String, long[]> byResource = new LinkedHashMap<>();   // [nombre, annulées]
        Map<String, Double> hoursByResource = new LinkedHashMap<>();
        for (Maintenance m : cur.list) {
            String key = m.getResourceType() + " — " + m.getResourceName();
            long[] n = byResource.computeIfAbsent(key, k -> new long[2]);
            n[0]++;
            if (m.getStatus() == MaintenanceStatus.CANCELLED) {
                n[1]++;
            }
            hoursByResource.merge(key, overlapHours(m.getStartAt(), m.getEndAt(), p), Double::sum);
        }
        List<Map.Entry<String, long[]>> resources = new ArrayList<>(byResource.entrySet());
        resources.sort((a, b) -> Long.compare(b.getValue()[0], a.getValue()[0]));
        List<List<Object>> resourceRows = new ArrayList<>();
        for (Map.Entry<String, long[]> e : resources) {
            resourceRows.add(row(e.getKey(), e.getValue()[0], ReportContent.round1(hoursByResource.get(e.getKey())),
                    e.getValue()[1]));
        }
        c.section("Par ressource", List.of("Ressource", "Maintenances", "Heures planifiées", "Annulées"), resourceRows);

        List<List<Object>> detail = new ArrayList<>();
        for (Maintenance m : cur.list.stream().limit(DETAIL_LIMIT).toList()) {
            detail.add(row(m.getId(), m.getTitle(), m.getResourceName(), STAMP.format(m.getStartAt()),
                    STAMP.format(m.getEndAt()), m.getStatus().name(),
                    m.getResponsible() == null ? "—" : m.getResponsible().getUsername()));
        }
        c.section("Détail des maintenances", List.of("N°", "Titre", "Ressource", "Début", "Fin", "Statut", "Responsable"),
                detail);
        c.section("Suspensions (pauses de la période)", List.of("N°", "Titre", "Ressource", "Suspendue le", "Cause", "Motif"),
                susp.rows);

        List<String> statusLabels = new ArrayList<>();
        List<Long> statusValues = new ArrayList<>();
        cur.byStatus.forEach((s, n) -> {
            statusLabels.add(s.name());
            statusValues.add(n);
        });
        c.chart("doughnut", "Répartition par statut", statusLabels, Map.of("Maintenances", statusValues));
        List<String> resLabels = new ArrayList<>();
        List<Long> resValues = new ArrayList<>();
        resources.stream().limit(TOP).forEach(e -> {
            resLabels.add(e.getKey());
            resValues.add(e.getValue()[0]);
        });
        c.chart("bar", "Maintenances par ressource (top " + TOP + ")", resLabels, Map.of("Maintenances", resValues));
        return c;
    }

    // ── Équipes et tâches ─────────────────────────────────────────────────────

    private ReportContent teamsTasks(ReportPeriod p, ReportPeriod prev) {
        LocalDate today = LocalDate.now();
        List<TeamTask> created = data.tasksCreatedBetween(p.from(), p.to());
        long completed = data.tasksCompletedBetween(p.from(), p.to());
        List<TeamTask> open = data.openTasks();
        long overdueNow = open.stream().filter(t -> t.isOverdue(today)).count();
        TaskSummaryResponse sum = TaskStats.summarize(created, today);

        Number prevCreated = null;
        Number prevCompleted = null;
        List<TeamTask> prevList = null;
        if (prev != null) {
            prevList = data.tasksCreatedBetween(prev.from(), prev.to());
            prevCreated = prevList.size();
            prevCompleted = data.tasksCompletedBetween(prev.from(), prev.to());
        }
        long auto = created.stream().filter(t -> t.getSourceIncidentId() != null).count();
        Long prevAuto = prevList == null ? null : prevList.stream().filter(t -> t.getSourceIncidentId() != null).count();
        long pendingDecisions = open.stream()
                .filter(t -> t.getSourceEvent() == com.orange.pfe.backend.model.team.TaskSourceEvent.DECISION).count();
        ReportContent c = new ReportContent()
                .metric("Tâches créées", created.size(), prevCreated, null)
                .metric("Tâches terminées dans la période", completed, prevCompleted, null)
                .metric("Tâches ouvertes (état actuel)", open.size(), null, null)
                .metric("Tâches en retard (état actuel)", overdueNow, null, null)
                .metric("Taux d'achèvement des tâches créées", sum.completionRate(), null, "%")
                .metric("Tâches créées automatiquement (routage incident)", auto, prevAuto, null)
                .metric("Tâches créées manuellement", created.size() - auto, prevList == null ? null : prevList.size() - prevAuto, null)
                .metric("Tâches de décision en attente (état actuel)", pendingDecisions, null, null);

        Map<String, long[]> byTeam = new LinkedHashMap<>();   // [créées, terminées, ouvertes, en retard]
        for (TeamTask t : created) {
            String team = t.getTeam() == null ? "Sans équipe" : t.getTeam().getName();
            long[] n = byTeam.computeIfAbsent(team, k -> new long[4]);
            n[0]++;
            if (t.getStatus() == com.orange.pfe.backend.model.team.TaskStatus.DONE) {
                n[1]++;
            }
            if (t.getStatus().isOpen()) {
                n[2]++;
            }
            if (t.isOverdue(today)) {
                n[3]++;
            }
        }
        List<List<Object>> teamRows = new ArrayList<>();
        List<String> teamLabels = new ArrayList<>();
        List<Long> teamCreated = new ArrayList<>();
        List<Long> teamDone = new ArrayList<>();
        byTeam.entrySet().stream().sorted((a, b) -> Long.compare(b.getValue()[0], a.getValue()[0])).forEach(e -> {
            long[] n = e.getValue();
            teamRows.add(row(e.getKey(), n[0], n[1], n[2], n[3]));
            if (teamLabels.size() < TOP) {
                teamLabels.add(e.getKey());
                teamCreated.add(n[0]);
                teamDone.add(n[1]);
            }
        });
        c.section("Par équipe (tâches créées dans la période)",
                List.of("Équipe", "Créées", "Terminées", "Ouvertes", "En retard"), teamRows);

        // tâches automatiques du routage incident → équipe, par étape (décision, suivi, reprise)
        Map<com.orange.pfe.backend.model.team.TaskSourceEvent, long[]> byEvent = new EnumMap<>(com.orange.pfe.backend.model.team.TaskSourceEvent.class);
        for (com.orange.pfe.backend.model.team.TaskSourceEvent ev : com.orange.pfe.backend.model.team.TaskSourceEvent.values()) {
            byEvent.put(ev, new long[3]);   // [créées, terminées, ouvertes]
        }
        for (TeamTask t : created) {
            if (t.getSourceEvent() != null) {
                long[] n = byEvent.get(t.getSourceEvent());
                n[0]++;
                if (t.getStatus() == com.orange.pfe.backend.model.team.TaskStatus.DONE) {
                    n[1]++;
                }
                if (t.getStatus().isOpen()) {
                    n[2]++;
                }
            }
        }
        List<List<Object>> eventRows = new ArrayList<>();
        byEvent.forEach((ev, n) -> eventRows.add(row(switch (ev) {
            case DECISION -> "Décision (continuer ou suspendre)";
            case FOLLOW_UP -> "Suivi de l'incident";
            case RESUME -> "Reprise de la maintenance";
        }, n[0], n[1], n[2])));
        c.section("Tâches automatiques du routage incident → équipe (créées dans la période)",
                List.of("Étape", "Créées", "Terminées", "Ouvertes"), eventRows);

        List<List<Object>> memberRows = new ArrayList<>();
        for (MemberTaskStats m : TaskStats.byMember(created, today)) {
            memberRows.add(row(m.displayName(), m.total(), m.open(), m.done(), m.overdue()));
        }
        c.section("Par membre (tâches créées dans la période)",
                List.of("Membre", "Total", "Ouvertes", "Terminées", "En retard"), memberRows);

        List<String> statusLabels = new ArrayList<>(sum.byStatus().keySet());
        c.chart("doughnut", "Statut des tâches créées", statusLabels, Map.of("Tâches", new ArrayList<>(sum.byStatus().values())));
        Map<String, List<Long>> perTeam = new LinkedHashMap<>();
        perTeam.put("Créées", teamCreated);
        perTeam.put("Terminées", teamDone);
        c.chart("bar", "Tâches par équipe (top " + TOP + ")", teamLabels, perTeam);
        return c;
    }

    // ── Activités de la plateforme ────────────────────────────────────────────

    private ReportContent platformActivity(ReportPeriod p, ReportPeriod prev) {
        Map<String, Long> auditByModule = data.auditCountsByModule(p.from(), p.to());
        Map<String, Long> bySeverity = data.incidentsBySeverity(p.from(), p.to());
        Map<String, Long> byStatus = data.incidentsByStatus(p.from(), p.to());
        Map<String, Long> actions = data.remediationActionsByStatus(p.from(), p.to());
        long maintCount = maintenanceSearch.findInRange(MaintenanceFilter.range(p.from(), p.to()), MAINTENANCE_SCAN_LIMIT).size();
        int tasksCreated = data.tasksCreatedBetween(p.from(), p.to()).size();

        Number prevAudit = null, prevIncidents = null, prevActions = null, prevMaint = null, prevTasks = null;
        if (prev != null) {
            prevAudit = sum(data.auditCountsByModule(prev.from(), prev.to()));
            prevIncidents = sum(data.incidentsBySeverity(prev.from(), prev.to()));
            prevActions = sum(data.remediationActionsByStatus(prev.from(), prev.to()));
            prevMaint = maintenanceSearch.findInRange(MaintenanceFilter.range(prev.from(), prev.to()), MAINTENANCE_SCAN_LIMIT).size();
            prevTasks = data.tasksCreatedBetween(prev.from(), prev.to()).size();
        }
        ReportContent c = new ReportContent()
                .metric("Activités d'audit", sum(auditByModule), prevAudit, null)
                .metric("Incidents créés", sum(bySeverity), prevIncidents, null)
                .metric("Actions de remédiation", sum(actions), prevActions, null)
                .metric("Maintenances de la période", maintCount, prevMaint, null)
                .metric("Tâches créées", tasksCreated, prevTasks, null);

        c.section("Activité d'audit par module", List.of("Module", "Activités"), countRows(auditByModule));
        Map<String, Long> auditByResult = data.auditCountsByResult(p.from(), p.to());
        c.section("Activité d'audit par résultat", List.of("Résultat", "Activités"), countRows(auditByResult));
        c.section("Incidents créés par sévérité", List.of("Sévérité", "Incidents"), countRows(bySeverity));
        c.section("Incidents créés par statut actuel", List.of("Statut", "Incidents"), countRows(byStatus));
        c.section("Actions de remédiation par statut", List.of("Statut", "Actions"), countRows(actions));

        addTimeline(c, "Activité d'audit par jour", data.auditTimestamps(p.from(), p.to()), p);
        c.chart("bar", "Activité d'audit par module", new ArrayList<>(auditByModule.keySet()),
                Map.of("Activités", new ArrayList<>(auditByModule.values())));
        c.chart("bar", "Incidents créés par sévérité", new ArrayList<>(bySeverity.keySet()),
                Map.of("Incidents", new ArrayList<>(bySeverity.values())));
        return c;
    }

    // ── Performance ───────────────────────────────────────────────────────────

    private record Perf(long created, long resolved, double mttrAvg, double mttrMedian,
                        Map<String, long[]> bySeverity, Map<String, double[]> mttrBySeverity,
                        long actionsTotal, long actionsCompleted, long actionsFailed) {}

    private Perf perf(ReportPeriod p) {
        Map<String, Long> createdBySeverity = data.incidentsBySeverity(p.from(), p.to());
        long created = sum(createdBySeverity);
        List<Long> minutes = new ArrayList<>();
        Map<String, long[]> bySeverity = new LinkedHashMap<>();    // [créés, résolus]
        Map<String, double[]> mttr = new LinkedHashMap<>();        // [somme, nombre]
        createdBySeverity.forEach((sev, n) -> bySeverity.put(sev, new long[]{n, 0}));
        for (Object[] r : data.incidentResolutionTimes(p.from(), p.to())) {
            String sev = r[1] == null ? "—" : String.valueOf(r[1]);
            LocalDateTime createdAt = (LocalDateTime) r[2];
            LocalDateTime resolvedAt = (LocalDateTime) r[3];
            long m = Math.max(0, Duration.between(createdAt, resolvedAt).toMinutes());
            minutes.add(m);
            bySeverity.computeIfAbsent(sev, k -> new long[]{0, 0})[1]++;
            double[] acc = mttr.computeIfAbsent(sev, k -> new double[2]);
            acc[0] += m;
            acc[1]++;
        }
        minutes.sort(Comparator.naturalOrder());
        double avg = minutes.isEmpty() ? 0.0 : ReportContent.round1(minutes.stream().mapToLong(Long::longValue).average().orElse(0));
        double median = minutes.isEmpty() ? 0.0 : (minutes.size() % 2 == 1
                ? minutes.get(minutes.size() / 2)
                : (minutes.get(minutes.size() / 2 - 1) + minutes.get(minutes.size() / 2)) / 2.0);
        Map<String, Long> actions = data.remediationActionsByStatus(p.from(), p.to());
        return new Perf(created, minutes.size(), avg, ReportContent.round1(median), bySeverity, mttr,
                sum(actions), actions.getOrDefault("COMPLETED", 0L), actions.getOrDefault("FAILED", 0L));
    }

    /**
     * Routage incident → équipe de la période. Le délai de décision part de la création de la tâche « décision », faite au
     * même instant que l'envoi de la notification au responsable (ce n'est pas l'heure de réception du message).
     */
    private record Routing(long routed, long decided, double avgMinutes, double medianMinutes, long paused, long continued) {}

    private Routing routing(ReportPeriod p) {
        List<TeamTask> tasks = data.routingDecisionTasks(p.from(), p.to());
        List<TeamTask> decided = tasks.stream().filter(t -> t.getStatus() == com.orange.pfe.backend.model.team.TaskStatus.DONE
                && t.getCompletedAt() != null && t.getSourceIncidentId() != null).toList();
        java.util.Set<Long> suspended = decided.isEmpty() ? java.util.Set.of()
                : data.incidentsWithFollowUp(decided.stream().map(TeamTask::getSourceIncidentId).toList());
        List<Long> minutes = new ArrayList<>();
        long paused = 0;
        for (TeamTask t : decided) {
            minutes.add(Math.max(0, Duration.between(t.getCreatedAt(), t.getCompletedAt()).toMinutes()));
            if (suspended.contains(t.getSourceIncidentId())) {
                paused++;
            }
        }
        minutes.sort(Comparator.naturalOrder());
        double avg = minutes.isEmpty() ? 0.0 : ReportContent.round1(minutes.stream().mapToLong(Long::longValue).average().orElse(0));
        double median = minutes.isEmpty() ? 0.0 : (minutes.size() % 2 == 1
                ? minutes.get(minutes.size() / 2)
                : (minutes.get(minutes.size() / 2 - 1) + minutes.get(minutes.size() / 2)) / 2.0);
        return new Routing(tasks.size(), decided.size(), avg, ReportContent.round1(median), paused, decided.size() - paused);
    }

    private ReportContent performance(ReportPeriod p, ReportPeriod prev) {
        Perf cur = perf(p);
        Perf old = prev == null ? null : perf(prev);
        Routing route = routing(p);
        Routing oldRoute = prev == null ? null : routing(prev);
        double rate = cur.created == 0 ? 0.0 : ReportContent.round1(cur.resolved * 100.0 / cur.created);
        Double oldRate = old == null ? null : (old.created == 0 ? 0.0 : ReportContent.round1(old.resolved * 100.0 / old.created));
        long finished = cur.actionsCompleted + cur.actionsFailed;
        double success = finished == 0 ? 0.0 : ReportContent.round1(cur.actionsCompleted * 100.0 / finished);
        Double oldSuccess = null;
        if (old != null) {
            long oldFinished = old.actionsCompleted + old.actionsFailed;
            oldSuccess = oldFinished == 0 ? 0.0 : ReportContent.round1(old.actionsCompleted * 100.0 / oldFinished);
        }
        ReportContent c = new ReportContent()
                .metric("Incidents créés", cur.created, old == null ? null : old.created, null)
                .metric("Incidents résolus (parmi les créés)", cur.resolved, old == null ? null : old.resolved, null)
                .metric("Taux de résolution", rate, oldRate, "%")
                .metric("Temps de résolution moyen", cur.mttrAvg, old == null ? null : old.mttrAvg, "min")
                .metric("Temps de résolution médian", cur.mttrMedian, old == null ? null : old.mttrMedian, "min")
                .metric("Actions de remédiation terminées", cur.actionsCompleted, old == null ? null : old.actionsCompleted, null)
                .metric("Actions de remédiation en échec", cur.actionsFailed, old == null ? null : old.actionsFailed, null)
                .metric("Taux de réussite des actions", success, oldSuccess, "%")
                .metric("Incidents routés vers une équipe pendant une maintenance", route.routed, oldRoute == null ? null : oldRoute.routed, null)
                .metric("Décisions du responsable : suspendre la maintenance", route.paused, oldRoute == null ? null : oldRoute.paused, null)
                .metric("Décisions du responsable : continuer la maintenance", route.continued, oldRoute == null ? null : oldRoute.continued, null)
                .metric("Délai moyen de décision du responsable (depuis la notification)", route.avgMinutes, oldRoute == null ? null : oldRoute.avgMinutes, "min")
                .metric("Délai médian de décision du responsable", route.medianMinutes, oldRoute == null ? null : oldRoute.medianMinutes, "min");

        List<List<Object>> rows = new ArrayList<>();
        List<String> labels = new ArrayList<>();
        List<Long> createdSeries = new ArrayList<>();
        List<Long> resolvedSeries = new ArrayList<>();
        List<Double> mttrSeries = new ArrayList<>();
        for (Map.Entry<String, long[]> e : cur.bySeverity.entrySet()) {
            double[] acc = cur.mttrBySeverity.get(e.getKey());
            double avg = acc == null || acc[1] == 0 ? 0.0 : ReportContent.round1(acc[0] / acc[1]);
            rows.add(row(e.getKey(), e.getValue()[0], e.getValue()[1], avg));
            labels.add(e.getKey());
            createdSeries.add(e.getValue()[0]);
            resolvedSeries.add(e.getValue()[1]);
            mttrSeries.add(avg);
        }
        c.section("Par sévérité", List.of("Sévérité", "Créés", "Résolus", "Résolution moyenne (min)"), rows);
        Map<String, List<Long>> sets = new LinkedHashMap<>();
        sets.put("Créés", createdSeries);
        sets.put("Résolus", resolvedSeries);
        c.chart("bar", "Incidents créés et résolus par sévérité", labels, sets);
        c.chart("bar", "Temps de résolution moyen par sévérité (min)", labels, Map.of("Minutes", mttrSeries));
        return c;
    }

    // ── Cycle d'auto-remédiation ──────────────────────────────────────────────

    /** Types d'action qui ne sont pas de vraies remédiations : ils ne comptent pas comme « action lancée ». */
    private static final Set<ActionType> NOT_REMEDIATION = EnumSet.of(ActionType.NOTIFICATION, ActionType.INVESTIGATION, ActionType.ESCALATION);

    private static final String COMPLETE = "Cycle complet";
    private static final String NO_ACTION = "Sans action";
    private static final String NO_VERIFICATION = "Action sans vérification";
    private static final String NO_RESOLUTION = "Vérifié sans résolution";
    private static final String RESOLVED_FIRST = "Résolu avant la vérification";

    /** Un incident et les horodatages de chaque phase de son cycle (une borne absente vaut {@code null}). */
    private record Cycle(Incident incident, LocalDateTime detected, LocalDateTime decided, IncidentAction firstAction,
                         LocalDateTime actionStart, LocalDateTime actionEnd, IncidentVerification verification,
                         LocalDateTime resolved, String state, boolean automatic) {}

    private record CycleStats(List<Cycle> cycles, long complete, long resolved, long automatic, double avgDetectToAction,
                              double avgActionToVerification, double avgVerificationToResolution, long failedActions,
                              double avgAttempts, long evidenceBefore, long evidenceAfter,
                              Map<ActionType, long[]> byType, Map<String, Long> byState) {}

    private CycleStats cycleStats(ReportPeriod p) {
        List<Incident> incidents = data.incidentsCreatedBetween(p.from(), p.to());
        List<Long> ids = incidents.stream().map(Incident::getId).toList();
        Map<Long, List<IncidentAction>> actions = new LinkedHashMap<>();
        Map<Long, List<IncidentVerification>> verifications = new LinkedHashMap<>();
        Map<Long, LocalDateTime> resolvedAt = Map.of();
        java.util.Set<Long> waiting = java.util.Set.of();
        java.util.Set<Long> humans = java.util.Set.of();
        Map<Long, TeamTask> decisions = new LinkedHashMap<>();
        long evBefore = 0, evAfter = 0;
        if (!ids.isEmpty()) {
            for (IncidentAction a : data.actionsOfIncidents(ids)) {
                if (!NOT_REMEDIATION.contains(a.getType())) {
                    actions.computeIfAbsent(a.getIncident().getId(), k -> new ArrayList<>()).add(a);
                }
            }
            for (IncidentVerification v : data.verificationsOfIncidents(ids)) {
                if (v.getVerifiedAt() != null) {
                    verifications.computeIfAbsent(v.getIncidentId(), k -> new ArrayList<>()).add(v);
                }
            }
            resolvedAt = data.firstHistoryAt(ids, IncidentStatus.RESOLVED);
            waiting = data.firstHistoryAt(ids, IncidentStatus.WAITING_FOR_APPROVAL).keySet();
            humans = data.incidentsWithHumanTask(ids);
            for (TeamTask t : data.decisionTasksOfIncidents(ids)) {
                decisions.putIfAbsent(t.getSourceIncidentId(), t);
            }
            for (Object[] r : data.terminalEvidenceCounts(ids)) {
                if (r[1] == TerminalEvidencePhase.BEFORE) {
                    evBefore += ((Number) r[2]).longValue();
                } else {
                    evAfter += ((Number) r[2]).longValue();
                }
            }
        }

        List<Cycle> cycles = new ArrayList<>();
        Map<String, Long> byState = new LinkedHashMap<>();
        for (String s : List.of(COMPLETE, NO_ACTION, NO_VERIFICATION, NO_RESOLUTION, RESOLVED_FIRST)) {
            byState.put(s, 0L);
        }
        List<Long> toAction = new ArrayList<>(), toVerification = new ArrayList<>(), toResolution = new ArrayList<>();
        long complete = 0, resolvedCount = 0, automatic = 0;
        for (Incident i : incidents) {
            List<IncidentAction> acts = actions.getOrDefault(i.getId(), List.of());
            IncidentAction first = acts.isEmpty() ? null : acts.get(0);
            LocalDateTime actionStart = first == null ? null : first.getCreatedAt();
            LocalDateTime actionEnd = first == null ? null : (first.getExecutedAt() != null ? first.getExecutedAt() : first.getCreatedAt());
            IncidentVerification verification = null;
            if (actionStart != null) {
                for (IncidentVerification v : verifications.getOrDefault(i.getId(), List.of())) {
                    if (!v.getVerifiedAt().isBefore(actionStart)) {
                        verification = v;
                        break;
                    }
                }
            }
            LocalDateTime resolved = resolvedAt.get(i.getId());
            TeamTask decision = decisions.get(i.getId());
            String state;
            if (first == null) {
                state = NO_ACTION;
            } else if (verification == null) {
                state = NO_VERIFICATION;
            } else if (resolved == null) {
                state = NO_RESOLUTION;
            } else if (resolved.isBefore(verification.getVerifiedAt())) {
                state = RESOLVED_FIRST;
            } else {
                state = COMPLETE;
            }
            byState.merge(state, 1L, Long::sum);
            if (COMPLETE.equals(state)) {
                complete++;
            }
            if (first != null) {
                toAction.add(Math.max(0, Duration.between(i.getCreatedAt(), actionStart).toMinutes()));
            }
            if (first != null && verification != null) {
                toVerification.add(Math.max(0, Duration.between(actionEnd, verification.getVerifiedAt()).toMinutes()));
            }
            if (verification != null && resolved != null && !resolved.isBefore(verification.getVerifiedAt())) {
                toResolution.add(Duration.between(verification.getVerifiedAt(), resolved).toMinutes());
            }
            boolean auto = false;
            if (resolved != null) {
                resolvedCount++;
                // résolu sans intervention humaine : toutes les remédiations en AUTO, aucune tâche humaine, aucune
                // validation demandée, aucune décision d'équipe sollicitée par le routage
                auto = !acts.isEmpty() && acts.stream().allMatch(a -> a.getAutomationMode() == AutomationMode.AUTO)
                        && !humans.contains(i.getId()) && !waiting.contains(i.getId()) && decision == null;
                if (auto) {
                    automatic++;
                }
            }
            cycles.add(new Cycle(i, i.getCreatedAt(), decision == null ? null : decision.getCompletedAt(), first, actionStart,
                    actionEnd, verification, resolved, state, auto));
        }

        Map<ActionType, long[]> byType = new EnumMap<>(ActionType.class);   // [lancées, réussies, en échec, somme des tentatives]
        long failed = 0, attempts = 0, actionCount = 0;
        for (List<IncidentAction> list : actions.values()) {
            for (IncidentAction a : list) {
                long[] n = byType.computeIfAbsent(a.getType(), k -> new long[4]);
                n[0]++;
                n[3] += a.getAttemptCount();
                if (a.getStatus() == ActionStatus.COMPLETED) {
                    n[1]++;
                }
                if (a.getStatus() == ActionStatus.FAILED) {
                    n[2]++;
                    failed++;
                }
                attempts += a.getAttemptCount();
                actionCount++;
            }
        }
        return new CycleStats(cycles, complete, resolvedCount, automatic, avg(toAction), avg(toVerification), avg(toResolution),
                failed, actionCount == 0 ? 0.0 : ReportContent.round1((double) attempts / actionCount), evBefore, evAfter, byType, byState);
    }

    private static double avg(List<Long> minutes) {
        return minutes.isEmpty() ? 0.0 : ReportContent.round1(minutes.stream().mapToLong(Long::longValue).average().orElse(0));
    }

    private static String stamp(LocalDateTime t) {
        return t == null ? "—" : STAMP.format(t);
    }

    private ReportContent autoRemediationCycle(ReportPeriod p, ReportPeriod prev) {
        CycleStats cur = cycleStats(p);
        CycleStats old = prev == null ? null : cycleStats(prev);
        double rate = cur.resolved == 0 ? 0.0 : ReportContent.round1(cur.automatic * 100.0 / cur.resolved);
        Double oldRate = old == null ? null : (old.resolved == 0 ? 0.0 : ReportContent.round1(old.automatic * 100.0 / old.resolved));
        ReportContent c = new ReportContent()
                .metric("Incidents de la période", cur.cycles.size(), old == null ? null : old.cycles.size(), null)
                .metric("Cycles complets (action, vérification, résolution)", cur.complete, old == null ? null : old.complete, null)
                .metric("Cycles incomplets", cur.cycles.size() - cur.complete, old == null ? null : old.cycles.size() - old.complete, null)
                .metric("Résolutions automatiques sans intervention humaine", cur.automatic, old == null ? null : old.automatic, null)
                .metric("Taux de résolution automatique", rate, oldRate, "%")
                .metric("Durée moyenne détection → action", cur.avgDetectToAction, old == null ? null : old.avgDetectToAction, "min")
                .metric("Durée moyenne action → vérification", cur.avgActionToVerification, old == null ? null : old.avgActionToVerification, "min")
                .metric("Durée moyenne vérification → résolution", cur.avgVerificationToResolution, old == null ? null : old.avgVerificationToResolution, "min")
                .metric("Actions de remédiation en échec", cur.failedActions, old == null ? null : old.failedActions, null)
                .metric("Tentatives moyennes par action", cur.avgAttempts, old == null ? null : old.avgAttempts, null)
                .metric("Relevés VM avant remédiation", cur.evidenceBefore, old == null ? null : old.evidenceBefore, null)
                .metric("Relevés VM après remédiation", cur.evidenceAfter, old == null ? null : old.evidenceAfter, null);

        List<List<Object>> rows = new ArrayList<>();
        for (Cycle cy : cur.cycles.stream().limit(DETAIL_LIMIT).toList()) {
            Incident i = cy.incident;
            rows.add(row(i.getId(), i.getTitle(), i.getIncidentType() == null ? "—" : i.getIncidentType(),
                    stamp(cy.detected), stamp(cy.decided),
                    cy.firstAction == null ? "—" : cy.firstAction.getType() + " · " + cy.firstAction.getAutomationMode(),
                    stamp(cy.actionStart),
                    cy.verification == null ? "—" : (cy.verification.getResult() == null ? "?" : cy.verification.getResult()),
                    stamp(cy.verification == null ? null : cy.verification.getVerifiedAt()), stamp(cy.resolved),
                    cy.state + (cy.automatic ? " (automatique)" : "")));
        }
        c.section("Cycle par incident", List.of("N°", "Incident", "Type", "Détecté", "Décision d'équipe", "Action",
                "Action le", "Vérification", "Vérifié le", "Résolu le", "Statut du cycle"), rows);

        List<List<Object>> typeRows = new ArrayList<>();
        cur.byType.entrySet().stream().sorted((a, b) -> Long.compare(b.getValue()[0], a.getValue()[0])).forEach(e -> {
            long[] n = e.getValue();
            typeRows.add(row(e.getKey().name(), n[0], n[1], n[2], ReportContent.round1((double) n[3] / n[0])));
        });
        c.section("Par type d'action", List.of("Type d'action", "Lancées", "Réussies", "En échec", "Tentatives moyennes"), typeRows);

        List<List<Object>> stateRows = new ArrayList<>();
        cur.byState.forEach((s, n) -> stateRows.add(row(s, n)));
        c.section("Où s'arrêtent les cycles", List.of("Étape", "Incidents"), stateRows);

        c.chart("bar", "Durée moyenne de chaque phase (min)",
                List.of("Détection → action", "Action → vérification", "Vérification → résolution"),
                Map.of("Minutes", List.of(cur.avgDetectToAction, cur.avgActionToVerification, cur.avgVerificationToResolution)));
        c.chart("doughnut", "Cycles complets et étape de blocage", new ArrayList<>(cur.byState.keySet()),
                Map.of("Incidents", new ArrayList<>(cur.byState.values())));
        return c;
    }

    // ── Outils ────────────────────────────────────────────────────────────────

    private static List<Object> row(Object... cells) {
        return new ArrayList<>(java.util.Arrays.asList(cells));
    }

    private static List<List<Object>> countRows(Map<String, Long> counts) {
        List<List<Object>> rows = new ArrayList<>();
        counts.forEach((k, v) -> rows.add(row(k, v)));
        return rows;
    }

    private static long sum(Map<?, Long> m) {
        return m.values().stream().mapToLong(Long::longValue).sum();
    }

    private static long sumWhere(Map<AuditAction, Long> m, Predicate<AuditAction> filter) {
        return m.entrySet().stream().filter(e -> filter.test(e.getKey())).mapToLong(Map.Entry::getValue).sum();
    }

    private static double overlapHours(LocalDateTime start, LocalDateTime end, ReportPeriod p) {
        LocalDateTime from = start.isAfter(p.from()) ? start : p.from();
        LocalDateTime to = end.isBefore(p.to()) ? end : p.to();
        if (!to.isAfter(from)) {
            return 0.0;
        }
        return Duration.between(from, to).toMinutes() / 60.0;
    }

    private void addTimeline(ReportContent c, String title, List<LocalDateTime> timestamps, ReportPeriod p) {
        boolean weekly = p.length().toDays() > 62;
        Map<String, Long> buckets = new LinkedHashMap<>();
        LocalDate cursor = p.from().toLocalDate();
        if (weekly) {
            cursor = cursor.with(DayOfWeek.MONDAY);
        }
        LocalDate last = p.to().minusNanos(1).toLocalDate();
        while (!cursor.isAfter(last)) {
            buckets.put(cursor.toString(), 0L);
            cursor = weekly ? cursor.plusWeeks(1) : cursor.plusDays(1);
        }
        for (LocalDateTime t : timestamps) {
            LocalDate d = t.toLocalDate();
            if (weekly) {
                d = d.with(DayOfWeek.MONDAY);
            }
            buckets.merge(d.toString(), 1L, Long::sum);
        }
        c.chart("line", title + (weekly ? " (par semaine)" : ""), new ArrayList<>(buckets.keySet()),
                Map.of("Activités", new ArrayList<>(buckets.values())));
    }
}
