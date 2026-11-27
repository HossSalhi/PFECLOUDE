package com.orange.pfe.backend.service.team;

import com.orange.pfe.backend.dto.org.UserRef;
import com.orange.pfe.backend.dto.team.TaskFilter;
import com.orange.pfe.backend.dto.team.TeamRequest;
import com.orange.pfe.backend.dto.team.TeamResponses.MemberTaskStats;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamMemberResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamSummaryResponse;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.team.TeamMember;
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
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;

/**
 * Gestion des équipes et de leurs membres. Réutilise l'entité utilisateur existante ({@link AppUser}) : le
 * responsable et les membres sont des utilisateurs HELIOS, jamais une table parallèle.
 *
 * <p>Règles : nom unique (insensible à la casse) ; le responsable est toujours membre ; pas de doublon de membre ;
 * on n'ajoute pas de membre à une équipe désactivée ; le responsable ne peut pas être retiré ; un membre qui a
 * encore des tâches ouvertes dans l'équipe ne peut pas être retiré ; une équipe qui a des tâches ne peut pas être
 * supprimée (on la désactive). Chaque modification est tracée dans le journal d'audit, dans la même transaction.</p>
 */
@Service
public class TeamService {

    static final int TASK_SCAN_LIMIT = 10_000;

    private final TeamRepository teamRepository;
    private final TeamMemberRepository memberRepository;
    private final TeamTaskRepository taskRepository;
    private final TeamTaskSearchRepository taskSearchRepository;
    private final AppUserRepository userRepository;
    private final OrgAccessService access;
    private final AuditTrailService audit;

    public TeamService(TeamRepository teamRepository,
                       TeamMemberRepository memberRepository,
                       TeamTaskRepository taskRepository,
                       TeamTaskSearchRepository taskSearchRepository,
                       AppUserRepository userRepository,
                       OrgAccessService access,
                       AuditTrailService audit) {
        this.teamRepository = teamRepository;
        this.memberRepository = memberRepository;
        this.taskRepository = taskRepository;
        this.taskSearchRepository = taskSearchRepository;
        this.userRepository = userRepository;
        this.access = access;
        this.audit = audit;
    }

