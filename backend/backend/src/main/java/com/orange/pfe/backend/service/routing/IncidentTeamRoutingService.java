package com.orange.pfe.backend.service.routing;

import com.orange.pfe.backend.dto.org.UserRef;
import com.orange.pfe.backend.dto.routing.RoutingDtos.Decision;
import com.orange.pfe.backend.dto.routing.RoutingDtos.DecisionRequest;
import com.orange.pfe.backend.dto.routing.RoutingDtos.RoutedMaintenance;
import com.orange.pfe.backend.dto.routing.RoutingDtos.RoutedTask;
import com.orange.pfe.backend.dto.routing.RoutingDtos.RoutedTeam;
import com.orange.pfe.backend.dto.routing.RoutingDtos.RoutingState;
import com.orange.pfe.backend.dto.routing.RoutingDtos.RoutingView;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.maintenance.Maintenance;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.team.TaskPriority;
import com.orange.pfe.backend.model.team.TaskSourceEvent;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.team.TeamTask;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.IncidentRoutingQueryRepository;
import com.orange.pfe.backend.repository.MaintenanceRepository;
import com.orange.pfe.backend.repository.TeamRepository;
import com.orange.pfe.backend.repository.TeamTaskRepository;
import com.orange.pfe.backend.service.EmailNotificationService;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import com.orange.pfe.backend.service.maintenance.MaintenanceService;
import com.orange.pfe.backend.service.org.OrgAccessService;
import com.orange.pfe.backend.service.org.OrgPermission;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Routage « incident → équipe » lorsqu'un incident survient PENDANT une maintenance en cours sur la même ressource.
 *
 * <p><b>Ce comportement est volontairement ACTIF.</b> Le reste du module Maintenance est déclaratif (il ne fait que
 * déclarer une ressource « en maintenance » sans rien piloter, garanti par {@code MaintenanceIsolationTest}). Ce service
 * fait exception, de façon explicite et bornée : il (1) notifie le responsable de l'équipe spécialisée, (2) crée des
 * tâches d'équipe automatiques, et (3) peut SUSPENDRE puis REPRENDRE une maintenance sur décision d'un responsable.
 * Il ne modifie aucun service de détection ni de remédiation : il LIT les incidents (planificateur
 * {@link IncidentRoutingScheduler}, désactivé par défaut) et n'agit que sur la maintenance et les tâches d'équipe.</p>
 *
 * <p>Déroulement : incident détecté + maintenance EN COURS sur la même ressource (démarrée avant l'incident) → équipe
 * dont la spécialité correspond ({@link ResourceScope#fromIncident}) → tâche DECISION assignée au responsable + e-mail
 * avec liens vers une page authentifiée → le responsable choisit CONTINUER ou SUSPENDRE → en cas de suspension, tâche
 * FOLLOW_UP ; à la résolution de l'incident → tâche RESUME + e-mail → le responsable reprend. Sans maintenance en
 * cours, sans équipe adaptée ou hors périmètre : AUCUNE action (comportement actuel inchangé).</p>
 *
 * <p>Autorisation : le responsable de l'équipe concernée, ou {@code MAINTENANCE_UPDATE}. Idempotence : une tâche par
 * (incident, étape), garantie en base par un index unique.</p>
 */
@Service
public class IncidentTeamRoutingService {

    private static final Logger log = LoggerFactory.getLogger(IncidentTeamRoutingService.class);

    /** Résultat du routage d'un incident. */
    public enum Outcome { ROUTED, ALREADY_ROUTED, NOT_ROUTABLE, NO_MAINTENANCE, NO_TEAM, NO_LEAD }

    private static final Set<IncidentStatus> ACTIVE_INCIDENT = EnumSet.of(IncidentStatus.NEW, IncidentStatus.OPEN,
            IncidentStatus.IN_PROGRESS, IncidentStatus.WAITING_FOR_APPROVAL, IncidentStatus.RESOLUTION_FAILED);
    private static final Set<IncidentStatus> RESOLVED_INCIDENT = EnumSet.of(IncidentStatus.RESOLVED,
            IncidentStatus.CLOSED, IncidentStatus.ARCHIVED);
    private static final String SYSTEM = "system";

    private final IncidentRoutingQueryRepository incidents;
    private final MaintenanceRepository maintenances;
    private final TeamRepository teams;
    private final TeamTaskRepository tasks;
    private final MaintenanceService maintenanceService;
    private final EmailNotificationService email;
    private final AuditTrailService audit;
    private final OrgAccessService access;

    @Value("${helios.routing.public-base-url:https://helios.orange.tn}")
    private String publicBaseUrl;

    @Value("${helios.routing.lookback-hours:24}")
    private long lookbackHours;

    public IncidentTeamRoutingService(IncidentRoutingQueryRepository incidents, MaintenanceRepository maintenances,
                                      TeamRepository teams, TeamTaskRepository tasks, MaintenanceService maintenanceService,
                                      EmailNotificationService email, AuditTrailService audit, OrgAccessService access) {
        this.incidents = incidents;
        this.maintenances = maintenances;
        this.teams = teams;
        this.tasks = tasks;
        this.maintenanceService = maintenanceService;
        this.email = email;
        this.audit = audit;
        this.access = access;
    }

    // ── Détection (appelée par le planificateur) ───────────────────────────────

    /** Incidents actifs récents qui n'ont pas encore été routés. */
    @Transactional(readOnly = true)
    public List<Long> newIncidentIds() {
        LocalDateTime since = LocalDateTime.now().minusHours(Math.max(1, lookbackHours));
        return incidents.findByStatusInAndCreatedAtAfterOrderByCreatedAtAsc(ACTIVE_INCIDENT, since).stream()
                .map(Incident::getId)
                .filter(id -> !tasks.existsBySourceIncidentIdAndSourceEvent(id, TaskSourceEvent.DECISION))
                .toList();
    }

    /** Route un incident ; sans maintenance en cours (ou hors périmètre) ne fait RIEN. Idempotent. */
    @Transactional
    public Outcome routeIncident(Long incidentId) {
        Incident incident = incidents.findById(incidentId).orElse(null);
        if (incident == null) {
            return Outcome.NOT_ROUTABLE;
        }
        if (tasks.existsBySourceIncidentIdAndSourceEvent(incidentId, TaskSourceEvent.DECISION)) {
            return Outcome.ALREADY_ROUTED;
        }
        Optional<ResourceScope> scope = ResourceScope.fromIncident(incident.getIncidentType(), incident.getNamespace(), incident.getApp());
        if (scope.isEmpty()) {
            return Outcome.NOT_ROUTABLE;
        }
        Maintenance maintenance = maintenanceInProgressOn(incident);
        if (maintenance == null) {
            return Outcome.NO_MAINTENANCE;
        }
        List<Team> candidates = teams.findAllByOrderByNameAsc().stream()
                .filter(t -> t.isActive() && t.getSpecialty() == scope.get()).toList();
        if (candidates.isEmpty()) {
            log.info("Routage incident #{} : aucune équipe active spécialisée en {}", incidentId, scope.get());
            return Outcome.NO_TEAM;
        }
        Team team = candidates.stream().filter(t -> t.getLead() != null && t.getLead().isEnabled())
                .min(Comparator.comparing(Team::getId)).orElse(null);
        if (team == null) {
            log.info("Routage incident #{} : l'équipe spécialisée en {} n'a pas de responsable actif", incidentId, scope.get());
            return Outcome.NO_LEAD;
        }
        AppUser lead = team.getLead();
        String title = "Incident #" + incidentId + " pendant la maintenance « " + maintenance.getTitle()
                + " » : continuer ou suspendre ?";
        String description = "L'incident #" + incidentId + " (« " + incident.getTitle() + " », " + incident.getIncidentType()
                + ") est survenu alors que la maintenance « " + maintenance.getTitle() + " » est en cours sur "
                + maintenance.getResourceName() + ". Choisissez de continuer ou de suspendre la maintenance : "
                + link(incidentId, null);
        createSystemTask(team, lead, TaskSourceEvent.DECISION, incident, title, description);

        notifyLeads(team, maintenance,
                "[HELIOS] Incident #" + incidentId + " pendant la maintenance « " + maintenance.getTitle() + " » — décision requise",
                decisionHtml(incident, maintenance, team));
        return Outcome.ROUTED;
    }

    /** Incidents dont une maintenance est suspendue, désormais résolus, sans tâche de reprise. */
    @Transactional(readOnly = true)
    public List<Long> resolvedIncidentIdsAwaitingResume() {
        List<Long> ids = new ArrayList<>();
        for (Maintenance m : maintenances.findByStatusAndSuspendIncidentIdIsNotNullOrderByIdAsc(MaintenanceStatus.SUSPENDED)) {
            Long incidentId = m.getSuspendIncidentId();
            boolean resolved = incidents.findById(incidentId).map(i -> RESOLVED_INCIDENT.contains(i.getStatus())).orElse(false);
            if (resolved && !tasks.existsBySourceIncidentIdAndSourceEvent(incidentId, TaskSourceEvent.RESUME)) {
                ids.add(incidentId);
            }
        }
        return ids;
    }

    /** Incident résolu : tâche RESUME + notification pour proposer la reprise. Retourne false si rien à faire. */
    @Transactional
    public boolean notifyResolution(Long incidentId) {
        Incident incident = incidents.findById(incidentId).orElse(null);
        if (incident == null || !RESOLVED_INCIDENT.contains(incident.getStatus())) {
            return false;
        }
        Maintenance maintenance = maintenances.findFirstBySuspendIncidentIdAndStatus(incidentId, MaintenanceStatus.SUSPENDED).orElse(null);
        if (maintenance == null || tasks.existsBySourceIncidentIdAndSourceEvent(incidentId, TaskSourceEvent.RESUME)) {
            return false;
        }
        Team team = routedTeam(incident);
        if (team == null || team.getLead() == null || !team.getLead().isEnabled()) {
            log.info("Reprise incident #{} : aucune équipe avec responsable actif à notifier", incidentId);
            return false;
        }
        createSystemTask(team, team.getLead(), TaskSourceEvent.RESUME, incident,
                "Incident #" + incidentId + " résolu : reprendre la maintenance « " + maintenance.getTitle() + " »",
                "L'incident #" + incidentId + " est résolu. La maintenance « " + maintenance.getTitle() + " » sur "
                        + maintenance.getResourceName() + " est suspendue : reprenez-la quand vous êtes prêt. " + link(incidentId, "resume"));
        notifyLeads(team, maintenance,
                "[HELIOS] Incident #" + incidentId + " résolu — reprendre la maintenance « " + maintenance.getTitle() + " » ?",
                resolvedHtml(incident, maintenance));
        return true;
    }

    // ── Décisions du responsable ───────────────────────────────────────────────

    /** Le responsable de l'équipe (ou {@code MAINTENANCE_UPDATE}) choisit de continuer ou de suspendre. */
    @Transactional
    public RoutingView decide(AppUser actor, Long incidentId, DecisionRequest request) {
        Incident incident = requireIncident(incidentId);
        TeamTask decisionTask = tasks.findBySourceIncidentIdAndSourceEvent(incidentId, TaskSourceEvent.DECISION)
                .orElseThrow(() -> new ResourceNotFoundException("Aucun routage pour l'incident #" + incidentId));
        requireDecider(actor, decisionTask.getTeam());
        if (!decisionTask.getStatus().isOpen()) {
            throw new IllegalStateException("La décision pour l'incident #" + incidentId + " a déjà été prise");
        }
        Maintenance maintenance = maintenanceInProgressOn(incident);
        if (maintenance == null || !maintenance.getId().equals(request.maintenanceId())) {
            throw new IllegalStateException("Cette maintenance n'est plus en cours sur la ressource de l'incident #" + incidentId);
        }
        Long teamId = decisionTask.getTeam() == null ? null : decisionTask.getTeam().getId();
        if (request.decision() == Decision.PAUSE) {
            maintenanceService.pauseAsTeamLead(actor, maintenance.getId(), incidentId, teamId,
                    "Incident #" + incidentId + " : " + incident.getTitle());
            closeTask(decisionTask, actor);
            createSystemTask(decisionTask.getTeam(), decisionTask.getAssignee(), TaskSourceEvent.FOLLOW_UP, incident,
                    "Suivre l'incident #" + incidentId + " : maintenance « " + maintenance.getTitle() + " » suspendue",
                    "La maintenance « " + maintenance.getTitle() + " » est suspendue à cause de l'incident #" + incidentId
                            + ". Suivez sa résolution ; la reprise vous sera proposée. " + link(incidentId, null));
        } else {
            closeTask(decisionTask, actor);
            Map<String, Object> values = new LinkedHashMap<>();
            values.put("decision", "CONTINUE");
            values.put("incidentId", incidentId);
            audit.record(AuditEvent.of(AuditModule.MAINTENANCE, AuditAction.STATUS_CHANGE).actor(actor)
                    .object("Maintenance", maintenance.getId())
                    .summary("Décision : continuer la maintenance « " + maintenance.getTitle() + " » malgré l'incident #" + incidentId)
                    .newValues(values));
        }
        return buildView(actor, incident);
    }

    /** Après résolution de l'incident : le responsable (ou {@code MAINTENANCE_UPDATE}) reprend la maintenance. */
    @Transactional
    public RoutingView resume(AppUser actor, Long incidentId, Long maintenanceId) {
        Incident incident = requireIncident(incidentId);
        if (!RESOLVED_INCIDENT.contains(incident.getStatus())) {
            throw new IllegalStateException("L'incident #" + incidentId + " n'est pas encore résolu");
        }
        Maintenance maintenance = maintenances.findFirstBySuspendIncidentIdAndStatus(incidentId, MaintenanceStatus.SUSPENDED)
                .filter(m -> m.getId().equals(maintenanceId))
                .orElseThrow(() -> new IllegalStateException("Aucune maintenance suspendue pour l'incident #" + incidentId));
        Team team = routedTeam(incident);
        requireDecider(actor, team);
        maintenanceService.resumeAsTeamLead(actor, maintenance.getId(), team == null ? null : team.getId());
        for (TaskSourceEvent ev : List.of(TaskSourceEvent.RESUME, TaskSourceEvent.FOLLOW_UP)) {
            tasks.findBySourceIncidentIdAndSourceEvent(incidentId, ev).filter(t -> t.getStatus().isOpen())
                    .ifPresent(t -> closeTask(t, actor));
        }
        return buildView(actor, incident);
    }

    // ── Consultation ───────────────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public RoutingView view(AppUser actor, Long incidentId) {
        access.require(actor, OrgPermission.MAINTENANCE_VIEW);
        return buildView(actor, requireIncident(incidentId));
    }

    // ── Règles internes ────────────────────────────────────────────────────────

    private RoutingView buildView(AppUser actor, Incident incident) {
        Long id = incident.getId();
        Optional<TeamTask> decision = tasks.findBySourceIncidentIdAndSourceEvent(id, TaskSourceEvent.DECISION);
        Optional<TeamTask> resumeTask = tasks.findBySourceIncidentIdAndSourceEvent(id, TaskSourceEvent.RESUME);
        Maintenance suspended = maintenances.findFirstBySuspendIncidentIdAndStatus(id, MaintenanceStatus.SUSPENDED).orElse(null);
        boolean resolved = RESOLVED_INCIDENT.contains(incident.getStatus());

        RoutingState state;
        Maintenance maintenance = null;
        if (decision.isEmpty()) {
            state = RoutingState.NONE;
        } else if (resumeTask.isPresent() && !resumeTask.get().getStatus().isOpen()) {
            state = RoutingState.RESUMED;
        } else if (suspended != null) {
            maintenance = suspended;
            state = resolved ? RoutingState.AWAITING_RESUME : RoutingState.PAUSED;
        } else if (decision.get().getStatus().isOpen()) {
            maintenance = maintenanceInProgressOn(incident);
            state = RoutingState.AWAITING_DECISION;
        } else if (tasks.existsBySourceIncidentIdAndSourceEvent(id, TaskSourceEvent.FOLLOW_UP)) {
            state = RoutingState.RESUMED;
        } else {
            state = RoutingState.CONTINUED;
        }
        Team team = decision.map(TeamTask::getTeam).orElse(null);
        boolean authorized = actor != null && (isLead(actor, team) || access.has(actor, OrgPermission.MAINTENANCE_UPDATE));
        List<RoutedTask> routedTasks = tasks.findBySourceIncidentIdOrderByIdAsc(id).stream()
                .map(t -> new RoutedTask(t.getId(), t.getTitle(), t.getSourceEvent(), t.getStatus())).toList();
        return new RoutingView(id, incident.getTitle(), incident.getIncidentType(), incident.getStatus(),
                ResourceScope.fromIncident(incident.getIncidentType(), incident.getNamespace(), incident.getApp()).orElse(null),
                team == null ? null : new RoutedTeam(team.getId(), team.getName(), team.getSpecialty(), UserRef.of(team.getLead())),
                maintenance == null ? null : new RoutedMaintenance(maintenance.getId(), maintenance.getTitle(),
                        maintenance.getResourceType().name(), maintenance.getResourceName(), maintenance.getStatus()),
                state, authorized && state == RoutingState.AWAITING_DECISION && maintenance != null,
                authorized && state == RoutingState.AWAITING_RESUME, routedTasks);
    }

    /** Maintenance EN COURS sur la ressource de l'incident, démarrée avant la survenue de l'incident. */
    private Maintenance maintenanceInProgressOn(Incident incident) {
        return maintenances.findByStatusOrderByStartAtAsc(MaintenanceStatus.IN_PROGRESS).stream()
                .filter(m -> sameResource(m, incident))
                .filter(m -> m.getActualStartAt() != null && incident.getCreatedAt() != null
                        && !m.getActualStartAt().isAfter(incident.getCreatedAt()))
                .findFirst().orElse(null);
    }

    /**
     * Même ressource : pour une VM ou un nœud, le nom de l'hôte est le champ {@code app} de l'incident (NODE et VM
     * désignent la même machine) ; pour une application, « namespace/app » ; pour un service, son nom.
     */
    static boolean sameResource(Maintenance m, Incident i) {
        String name = lower(m.getResourceName());
        String app = lower(i.getApp());
        String qualified = lower(i.getNamespace()) + "/" + app;
        return switch (m.getResourceType()) {
            case VM, NODE -> name.equals(app);
            case APPLICATION, SERVICE -> name.equals(qualified) || name.equals(app);
        };
    }

    /** Équipe déjà associée à l'incident (via ses tâches automatiques), sinon équipe spécialisée du moment. */
    private Team routedTeam(Incident incident) {
        for (TaskSourceEvent ev : List.of(TaskSourceEvent.RESUME, TaskSourceEvent.FOLLOW_UP, TaskSourceEvent.DECISION)) {
            Optional<TeamTask> t = tasks.findBySourceIncidentIdAndSourceEvent(incident.getId(), ev);
            if (t.isPresent() && t.get().getTeam() != null) {
                return t.get().getTeam();
            }
        }
        return ResourceScope.fromIncident(incident.getIncidentType(), incident.getNamespace(), incident.getApp())
                .flatMap(scope -> teams.findAllByOrderByNameAsc().stream()
                        .filter(t -> t.isActive() && t.getSpecialty() == scope && t.getLead() != null && t.getLead().isEnabled())
                        .min(Comparator.comparing(Team::getId)))
                .orElse(null);
    }

    private static boolean isLead(AppUser actor, Team team) {
        return actor != null && team != null && team.getLead() != null && team.getLead().getId().equals(actor.getId());
    }

    private void requireDecider(AppUser actor, Team team) {
        if (!isLead(actor, team)) {
            access.require(actor, OrgPermission.MAINTENANCE_UPDATE);   // refus journalisé (ACCESS_DENIED) sinon
        }
    }

    private Incident requireIncident(Long id) {
        return incidents.findById(id).orElseThrow(() -> new ResourceNotFoundException("Incident introuvable : " + id));
    }

    // ── Tâches automatiques ────────────────────────────────────────────────────

    private TeamTask createSystemTask(Team team, AppUser assignee, TaskSourceEvent event, Incident incident,
                                      String title, String description) {
        TeamTask task = new TeamTask();
        task.setTitle(title.length() > 200 ? title.substring(0, 197) + "..." : title);
        task.setDescription(description);
        task.setTeam(team);
        task.setAssignee(assignee);
        task.setPriority(priorityOf(incident.getSeverity()));
        task.setStatus(TaskStatus.TODO);
        task.setDueDate(LocalDate.now());
        task.setCreatedByUsername(SYSTEM);
        task.markFromIncident(incident.getId(), event);
        task = tasks.saveAndFlush(task);

        Map<String, Object> values = new LinkedHashMap<>();
        values.put("title", task.getTitle());
        values.put("team", team == null ? null : team.getName());
        values.put("assignee", assignee == null ? null : assignee.getUsername());
        values.put("sourceIncidentId", incident.getId());
        values.put("sourceEvent", event.name());
        audit.record(AuditEvent.of(AuditModule.TASKS, AuditAction.CREATE).actorUsername(SYSTEM)
                .object("TeamTask", task.getId())
                .summary("Création automatique de la tâche « " + task.getTitle() + " » (incident #" + incident.getId() + ")")
                .newValues(values));
        return task;
    }

    private void closeTask(TeamTask task, AppUser actor) {
        TaskStatus old = task.getStatus();
        task.setStatus(TaskStatus.DONE);
        task.setCompletedAt(LocalDateTime.now());
        tasks.save(task);
        audit.record(AuditEvent.of(AuditModule.TASKS, AuditAction.STATUS_CHANGE).actor(actor)
                .object("TeamTask", task.getId())
                .summary("Tâche automatique terminée : « " + task.getTitle() + " »")
                .oldValues(single("status", old)).newValues(single("status", TaskStatus.DONE)));
    }

    private static TaskPriority priorityOf(IncidentSeverity severity) {
        if (severity == null) {
            return TaskPriority.MEDIUM;
        }
        return switch (severity) {
            case CRITICAL -> TaskPriority.CRITICAL;
            case HIGH -> TaskPriority.HIGH;
            case MEDIUM -> TaskPriority.MEDIUM;
            default -> TaskPriority.LOW;
        };
    }

    // ── Notifications ──────────────────────────────────────────────────────────

    /** Responsable de l'équipe, et responsable de l'équipe porteuse de la maintenance s'il est différent. */
    private void notifyLeads(Team team, Maintenance maintenance, String subject, String html) {
        List<AppUser> recipients = new ArrayList<>();
        recipients.add(team.getLead());
        if (maintenance.getTeam() != null && maintenance.getTeam().getLead() != null
                && !maintenance.getTeam().getLead().getId().equals(team.getLead().getId())) {
            recipients.add(maintenance.getTeam().getLead());
        }
        for (AppUser u : recipients) {
            if (u.isEnabled() && u.getEmail() != null && !u.getEmail().isBlank()) {
                email.sendTeamNotification(u.getEmail(), subject, html);
            }
        }
    }

    private String link(Long incidentId, String action) {
        String base = publicBaseUrl == null ? "" : publicBaseUrl.replaceAll("/+$", "");
        return base + "/incidents/" + incidentId + "/maintenance" + (action == null ? "" : "?action=" + action);
    }

    private String decisionHtml(Incident incident, Maintenance maintenance, Team team) {
        Long id = incident.getId();
        return page("Incident pendant une maintenance — décision requise", "#dc3545",
                "<p>L'incident <b>#" + id + "</b> (" + esc(incident.getIncidentType()) + ") est survenu sur <b>"
                        + esc(maintenance.getResourceName()) + "</b> alors que la maintenance <b>« " + esc(maintenance.getTitle())
                        + " »</b> est en cours. En tant que responsable de l'équipe <b>" + esc(team.getName())
                        + "</b>, choisissez : continuer la maintenance ou la suspendre le temps de traiter l'incident.</p>"
                        + button(link(id, "continue"), "Continuer la maintenance", "#6c757d")
                        + button(link(id, "pause"), "Suspendre la maintenance", "#FF7900")
                        + "<p style=\"color:#666;font-size:12px\">Ces liens ouvrent HELIOS : connexion requise, aucune action n'est "
                        + "exécutée sans votre confirmation.</p>");
    }

    private String resolvedHtml(Incident incident, Maintenance maintenance) {
        Long id = incident.getId();
        return page("Incident résolu — reprendre la maintenance ?", "#28a745",
                "<p>L'incident <b>#" + id + "</b> est résolu. La maintenance <b>« " + esc(maintenance.getTitle()) + " »</b> sur <b>"
                        + esc(maintenance.getResourceName()) + "</b> est suspendue.</p>"
                        + button(link(id, "resume"), "Reprendre la maintenance", "#28a745")
                        + "<p style=\"color:#666;font-size:12px\">Connexion requise ; la reprise est effectuée à votre confirmation.</p>");
    }

    private static String page(String title, String color, String body) {
        return "<div style=\"font-family:Arial,sans-serif;max-width:640px\"><h2 style=\"color:" + color + "\">" + esc(title)
                + "</h2>" + body + "</div>";
    }

    private static String button(String href, String label, String color) {
        return "<p><a href=\"" + esc(href) + "\" style=\"background:" + color + ";color:#fff;padding:10px 18px;border-radius:4px;"
                + "text-decoration:none;font-weight:bold\">" + esc(label) + "</a></p>";
    }

    private static String esc(String s) {
        return s == null ? "" : s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }

    private static String lower(String s) {
        return s == null ? "" : s.trim().toLowerCase(Locale.ROOT);
    }

    private static Map<String, Object> single(String key, Object value) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put(key, value);
        return m;
    }
}
