package com.orange.pfe.backend.service.team;

import com.orange.pfe.backend.dto.org.UserRef;
import com.orange.pfe.backend.dto.team.TaskFilter;
import com.orange.pfe.backend.dto.team.TeamRequests.TaskRequest;
import com.orange.pfe.backend.dto.team.TeamResponses.TaskResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TaskSummaryResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamRef;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.audit.AuditResult;
import com.orange.pfe.backend.model.team.TaskPriority;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.team.TeamTask;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.repository.TeamMemberRepository;
import com.orange.pfe.backend.repository.TeamRepository;
import com.orange.pfe.backend.repository.TeamTaskRepository;
import com.orange.pfe.backend.repository.TeamTaskSearchRepository;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import com.orange.pfe.backend.service.org.OrgAccessService;
import com.orange.pfe.backend.service.org.OrgPermission;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Gestion des tâches organisationnelles affectées aux membres des équipes.
 *
 * <p>Ne duplique PAS la gestion des incidents : ces tâches sont des travaux organisationnels ou techniques
 * (table {@code team_task}), distincts des tâches d'approbation d'incidents ({@code human_task}, API {@code /api/tasks}).</p>
 *
 * <p>Droits : créer / modifier / affecter = gestionnaires ; changer le statut = gestionnaires, ou le responsable
 * de la tâche lui-même (sans pouvoir annuler ni rouvrir). Un utilisateur ordinaire ne voit que les tâches qui lui
 * sont affectées, qu'il a créées, ou celles de ses équipes.</p>
 */
@Service
public class TeamTaskService {

    private static final Logger log = LoggerFactory.getLogger(TeamTaskService.class);
    static final int MAX_PAGE_SIZE = 100;

    private final TeamTaskRepository taskRepository;
    private final TeamTaskSearchRepository searchRepository;
    private final TeamRepository teamRepository;
    private final TeamMemberRepository memberRepository;
    private final AppUserRepository userRepository;
    private final OrgAccessService access;
    private final AuditTrailService audit;

    public TeamTaskService(TeamTaskRepository taskRepository,
                           TeamTaskSearchRepository searchRepository,
                           TeamRepository teamRepository,
                           TeamMemberRepository memberRepository,
                           AppUserRepository userRepository,
                           OrgAccessService access,
                           AuditTrailService audit) {
        this.taskRepository = taskRepository;
        this.searchRepository = searchRepository;
        this.teamRepository = teamRepository;
        this.memberRepository = memberRepository;
        this.userRepository = userRepository;
        this.access = access;
        this.audit = audit;
    }

    /** Critères de la liste, tels que reçus de l'API. */
    public record Query(Long teamId, Long assigneeId, TaskStatus status, TaskPriority priority,
                        boolean overdueOnly, boolean mine, String text) {}