    // ── Lecture ───────────────────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public List<TeamResponse> list(AppUser actor, boolean includeInactive, String q) {
        access.require(actor, OrgPermission.TEAMS_VIEW);
        List<Team> teams = canSeeAll(actor)
                ? teamRepository.findAllByOrderByNameAsc()
                : teamRepository.findVisibleToUser(actor.getId());
        String needle = q == null ? "" : q.trim().toLowerCase(Locale.ROOT);
        List<Team> filtered = teams.stream()
                .filter(t -> includeInactive || t.isActive())
                .filter(t -> needle.isEmpty() || t.getNameKey().contains(needle))
                .toList();
        return toResponses(filtered);
    }

    @Transactional(readOnly = true)
    public TeamResponse get(AppUser actor, Long id) {
        access.require(actor, OrgPermission.TEAMS_VIEW);
        Team team = getOrThrow(id);
        requireVisible(actor, team);
        return toResponses(List.of(team)).get(0);
    }

    @Transactional(readOnly = true)
    public List<TeamMemberResponse> members(AppUser actor, Long teamId) {
        access.require(actor, OrgPermission.TEAMS_VIEW);
        Team team = getOrThrow(teamId);
        requireVisible(actor, team);
        return memberRepository.findByTeamIdWithUser(teamId).stream()
                .map(m -> toMemberResponse(m, team))
                .toList();
    }

    @Transactional(readOnly = true)
    public TeamSummaryResponse summary(AppUser actor, Long teamId) {
        access.require(actor, OrgPermission.TEAMS_VIEW);
        Team team = getOrThrow(teamId);
        requireVisible(actor, team);
        List<TeamTask> tasks = taskSearchRepository.findAllMatching(TaskFilter.forTeam(teamId), TASK_SCAN_LIMIT);
        LocalDate today = LocalDate.now();
        List<MemberTaskStats> byMember = TaskStats.byMember(tasks, today);
        return new TeamSummaryResponse(team.getId(), team.getName(), TaskStats.summarize(tasks, today), byMember);
    }

    // ── Équipes ───────────────────────────────────────────────────────────────

    @Transactional
    public TeamResponse create(AppUser actor, TeamRequest request) {
        access.require(actor, OrgPermission.TEAMS_CREATE);
        String name = requireName(request.name());
        if (teamRepository.existsByNameKey(Team.keyOf(name))) {
            throw new IllegalStateException("Une équipe porte déjà le nom « " + name + " »");
        }
        AppUser lead = request.leadId() == null ? null : requireEnabledUser(request.leadId(), "responsable");

        Team team = new Team();
        team.setName(name);
        team.setDescription(blankToNull(request.description()));
        team.setSpecialty(request.specialty());
        team.setLead(lead);
        team.setCreatedBy(actor);
        team.setCreatedByUsername(actor.getUsername());
        team = saveUnique(team);
        if (lead != null) {
            memberRepository.save(new TeamMember(team, lead, actor.getUsername()));
        }

        audit.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.CREATE).actor(actor)
                .object("Team", team.getId())
                .summary("Création de l'équipe « " + name + " »")
                .newValues(values(team)));
        return toResponses(List.of(team)).get(0);
    }

    @Transactional
    public TeamResponse update(AppUser actor, Long id, TeamRequest request) {
        access.require(actor, OrgPermission.TEAMS_UPDATE);
        Team team = getOrThrow(id);
        String name = requireName(request.name());
        if (!Team.keyOf(name).equals(team.getNameKey())
                && teamRepository.existsByNameKeyAndIdNot(Team.keyOf(name), id)) {
            throw new IllegalStateException("Une équipe porte déjà le nom « " + name + " »");
        }
        Map<String, Object> before = values(team);
        Long oldLeadId = team.getLead() == null ? null : team.getLead().getId();
        AppUser newLead = null;
        if (request.leadId() != null) {
            newLead = request.leadId().equals(oldLeadId) ? team.getLead() : requireEnabledUser(request.leadId(), "responsable");
        }

        team.setName(name);
        team.setDescription(blankToNull(request.description()));
        team.setSpecialty(request.specialty());   // remplacement complet : null efface la spécialité
        team.setLead(newLead);
        team = saveUnique(team);
        if (newLead != null && !memberRepository.existsByTeamIdAndUserId(id, newLead.getId())) {
            memberRepository.save(new TeamMember(team, newLead, actor.getUsername()));
        }

        audit.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.UPDATE).actor(actor)
                .object("Team", id)
                .summary("Modification de l'équipe « " + name + " »")
                .oldValues(before).newValues(values(team)));
        Long newLeadId = newLead == null ? null : newLead.getId();
        if (!Objects.equals(oldLeadId, newLeadId)) {
            audit.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.LEAD_CHANGE).actor(actor)
                    .object("Team", id)
                    .summary("Nouveau responsable de l'équipe « " + name + " »")
                    .oldValues(single("lead", before.get("lead")))
                    .newValues(single("lead", newLead == null ? null : newLead.getUsername())));
        }
        return toResponses(List.of(team)).get(0);
    }

    @Transactional
    public TeamResponse changeLead(AppUser actor, Long teamId, Long userId) {
        access.require(actor, OrgPermission.TEAMS_UPDATE);
        Team team = getOrThrow(teamId);
        AppUser newLead = requireEnabledUser(userId, "responsable");
        String oldLead = team.getLead() == null ? null : team.getLead().getUsername();
        team.setLead(newLead);
        teamRepository.save(team);
        if (!memberRepository.existsByTeamIdAndUserId(teamId, userId)) {
            memberRepository.save(new TeamMember(team, newLead, actor.getUsername()));
        }
        audit.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.LEAD_CHANGE).actor(actor)
                .object("Team", teamId)
                .summary("Nouveau responsable de l'équipe « " + team.getName() + " » : " + newLead.getUsername())
                .oldValues(single("lead", oldLead)).newValues(single("lead", newLead.getUsername())));
        return toResponses(List.of(team)).get(0);
    }

    @Transactional
    public TeamResponse deactivate(AppUser actor, Long id) {
        access.require(actor, OrgPermission.TEAMS_DEACTIVATE);
        Team team = getOrThrow(id);
        if (!team.isActive()) {
            throw new IllegalStateException("L'équipe est déjà désactivée");
        }
        team.setActive(false);
        teamRepository.save(team);
        audit.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.DEACTIVATE).actor(actor)
                .object("Team", id)
                .summary("Désactivation de l'équipe « " + team.getName() + " »")
                .oldValues(single("active", true)).newValues(single("active", false)));
        return toResponses(List.of(team)).get(0);
    }

    @Transactional
    public TeamResponse activate(AppUser actor, Long id) {
        access.require(actor, OrgPermission.TEAMS_DEACTIVATE);
        Team team = getOrThrow(id);
        if (team.isActive()) {
            throw new IllegalStateException("L'équipe est déjà active");
        }
        team.setActive(true);
        teamRepository.save(team);
        audit.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.ACTIVATE).actor(actor)
                .object("Team", id)
                .summary("Réactivation de l'équipe « " + team.getName() + " »")
                .oldValues(single("active", false)).newValues(single("active", true)));
        return toResponses(List.of(team)).get(0);
    }

    @Transactional
    public void delete(AppUser actor, Long id) {
        access.require(actor, OrgPermission.TEAMS_DELETE);
        Team team = getOrThrow(id);
        if (taskRepository.countByTeamId(id) > 0) {
            throw new IllegalStateException(
                    "L'équipe possède des tâches : désactivez-la plutôt que de la supprimer");
        }
        Map<String, Object> before = values(team);
        memberRepository.deleteByTeamId(id);
        teamRepository.delete(team);
        audit.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.DELETE).actor(actor)
                .object("Team", id)
                .summary("Suppression de l'équipe « " + team.getName() + " »")
                .oldValues(before));
    }

    // ── Membres ───────────────────────────────────────────────────────────────

    @Transactional
    public TeamMemberResponse addMember(AppUser actor, Long teamId, Long userId) {
        access.require(actor, OrgPermission.TEAMS_MANAGE_MEMBERS);
        Team team = getOrThrow(teamId);
        if (!team.isActive()) {
            throw new IllegalStateException("Impossible d'ajouter un membre à une équipe désactivée");
        }
        AppUser user = requireEnabledUser(userId, "membre");
        if (memberRepository.existsByTeamIdAndUserId(teamId, userId)) {
            throw new IllegalStateException("« " + user.getUsername() + " » est déjà membre de cette équipe");
        }
        TeamMember member;
        try {
            member = memberRepository.saveAndFlush(new TeamMember(team, user, actor.getUsername()));
        } catch (DataIntegrityViolationException e) {
            throw new IllegalStateException("« " + user.getUsername() + " » est déjà membre de cette équipe");
        }
        audit.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.MEMBER_ADD).actor(actor)
                .object("Team", teamId)
                .summary("Ajout de « " + user.getUsername() + " » à l'équipe « " + team.getName() + " »")
                .newValues(single("member", user.getUsername())));
        return toMemberResponse(member, team);
    }

    @Transactional
    public void removeMember(AppUser actor, Long teamId, Long userId) {
        access.require(actor, OrgPermission.TEAMS_MANAGE_MEMBERS);
        Team team = getOrThrow(teamId);
        TeamMember member = memberRepository.findByTeamIdAndUserId(teamId, userId)
                .orElseThrow(() -> new ResourceNotFoundException("Cet utilisateur n'est pas membre de l'équipe"));
        if (team.getLead() != null && team.getLead().getId().equals(userId)) {
            throw new IllegalStateException(
                    "Le responsable ne peut pas être retiré : désignez d'abord un autre responsable");
        }
        long open = taskRepository.countByTeamIdAndAssigneeIdAndStatusIn(teamId, userId, TaskStatus.OPEN);
        if (open > 0) {
            throw new IllegalStateException("Ce membre a encore " + open
                    + " tâche(s) ouverte(s) dans l'équipe : réaffectez-les d'abord");
        }
        String username = member.getUser().getUsername();
        memberRepository.delete(member);
        audit.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.MEMBER_REMOVE).actor(actor)
                .object("Team", teamId)
                .summary("Retrait de « " + username + " » de l'équipe « " + team.getName() + " »")
                .oldValues(single("member", username)));
    }

    // ── Utilitaires (visibles pour TeamTaskService) ───────────────────────────

    /** Gestionnaires (droit de modifier les équipes) : voient toutes les équipes ; les autres, les leurs. */
    boolean canSeeAll(AppUser actor) {
        return access.has(actor, OrgPermission.TEAMS_UPDATE);
    }

    void requireVisible(AppUser actor, Team team) {
        if (canSeeAll(actor)) {
            return;
        }
        boolean lead = team.getLead() != null && team.getLead().getId().equals(actor.getId());
        if (lead || memberRepository.existsByTeamIdAndUserId(team.getId(), actor.getId())) {
            return;
        }
        throw new AccessDeniedException("Équipe non autorisée");
    }

    Team getOrThrow(Long id) {
        return teamRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("Équipe introuvable : " + id));
    }

    AppUser requireEnabledUser(Long id, String role) {
        AppUser user = userRepository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("Utilisateur introuvable (" + role + ") : " + id));
        if (!user.isEnabled()) {
            throw new IllegalArgumentException("L'utilisateur « " + user.getUsername() + " » est désactivé");
        }
        return user;
    }

    // ── Mapping ───────────────────────────────────────────────────────────────

    private List<TeamResponse> toResponses(List<Team> teams) {
        if (teams.isEmpty()) {
            return List.of();
        }
        List<Long> ids = teams.stream().map(Team::getId).toList();
        Map<Long, Integer> memberCounts = new HashMap<>();
        for (Object[] row : memberRepository.countByTeamIds(ids)) {
            memberCounts.put((Long) row[0], ((Number) row[1]).intValue());
        }
        Map<Long, Long> openTasks = new HashMap<>();
        for (Object[] row : taskRepository.countByTeamIdsAndStatuses(ids, TaskStatus.OPEN)) {
            openTasks.put((Long) row[0], ((Number) row[1]).longValue());
        }
        return teams.stream().map(t -> new TeamResponse(
                t.getId(), t.getName(), t.getDescription(), t.getSpecialty(), UserRef.of(t.getLead()), t.isActive(),
                memberCounts.getOrDefault(t.getId(), 0), openTasks.getOrDefault(t.getId(), 0L),
                t.getCreatedBy() != null ? UserRef.of(t.getCreatedBy())
                        : (t.getCreatedByUsername() == null ? null : new UserRef(null, t.getCreatedByUsername(), t.getCreatedByUsername())),
                t.getCreatedAt(), t.getUpdatedAt())).toList();
    }

    private TeamMemberResponse toMemberResponse(TeamMember m, Team team) {
        AppUser u = m.getUser();
        boolean lead = team.getLead() != null && team.getLead().getId().equals(u.getId());
        return new TeamMemberResponse(u.getId(), u.getUsername(), UserRef.displayNameOf(u),
                u.getRole() == null ? null : u.getRole().name(), u.isEnabled(), lead,
                m.getAddedAt(), m.getAddedByUsername(), RoleLabels.of(team.getSpecialty(), lead));
    }

    private static Map<String, Object> values(Team t) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("name", t.getName());
        m.put("description", t.getDescription());
        m.put("specialty", t.getSpecialty() == null ? null : t.getSpecialty().name());
        m.put("lead", t.getLead() == null ? null : t.getLead().getUsername());
        m.put("active", t.isActive());
        return m;
    }

    private static Map<String, Object> single(String key, Object value) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put(key, value);
        return m;
    }

    private Team saveUnique(Team team) {
        try {
            return teamRepository.saveAndFlush(team);
        } catch (DataIntegrityViolationException e) {
            throw new IllegalStateException("Une équipe porte déjà le nom « " + team.getName() + " »");
        }
    }

    private static String requireName(String name) {
        if (name == null || name.isBlank()) {
            throw new IllegalArgumentException("Le nom de l'équipe est obligatoire");
        }
        return name.trim();
    }

    private static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s.trim();
    }
}
