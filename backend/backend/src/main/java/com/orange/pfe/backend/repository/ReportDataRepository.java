package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditResult;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.maintenance.MaintenanceChangeType;
import com.orange.pfe.backend.model.maintenance.MaintenanceHistory;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
import com.orange.pfe.backend.model.team.TaskSourceEvent;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.TeamTask;
import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Requêtes d'agrégation LECTURE SEULE alimentant les rapports. Toutes les valeurs proviennent des tables réelles
 * (journal d'audit, incidents, actions de remédiation, tâches, utilisateurs) ; rien n'est simulé : une période
 * sans donnée produit des zéros et des tableaux vides. Période : [from, to[.
 */
@Repository
public class ReportDataRepository {

    static final int MAX_ROWS = 200_000;

    @PersistenceContext
    private EntityManager em;

    // ── Journal d'audit ───────────────────────────────────────────────────────

    /** Lignes [nom d'utilisateur (peut être null), action, nombre]. */
    @Transactional(readOnly = true)
    public List<Object[]> auditCountsByUserAndAction(LocalDateTime from, LocalDateTime to) {
        return em.createQuery("select a.actorUsername, a.action, count(a) from AuditLogEntry a "
                + "where a.occurredAt >= :from and a.occurredAt < :to group by a.actorUsername, a.action", Object[].class)
                .setParameter("from", from).setParameter("to", to).getResultList();
    }

    @Transactional(readOnly = true)
    public Map<String, Long> auditCountsByModule(LocalDateTime from, LocalDateTime to) {
        return toCountMap(em.createQuery("select a.module, count(a) from AuditLogEntry a "
                + "where a.occurredAt >= :from and a.occurredAt < :to group by a.module", Object[].class)
                .setParameter("from", from).setParameter("to", to).getResultList());
    }

    @Transactional(readOnly = true)
    public Map<String, Long> auditCountsByResult(LocalDateTime from, LocalDateTime to) {
        return toCountMap(em.createQuery("select a.result, count(a) from AuditLogEntry a "
                + "where a.occurredAt >= :from and a.occurredAt < :to group by a.result", Object[].class)
                .setParameter("from", from).setParameter("to", to).getResultList());
    }

    @Transactional(readOnly = true)
    public long auditCount(LocalDateTime from, LocalDateTime to, AuditAction action, AuditResult result) {
        return em.createQuery("select count(a) from AuditLogEntry a where a.occurredAt >= :from and a.occurredAt < :to "
                + "and a.action = :action and a.result = :result", Long.class)
                .setParameter("from", from).setParameter("to", to)
                .setParameter("action", action).setParameter("result", result).getSingleResult();
    }

    /** Horodatages des activités de la période (regroupés par jour côté Java : indépendant du SGBD). */
    @Transactional(readOnly = true)
    public List<LocalDateTime> auditTimestamps(LocalDateTime from, LocalDateTime to) {
        return em.createQuery("select a.occurredAt from AuditLogEntry a where a.occurredAt >= :from and a.occurredAt < :to "
                + "order by a.occurredAt", LocalDateTime.class)
                .setParameter("from", from).setParameter("to", to).setMaxResults(MAX_ROWS).getResultList();
    }

    // ── Incidents et remédiation ──────────────────────────────────────────────

    @Transactional(readOnly = true)
    public Map<String, Long> incidentsBySeverity(LocalDateTime from, LocalDateTime to) {
        return toCountMap(em.createQuery("select i.severity, count(i) from Incident i "
                + "where i.createdAt >= :from and i.createdAt < :to group by i.severity", Object[].class)
                .setParameter("from", from).setParameter("to", to).getResultList());
    }

    @Transactional(readOnly = true)
    public Map<String, Long> incidentsByStatus(LocalDateTime from, LocalDateTime to) {
        return toCountMap(em.createQuery("select i.status, count(i) from Incident i "
                + "where i.createdAt >= :from and i.createdAt < :to group by i.status", Object[].class)
                .setParameter("from", from).setParameter("to", to).getResultList());
    }

    /**
     * Incidents créés dans la période ET résolus : [identifiant, sévérité, création, première résolution].
     * La résolution est la première entrée d'historique au statut RESOLVED.
     */
    @Transactional(readOnly = true)
    public List<Object[]> incidentResolutionTimes(LocalDateTime from, LocalDateTime to) {
        return em.createQuery("select i.id, i.severity, i.createdAt, min(h.createdAt) from IncidentHistory h "
                + "join h.incident i where h.status = :resolved and i.createdAt >= :from and i.createdAt < :to "
                + "group by i.id, i.severity, i.createdAt", Object[].class)
                .setParameter("resolved", IncidentStatus.RESOLVED)
                .setParameter("from", from).setParameter("to", to).getResultList();
    }

    @Transactional(readOnly = true)
    public Map<String, Long> remediationActionsByStatus(LocalDateTime from, LocalDateTime to) {
        return toCountMap(em.createQuery("select a.status, count(a) from IncidentAction a "
                + "where a.createdAt >= :from and a.createdAt < :to group by a.status", Object[].class)
                .setParameter("from", from).setParameter("to", to).getResultList());
    }

    // ── Utilisateurs (état actuel) ────────────────────────────────────────────

    /** Lignes [rôle, actif, nombre]. */
    @Transactional(readOnly = true)
    public List<Object[]> usersByRoleAndEnabled() {
        return em.createQuery("select u.role, u.enabled, count(u) from AppUser u group by u.role, u.enabled", Object[].class)
                .getResultList();
    }

    // ── Tâches ────────────────────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public List<TeamTask> tasksCreatedBetween(LocalDateTime from, LocalDateTime to) {
        return em.createQuery("select t from TeamTask t left join fetch t.team left join fetch t.assignee "
                + "where t.createdAt >= :from and t.createdAt < :to", TeamTask.class)
                .setParameter("from", from).setParameter("to", to).setMaxResults(MAX_ROWS).getResultList();
    }

    @Transactional(readOnly = true)
    public long tasksCompletedBetween(LocalDateTime from, LocalDateTime to) {
        return em.createQuery("select count(t) from TeamTask t where t.status = :done "
                + "and t.completedAt >= :from and t.completedAt < :to", Long.class)
                .setParameter("done", TaskStatus.DONE)
                .setParameter("from", from).setParameter("to", to).getSingleResult();
    }

    /** Tâches encore ouvertes à l'instant présent (état actuel, indépendant de la période). */
    @Transactional(readOnly = true)
    public List<TeamTask> openTasks() {
        return em.createQuery("select t from TeamTask t left join fetch t.team left join fetch t.assignee "
                + "where t.status in :open", TeamTask.class)
                .setParameter("open", TaskStatus.OPEN).setMaxResults(MAX_ROWS).getResultList();
    }

    // ── Suspensions de maintenance et routage incident → équipe ───────────────

    /** Plafond des lignes lues pour les indicateurs de suspension et de routage (comme MAINTENANCE_SCAN_LIMIT). */
    static final int ROUTING_SCAN_LIMIT = 5_000;

    /**
     * Entrées d'historique PAUSED et RESUMED de la période, triées par maintenance puis par date. Le détail JSON de PAUSED
     * porte l'incident déclencheur ; celui de RESUMED porte l'heure de suspension.
     */
    @Transactional(readOnly = true)
    public List<MaintenanceHistory> pauseResumeHistory(LocalDateTime from, LocalDateTime to) {
        return em.createQuery("select h from MaintenanceHistory h join fetch h.maintenance m "
                + "where h.changeType in :types and h.changedAt >= :from and h.changedAt < :to "
                + "order by m.id, h.changedAt", MaintenanceHistory.class)
                .setParameter("types", java.util.List.of(MaintenanceChangeType.PAUSED, MaintenanceChangeType.RESUMED))
                .setParameter("from", from).setParameter("to", to).setMaxResults(ROUTING_SCAN_LIMIT).getResultList();
    }

    /** Maintenances suspendues à l'instant présent (état actuel). */
    @Transactional(readOnly = true)
    public long suspendedMaintenanceCount() {
        return em.createQuery("select count(m) from Maintenance m where m.status = :s", Long.class)
                .setParameter("s", MaintenanceStatus.SUSPENDED).getSingleResult();
    }

    /** Tâches « décision » du routage créées dans la période : une par incident routé pendant une maintenance. */
    @Transactional(readOnly = true)
    public List<TeamTask> routingDecisionTasks(LocalDateTime from, LocalDateTime to) {
        return em.createQuery("select t from TeamTask t where t.sourceEvent = :ev and t.createdAt >= :from "
                + "and t.createdAt < :to order by t.createdAt", TeamTask.class)
                .setParameter("ev", TaskSourceEvent.DECISION)
                .setParameter("from", from).setParameter("to", to).setMaxResults(ROUTING_SCAN_LIMIT).getResultList();
    }

    /** Parmi ces incidents, ceux dont le responsable a choisi de suspendre (une tâche de suivi existe). */
    @Transactional(readOnly = true)
    public java.util.Set<Long> incidentsWithFollowUp(java.util.Collection<Long> incidentIds) {
        java.util.Set<Long> out = new java.util.HashSet<>();
        List<Long> ids = new java.util.ArrayList<>(incidentIds);
        for (int i = 0; i < ids.size(); i += 1000) {   // lots de 1 000 : limite de paramètres des SGBD
            out.addAll(em.createQuery("select distinct t.sourceIncidentId from TeamTask t where t.sourceEvent = :ev "
                    + "and t.sourceIncidentId in :ids", Long.class)
                    .setParameter("ev", TaskSourceEvent.FOLLOW_UP)
                    .setParameter("ids", ids.subList(i, Math.min(i + 1000, ids.size()))).getResultList());
        }
        return out;
    }

    // ── Cycle d'auto-remédiation (lecture seule, bornée) ──────────────────────

    /** Incidents créés dans la période, du plus ancien au plus récent (au plus {@link #ROUTING_SCAN_LIMIT}). */
    @Transactional(readOnly = true)
    public List<com.orange.pfe.backend.model.core.Incident> incidentsCreatedBetween(LocalDateTime from, LocalDateTime to) {
        return em.createQuery("select i from Incident i where i.createdAt >= :from and i.createdAt < :to "
                + "order by i.createdAt", com.orange.pfe.backend.model.core.Incident.class)
                .setParameter("from", from).setParameter("to", to).setMaxResults(ROUTING_SCAN_LIMIT).getResultList();
    }

    /** Actions de remédiation de ces incidents, dans l'ordre de création. */
    @Transactional(readOnly = true)
    public List<com.orange.pfe.backend.model.action.IncidentAction> actionsOfIncidents(java.util.Collection<Long> incidentIds) {
        return chunked(incidentIds, ids -> em.createQuery("select a from IncidentAction a join fetch a.incident "
                + "where a.incident.id in :ids order by a.createdAt", com.orange.pfe.backend.model.action.IncidentAction.class)
                .setParameter("ids", ids).getResultList());
    }

    /** Vérifications finales de ces incidents, dans l'ordre chronologique. */
    @Transactional(readOnly = true)
    public List<com.orange.pfe.backend.model.core.IncidentVerification> verificationsOfIncidents(java.util.Collection<Long> incidentIds) {
        return chunked(incidentIds, ids -> em.createQuery("select v from IncidentVerification v where v.incidentId in :ids "
                + "order by v.verifiedAt", com.orange.pfe.backend.model.core.IncidentVerification.class)
                .setParameter("ids", ids).getResultList());
    }

    /** Première entrée d'historique au statut donné, par incident : lignes [identifiant, date]. */
    @Transactional(readOnly = true)
    public Map<Long, LocalDateTime> firstHistoryAt(java.util.Collection<Long> incidentIds, IncidentStatus status) {
        Map<Long, LocalDateTime> out = new LinkedHashMap<>();
        for (Object[] r : chunked(incidentIds, ids -> em.createQuery("select h.incident.id, min(h.createdAt) from IncidentHistory h "
                + "where h.status = :s and h.incident.id in :ids group by h.incident.id", Object[].class)
                .setParameter("s", status).setParameter("ids", ids).getResultList())) {
            out.put(((Number) r[0]).longValue(), (LocalDateTime) r[1]);
        }
        return out;
    }

    /** Incidents ayant une tâche humaine (validation, escalade) dans leur workflow. */
    @Transactional(readOnly = true)
    public java.util.Set<Long> incidentsWithHumanTask(java.util.Collection<Long> incidentIds) {
        return new java.util.HashSet<>(chunked(incidentIds, ids -> em.createQuery("select distinct w.incident.id from HumanTask t "
                + "join t.workflowInstance w where w.incident.id in :ids", Long.class).setParameter("ids", ids).getResultList()));
    }

    /** Tâches « décision » du routage de ces incidents. */
    @Transactional(readOnly = true)
    public List<TeamTask> decisionTasksOfIncidents(java.util.Collection<Long> incidentIds) {
        return chunked(incidentIds, ids -> em.createQuery("select t from TeamTask t where t.sourceEvent = :ev "
                + "and t.sourceIncidentId in :ids", TeamTask.class)
                .setParameter("ev", TaskSourceEvent.DECISION).setParameter("ids", ids).getResultList());
    }

    /** Nombre de preuves terminal disponibles (relevés sur la VM) par incident et par phase : [incident, phase, nombre]. */
    @Transactional(readOnly = true)
    public List<Object[]> terminalEvidenceCounts(java.util.Collection<Long> incidentIds) {
        return chunked(incidentIds, ids -> em.createQuery("select e.incidentId, e.phase, count(e) from IncidentTerminalEvidence e "
                + "where e.available = true and e.incidentId in :ids group by e.incidentId, e.phase", Object[].class)
                .setParameter("ids", ids).getResultList());
    }

    /** Exécute une requête par lots de 1 000 identifiants (limite de paramètres des SGBD) et regroupe les résultats. */
    private static <T> List<T> chunked(java.util.Collection<Long> ids, java.util.function.Function<List<Long>, List<T>> query) {
        List<T> out = new java.util.ArrayList<>();
        List<Long> all = new java.util.ArrayList<>(ids);
        for (int i = 0; i < all.size(); i += 1000) {
            out.addAll(query.apply(all.subList(i, Math.min(i + 1000, all.size()))));
        }
        return out;
    }

    // ── Outils ────────────────────────────────────────────────────────────────

    private static Map<String, Long> toCountMap(List<Object[]> rows) {
        Map<String, Long> out = new LinkedHashMap<>();
        for (Object[] r : rows) {
            String key = r[0] == null ? "—" : (r[0] instanceof Enum<?> e ? e.name() : String.valueOf(r[0]));
            out.merge(key, ((Number) r[1]).longValue(), Long::sum);
        }
        return out;
    }
}