    // ── Lecture ───────────────────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public Page<TaskResponse> search(AppUser actor, Query query, int page, int size) {
        access.require(actor, OrgPermission.TASKS_VIEW);
        TaskFilter filter = filterFor(actor, query);
        int safeSize = Math.max(1, Math.min(size, MAX_PAGE_SIZE));
        return searchRepository.search(filter, PageRequest.of(Math.max(0, page), safeSize)).map(this::toResponse);
    }

    @Transactional(readOnly = true)
    public TaskResponse get(AppUser actor, Long id) {
        access.require(actor, OrgPermission.TASKS_VIEW);
        TeamTask task = getOrThrow(id);
        requireVisible(actor, task);
        return toResponse(task);
    }

    @Transactional(readOnly = true)
    public TaskSummaryResponse summary(AppUser actor, Long teamId, boolean mine) {
        access.require(actor, OrgPermission.TASKS_VIEW);
        Query q = new Query(teamId, null, null, null, false, mine, null);
        List<TeamTask> tasks = searchRepository.findAllMatching(filterFor(actor, q), TeamService.TASK_SCAN_LIMIT);
        return TaskStats.summarize(tasks, LocalDate.now());
    }

    // ── Écriture ──────────────────────────────────────────────────────────────

    @Transactional
    public TaskResponse create(AppUser actor, TaskRequest request) {
        access.require(actor, OrgPermission.TASKS_CREATE);
        String title = requireTitle(request.title());
        Team team = request.teamId() == null ? null : requireActiveTeam(request.teamId());
        AppUser assignee = request.assigneeId() == null ? null : requireEnabledUser(request.assigneeId());
        requireMemberOfTeam(team, assignee);
        if (request.dueDate() != null && request.dueDate().isBefore(LocalDate.now())) {
            throw new IllegalArgumentException("La date limite ne peut pas être dans le passé");
        }

        TeamTask task = new TeamTask();
        task.setTitle(title);
        task.setDescription(blankToNull(request.description()));
        task.setTeam(team);
        task.setAssignee(assignee);
        task.setPriority(request.priority() == null ? TaskPriority.MEDIUM : request.priority());
        task.setStatus(TaskStatus.TODO);
        task.setDueDate(request.dueDate());
        task.setCreatedBy(actor);
        task.setCreatedByUsername(actor.getUsername());
        task = taskRepository.save(task);

        audit.record(AuditEvent.of(AuditModule.TASKS, AuditAction.CREATE).actor(actor)
                .object("TeamTask", task.getId())
                .summary("Création de la tâche « " + title + " »")
                .newValues(values(task)));
        return toResponse(task);
    }

    @Transactional
    public TaskResponse update(AppUser actor, Long id, TaskRequest request) {
        access.require(actor, OrgPermission.TASKS_UPDATE);
        TeamTask task = getOrThrow(id);
        requireOpen(task);
        String title = requireTitle(request.title());
        Team team = request.teamId() == null ? null : resolveTeamForUpdate(task, request.teamId());
        AppUser assignee = request.assigneeId() == null ? null : requireEnabledUser(request.assigneeId());
        requireMemberOfTeam(team, assignee);

        Map<String, Object> before = values(task);
        task.setTitle(title);
        task.setDescription(blankToNull(request.description()));
        task.setTeam(team);
        task.setAssignee(assignee);
        task.setPriority(request.priority() == null ? task.getPriority() : request.priority());
        task.setDueDate(request.dueDate());
        task = taskRepository.save(task);

        audit.record(AuditEvent.of(AuditModule.TASKS, AuditAction.UPDATE).actor(actor)
                .object("TeamTask", id)
                .summary("Modification de la tâche « " + title + " »")
                .oldValues(before).newValues(values(task)));
        return toResponse(task);
    }

    @Transactional
    public TaskResponse assign(AppUser actor, Long id, Long assigneeId) {
        access.require(actor, OrgPermission.TASKS_ASSIGN);
        TeamTask task = getOrThrow(id);
        requireOpen(task);
        AppUser assignee = assigneeId == null ? null : requireEnabledUser(assigneeId);
        requireMemberOfTeam(task.getTeam(), assignee);

        String oldAssignee = task.getAssignee() == null ? null : task.getAssignee().getUsername();
        task.setAssignee(assignee);
        task = taskRepository.save(task);

        audit.record(AuditEvent.of(AuditModule.TASKS, AuditAction.ASSIGN).actor(actor)
                .object("TeamTask", id)
                .summary("Affectation de la tâche « " + task.getTitle() + " »")
                .oldValues(single("assignee", oldAssignee))
                .newValues(single("assignee", assignee == null ? null : assignee.getUsername())));
        return toResponse(task);
    }

    @Transactional
    public TaskResponse changeStatus(AppUser actor, Long id, TaskStatus newStatus) {
        access.requireUser(actor);
        TeamTask task = getOrThrow(id);
        boolean fullRights = access.has(actor, OrgPermission.TASKS_UPDATE);
        if (!fullRights) {
            access.require(actor, OrgPermission.TASKS_UPDATE_OWN_STATUS);
            boolean own = task.getAssignee() != null && task.getAssignee().getId().equals(actor.getId());
            if (!own) {
                recordDenied(actor, task, "Seul le responsable de la tâche peut modifier son statut");
                throw new AccessDeniedException("Seul le responsable de la tâche peut modifier son statut");
            }
        }

        TaskStatus old = task.getStatus();
        if (old == newStatus) {
            throw new IllegalArgumentException("La tâche est déjà dans le statut " + newStatus);
        }
        if (!transitionAllowed(old, newStatus)) {
            throw new IllegalStateException("Transition interdite : " + old + " → " + newStatus);
        }
        if (!fullRights && (newStatus == TaskStatus.CANCELLED || old.isClosed())) {
            recordDenied(actor, task, "Annulation ou réouverture réservée aux administrateurs");
            throw new AccessDeniedException("Seul un administrateur peut annuler ou rouvrir une tâche");
        }

        task.setStatus(newStatus);
        task.setCompletedAt(newStatus == TaskStatus.DONE ? LocalDateTime.now() : null);
        task = taskRepository.save(task);

        audit.record(AuditEvent.of(AuditModule.TASKS, AuditAction.STATUS_CHANGE).actor(actor)
                .object("TeamTask", id)
                .summary("Statut de la tâche « " + task.getTitle() + " » : " + old + " → " + newStatus)
                .oldValues(single("status", old)).newValues(single("status", newStatus)));
        return toResponse(task);
    }

    // ── Règles ────────────────────────────────────────────────────────────────

    /**
     * Transitions autorisées. DONE et CANCELLED sont des états finaux, rouvrables uniquement vers IN_PROGRESS
     * (DONE) ou TODO (CANCELLED) ; la réouverture est réservée aux gestionnaires (vérifié par l'appelant).
     */
    static boolean transitionAllowed(TaskStatus from, TaskStatus to) {
        return switch (from) {
            case TODO -> to == TaskStatus.IN_PROGRESS || to == TaskStatus.ON_HOLD
                    || to == TaskStatus.DONE || to == TaskStatus.CANCELLED;
            case IN_PROGRESS -> to == TaskStatus.TODO || to == TaskStatus.ON_HOLD
                    || to == TaskStatus.DONE || to == TaskStatus.CANCELLED;
            case ON_HOLD -> to == TaskStatus.TODO || to == TaskStatus.IN_PROGRESS
                    || to == TaskStatus.DONE || to == TaskStatus.CANCELLED;
            case DONE -> to == TaskStatus.IN_PROGRESS;
            case CANCELLED -> to == TaskStatus.TODO;
        };
    }

    private void requireMemberOfTeam(Team team, AppUser assignee) {
        if (team == null || assignee == null) {
            return;
        }
        if (!memberRepository.existsByTeamIdAndUserId(team.getId(), assignee.getId())) {
            throw new IllegalArgumentException("« " + assignee.getUsername()
                    + " » n'est pas membre de l'équipe « " + team.getName() + " »");
        }
    }

    private void requireOpen(TeamTask task) {
        if (task.getStatus().isClosed()) {
            throw new IllegalStateException("Une tâche terminée ou annulée ne peut plus être modifiée : rouvrez-la d'abord");
        }
    }

    /** Conserver l'équipe actuelle même désactivée ; imposer une équipe active pour toute nouvelle équipe. */
    private Team resolveTeamForUpdate(TeamTask task, Long teamId) {
        if (task.getTeam() != null && Objects.equals(task.getTeam().getId(), teamId)) {
            return task.getTeam();
        }
        return requireActiveTeam(teamId);
    }

    private TaskFilter filterFor(AppUser actor, Query q) {
        boolean seeAll = access.has(actor, OrgPermission.TASKS_UPDATE);
        Long assignee = q.mine() ? actor.getId() : q.assigneeId();
        Long visibleTo = seeAll ? null : actor.getId();
        List<Long> visibleTeams = seeAll ? null : teamRepository.findVisibleTeamIds(actor.getId());
        return new TaskFilter(q.teamId(), assignee, q.status(), q.priority(), q.overdueOnly(), q.text(),
                visibleTo, visibleTeams);
    }

    private void requireVisible(AppUser actor, TeamTask task) {
        if (access.has(actor, OrgPermission.TASKS_UPDATE)) {
            return;
        }
        Long uid = actor.getId();
        boolean assignee = task.getAssignee() != null && task.getAssignee().getId().equals(uid);
        boolean creator = task.getCreatedBy() != null && task.getCreatedBy().getId().equals(uid);
        boolean inTeam = task.getTeam() != null && (
                (task.getTeam().getLead() != null && task.getTeam().getLead().getId().equals(uid))
                        || memberRepository.existsByTeamIdAndUserId(task.getTeam().getId(), uid));
        if (!assignee && !creator && !inTeam) {
            throw new AccessDeniedException("Tâche non autorisée");
        }
    }

    private void recordDenied(AppUser actor, TeamTask task, String reason) {
        try {
            audit.recordIndependent(AuditEvent.of(AuditModule.TASKS, AuditAction.ACCESS_DENIED).actor(actor)
                    .object("TeamTask", task.getId()).result(AuditResult.DENIED).summary(reason));
        } catch (RuntimeException e) {
            log.error("Audit: refus non tracé (tâche {}): {}", task.getId(), e.toString());
        }
    }

    // ── Accès / mapping ───────────────────────────────────────────────────────

    private TeamTask getOrThrow(Long id) {
        return taskRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("Tâche introuvable : " + id));
    }

    private Team requireActiveTeam(Long teamId) {
        Team team = teamRepository.findById(teamId)
                .orElseThrow(() -> new IllegalArgumentException("Équipe introuvable : " + teamId));
        if (!team.isActive()) {
            throw new IllegalStateException("L'équipe « " + team.getName() + " » est désactivée");
        }
        return team;
    }

    private AppUser requireEnabledUser(Long id) {
        AppUser user = userRepository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("Utilisateur introuvable : " + id));
        if (!user.isEnabled()) {
            throw new IllegalArgumentException("L'utilisateur « " + user.getUsername() + " » est désactivé");
        }
        return user;
    }

    private TaskResponse toResponse(TeamTask t) {
        TeamRef team = t.getTeam() == null ? null : new TeamRef(t.getTeam().getId(), t.getTeam().getName());
        UserRef creator = t.getCreatedBy() != null ? UserRef.of(t.getCreatedBy())
                : (t.getCreatedByUsername() == null ? null : new UserRef(null, t.getCreatedByUsername(), t.getCreatedByUsername()));
        return new TaskResponse(t.getId(), t.getTitle(), t.getDescription(), team, creator,
                UserRef.of(t.getAssignee()), t.getPriority(), t.getStatus(), t.getDueDate(),
                t.isOverdue(LocalDate.now()), t.getCompletedAt(), t.getCreatedAt(), t.getUpdatedAt(),
                t.getSourceIncidentId(), t.getSourceEvent());
    }

    private static Map<String, Object> values(TeamTask t) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("title", t.getTitle());
        m.put("team", t.getTeam() == null ? null : t.getTeam().getName());
        m.put("assignee", t.getAssignee() == null ? null : t.getAssignee().getUsername());
        m.put("priority", t.getPriority());
        m.put("status", t.getStatus());
        m.put("dueDate", t.getDueDate());
        return m;
    }

    private static Map<String, Object> single(String key, Object value) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put(key, value);
        return m;
    }

    private static String requireTitle(String title) {
        if (title == null || title.isBlank()) {
            throw new IllegalArgumentException("Le titre de la tâche est obligatoire");
        }
        return title.trim();
    }

    private static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s.trim();
    }
}
