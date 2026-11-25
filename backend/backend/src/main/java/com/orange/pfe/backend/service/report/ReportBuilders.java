package com.orange.pfe.backend.service.report;

import com.orange.pfe.backend.dto.maintenance.MaintenanceFilter;
import com.orange.pfe.backend.dto.team.TeamResponses.MemberTaskStats;
import com.orange.pfe.backend.dto.team.TeamResponses.TaskSummaryResponse;
import com.orange.pfe.backend.model.audit.AuditAction;
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

    private ReportContent maintenance(ReportPeriod p, ReportPeriod prev) {
        MaintStats cur = maintStats(p);
        MaintStats old = prev == null ? null : maintStats(prev);
        ReportContent c = new ReportContent()
                .metric("Maintenances de la période", cur.list.size(), old == null ? null : old.list.size(), null)
                .metric("Terminées", cur.byStatus.get(MaintenanceStatus.COMPLETED),
                        old == null ? null : old.byStatus.get(MaintenanceStatus.COMPLETED), null)
                .metric("Annulées", cur.byStatus.get(MaintenanceStatus.CANCELLED),
                        old == null ? null : old.byStatus.get(MaintenanceStatus.CANCELLED), null)
                .metric("Heures de maintenance planifiées", cur.plannedHours, old == null ? null : old.plannedHours, "h")
                .metric("Terminées dans les temps", cur.completedOnTime, old == null ? null : old.completedOnTime, null)
                .metric("Durée réelle moyenne", cur.avgActualHours, old == null ? null : old.avgActualHours, "h");

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
        if (prev != null) {
            prevCreated = data.tasksCreatedBetween(prev.from(), prev.to()).size();
            prevCompleted = data.tasksCompletedBetween(prev.from(), prev.to());
        }
        ReportContent c = new ReportContent()
                .metric("Tâches créées", created.size(), prevCreated, null)
                .metric("Tâches terminées dans la période", completed, prevCompleted, null)
                .metric("Tâches ouvertes (état actuel)", open.size(), null, null)
                .metric("Tâches en retard (état actuel)", overdueNow, null, null)
                .metric("Taux d'achèvement des tâches créées", sum.completionRate(), null, "%");

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

    private ReportContent performance(ReportPeriod p, ReportPeriod prev) {
        Perf cur = perf(p);
        Perf old = prev == null ? null : perf(prev);
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
                .metric("Taux de réussite des actions", success, oldSuccess, "%");

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
