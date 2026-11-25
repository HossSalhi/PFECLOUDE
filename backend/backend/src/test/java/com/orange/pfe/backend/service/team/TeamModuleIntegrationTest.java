package com.orange.pfe.backend.service.team;

import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.dto.team.TeamRequest;
import com.orange.pfe.backend.dto.team.TeamRequests.TaskRequest;
import com.orange.pfe.backend.dto.team.TeamResponses.TaskResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamMemberResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamResponse;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.team.TaskPriority;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.TeamTask;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.repository.TeamTaskRepository;
import com.orange.pfe.backend.service.audit.AuditTrailQueryService;
import com.orange.pfe.backend.service.org.OrgTestSupport;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Module Équipes / tâches avec une vraie base : création, membres, responsable, tâches, statuts, permissions,
 * relations avec les utilisateurs existants et traçage dans le journal d'audit.
 */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class TeamModuleIntegrationTest {

    @Autowired private TeamService teams;
    @Autowired private TeamTaskService tasks;
    @Autowired private AppUserRepository users;
    @Autowired private TeamTaskRepository taskRepository;
    @Autowired private AuditTrailQueryService auditQuery;

    private AppUser superAdmin;
    private AppUser admin;
    private AppUser lead;
    private AppUser member;
    private AppUser outsider;
    private String tag;

    @BeforeEach
    void setUp() {
        tag = UUID.randomUUID().toString().substring(0, 8);
        superAdmin = OrgTestSupport.user(users, "sup-" + tag, UserRole.SUPER_ADMIN);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
        lead = OrgTestSupport.user(users, "lead-" + tag, UserRole.USER);
        member = OrgTestSupport.user(users, "mem-" + tag, UserRole.USER);
        outsider = OrgTestSupport.user(users, "out-" + tag, UserRole.USER);
    }

    private TeamResponse newTeam(String name, Long leadId) {
        return teams.create(admin, new TeamRequest(name + "-" + tag, "Description " + name, leadId));
    }

    private TaskResponse newTask(TeamResponse team, AppUser assignee, String title) {
        return tasks.create(admin, new TaskRequest(title, "Détail " + title, team == null ? null : team.id(),
                assignee == null ? null : assignee.getId(), TaskPriority.HIGH, LocalDate.now().plusDays(7)));
    }

    // ── Équipes et membres ────────────────────────────────────────────────────

    @Test
    void createsATeamWhoseLeadIsAUserAndAMember() {
        TeamResponse team = newTeam("Réseau", lead.getId());

        assertThat(team.active()).isTrue();
        assertThat(team.lead().id()).isEqualTo(lead.getId());
        assertThat(team.lead().username()).isEqualTo(lead.getUsername());
        assertThat(team.createdBy().id()).isEqualTo(admin.getId());
        assertThat(team.memberCount()).isEqualTo(1);

        List<TeamMemberResponse> members = teams.members(admin, team.id());
        assertThat(members).singleElement().satisfies(m -> {
            assertThat(m.userId()).isEqualTo(lead.getId());
            assertThat(m.lead()).isTrue();
        });
    }

    @Test
    void teamNamesAreMandatoryAndUniqueIgnoringCase() {
        newTeam("reseau", null);
        assertThatThrownBy(() -> teams.create(admin, new TeamRequest("RESEAU-" + tag.toUpperCase(Locale.ROOT), null, null)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("porte déjà le nom");
        assertThatThrownBy(() -> teams.create(admin, new TeamRequest("   ", null, null)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void onlyAuthorisedRolesManageTeams() {
        TeamResponse team = newTeam("Sécurité", lead.getId());

        assertThatThrownBy(() -> teams.create(lead, new TeamRequest("Interdit-" + tag, null, null)))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> teams.addMember(lead, team.id(), member.getId()))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> teams.delete(admin, team.id())).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> teams.deactivate(member, team.id())).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void membersCannotBeDuplicatedNorDisabledNorAddedToAnInactiveTeam() {
        TeamResponse team = newTeam("Support", lead.getId());
        teams.addMember(admin, team.id(), member.getId());

        assertThatThrownBy(() -> teams.addMember(admin, team.id(), member.getId()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("déjà membre");

        outsider.setEnabled(false);
        users.saveAndFlush(outsider);
        assertThatThrownBy(() -> teams.addMember(admin, team.id(), outsider.getId()))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("désactivé");

        teams.deactivate(admin, team.id());
        assertThatThrownBy(() -> teams.addMember(admin, team.id(), admin.getId()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("désactivée");
        assertThatThrownBy(() -> teams.deactivate(admin, team.id())).isInstanceOf(IllegalStateException.class);
        assertThat(teams.activate(admin, team.id()).active()).isTrue();
    }

    @Test
    void theLeadCannotBeRemovedAndAMemberWithOpenTasksNeitherUntilReassigned() {
        TeamResponse team = newTeam("Données", lead.getId());
        teams.addMember(admin, team.id(), member.getId());
        TaskResponse task = newTask(team, member, "Sauvegarde");

        assertThatThrownBy(() -> teams.removeMember(admin, team.id(), lead.getId()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("responsable");
        assertThatThrownBy(() -> teams.removeMember(admin, team.id(), member.getId()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("tâche");

        tasks.assign(admin, task.id(), lead.getId());
        teams.removeMember(admin, team.id(), member.getId());
        assertThat(teams.members(admin, team.id())).extracting(TeamMemberResponse::userId).containsExactly(lead.getId());
    }

    @Test
    void changingTheLeadAddsTheNewLeadAsMemberAndIsTraced() {
        TeamResponse team = newTeam("Supervision", lead.getId());

        TeamResponse updated = teams.changeLead(admin, team.id(), member.getId());

        assertThat(updated.lead().id()).isEqualTo(member.getId());
        assertThat(teams.members(admin, team.id())).extracting(TeamMemberResponse::userId)
                .containsExactlyInAnyOrder(lead.getId(), member.getId());
        assertThat(actionsOn("Team", team.id())).contains(AuditAction.LEAD_CHANGE);
    }

    @Test
    void aTeamWithTasksCanBeDeactivatedButNotDeletedAndAnEmptyOneCanBeDeletedBySuperAdminOnly() {
        TeamResponse busy = newTeam("Occupée", lead.getId());
        newTask(busy, lead, "Une tâche");
        assertThatThrownBy(() -> teams.delete(superAdmin, busy.id()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("désactivez");

        TeamResponse empty = newTeam("Vide", null);
        teams.delete(superAdmin, empty.id());
        assertThat(teams.list(admin, true, "vide-" + tag)).isEmpty();
        assertThat(actionsOn("Team", empty.id())).contains(AuditAction.CREATE, AuditAction.DELETE);
    }

    // ── Tâches ────────────────────────────────────────────────────────────────

    @Test
    void createsATaskWithCreatorAssigneePriorityAndDueDate() {
        TeamResponse team = newTeam("Projet", lead.getId());
        teams.addMember(admin, team.id(), member.getId());

        TaskResponse task = newTask(team, member, "Déployer");

        assertThat(task.status()).isEqualTo(TaskStatus.TODO);
        assertThat(task.priority()).isEqualTo(TaskPriority.HIGH);
        assertThat(task.creator().id()).isEqualTo(admin.getId());
        assertThat(task.assignee().id()).isEqualTo(member.getId());
        assertThat(task.team().id()).isEqualTo(team.id());
        assertThat(task.dueDate()).isEqualTo(LocalDate.now().plusDays(7));
        assertThat(task.overdue()).isFalse();
    }

    @Test
    void taskCreationRulesAreEnforced() {
        TeamResponse team = newTeam("Règles", lead.getId());

        assertThatThrownBy(() -> newTask(team, outsider, "Pas membre"))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("n'est pas membre");
        assertThatThrownBy(() -> tasks.create(admin, new TaskRequest("Hier", null, team.id(), lead.getId(),
                TaskPriority.LOW, LocalDate.now().minusDays(1))))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("passé");
        assertThatThrownBy(() -> tasks.create(admin, new TaskRequest(" ", null, null, null, null, null)))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> tasks.create(lead, new TaskRequest("Interdit", null, null, null, null, null)))
                .isInstanceOf(AccessDeniedException.class);

        // sans équipe, n'importe quel utilisateur actif peut être responsable ; priorité par défaut MEDIUM
        TaskResponse free = tasks.create(admin, new TaskRequest("Libre", null, null, outsider.getId(), null, null));
        assertThat(free.priority()).isEqualTo(TaskPriority.MEDIUM);
        assertThat(free.team()).isNull();
    }

    @Test
    void theAssigneeMovesItsOwnTaskForwardButCannotCancelNorTouchOthers() {
        TeamResponse team = newTeam("Statuts", lead.getId());
        teams.addMember(admin, team.id(), member.getId());
        TaskResponse task = newTask(team, member, "Corriger");

        assertThat(tasks.changeStatus(member, task.id(), TaskStatus.IN_PROGRESS).status()).isEqualTo(TaskStatus.IN_PROGRESS);
        TaskResponse done = tasks.changeStatus(member, task.id(), TaskStatus.DONE);
        assertThat(done.status()).isEqualTo(TaskStatus.DONE);
        assertThat(done.completedAt()).isNotNull();

        TaskResponse other = newTask(team, lead, "Autre tâche");
        assertThatThrownBy(() -> tasks.changeStatus(member, other.id(), TaskStatus.IN_PROGRESS))
                .isInstanceOf(AccessDeniedException.class);
        TaskResponse mine = newTask(team, member, "Annulable");
        assertThatThrownBy(() -> tasks.changeStatus(member, mine.id(), TaskStatus.CANCELLED))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> tasks.changeStatus(outsider, mine.id(), TaskStatus.IN_PROGRESS))
                .isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void anAdminCanReopenAClosedTaskAndInvalidTransitionsAreRefused() {
        TeamResponse team = newTeam("Réouverture", lead.getId());
        TaskResponse task = newTask(team, lead, "Terminer");
        tasks.changeStatus(admin, task.id(), TaskStatus.DONE);

        assertThatThrownBy(() -> tasks.changeStatus(lead, task.id(), TaskStatus.IN_PROGRESS))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> tasks.changeStatus(admin, task.id(), TaskStatus.TODO))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("Transition interdite");
        assertThatThrownBy(() -> tasks.changeStatus(admin, task.id(), TaskStatus.DONE))
                .isInstanceOf(IllegalArgumentException.class);

        TaskResponse reopened = tasks.changeStatus(admin, task.id(), TaskStatus.IN_PROGRESS);
        assertThat(reopened.status()).isEqualTo(TaskStatus.IN_PROGRESS);
        assertThat(reopened.completedAt()).isNull();
        assertThat(actionsOn("TeamTask", task.id())).contains(AuditAction.CREATE, AuditAction.STATUS_CHANGE);
    }

    @Test
    void aClosedTaskCannotBeEditedOrReassigned() {
        TeamResponse team = newTeam("Figée", lead.getId());
        TaskResponse task = newTask(team, lead, "Figer");
        tasks.changeStatus(admin, task.id(), TaskStatus.CANCELLED);

        assertThatThrownBy(() -> tasks.assign(admin, task.id(), null)).isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> tasks.update(admin, task.id(),
                new TaskRequest("Nouveau titre", null, team.id(), lead.getId(), TaskPriority.LOW, null)))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void anOrdinaryUserOnlySeesTheirTasksAndThoseOfTheirTeams() {
        TeamResponse team = newTeam("Visibilité", lead.getId());
        teams.addMember(admin, team.id(), member.getId());
        TaskResponse inTeam = newTask(team, member, "Dans l'équipe");
        TaskResponse elsewhere = newTask(null, outsider, "Ailleurs");

        var seenByMember = tasks.search(member, new TeamTaskService.Query(null, null, null, null, false, false, null), 0, 50);
        assertThat(seenByMember.getContent()).extracting(TaskResponse::id).contains(inTeam.id()).doesNotContain(elsewhere.id());

        var seenByOutsider = tasks.search(outsider, new TeamTaskService.Query(null, null, null, null, false, false, null), 0, 50);
        assertThat(seenByOutsider.getContent()).extracting(TaskResponse::id).contains(elsewhere.id()).doesNotContain(inTeam.id());

        assertThatThrownBy(() -> tasks.get(outsider, inTeam.id())).isInstanceOf(AccessDeniedException.class);
        assertThat(tasks.get(member, inTeam.id()).id()).isEqualTo(inTeam.id());
        assertThatThrownBy(() -> teams.get(outsider, team.id())).isInstanceOf(AccessDeniedException.class);
        assertThat(teams.list(member, false, null)).extracting(TeamResponse::id).contains(team.id());
        assertThat(teams.list(outsider, false, null)).extracting(TeamResponse::id).doesNotContain(team.id());

        var adminView = tasks.search(admin, new TeamTaskService.Query(null, null, null, null, false, false, null), 0, 50);
        assertThat(adminView.getContent()).extracting(TaskResponse::id).contains(inTeam.id(), elsewhere.id());
    }

    @Test
    void filtersTasksByStatusPriorityAssigneeAndText() {
        TeamResponse team = newTeam("Filtres", lead.getId());
        teams.addMember(admin, team.id(), member.getId());
        TaskResponse a = newTask(team, member, "Alpha-" + tag);
        TaskResponse b = newTask(team, lead, "Bravo-" + tag);
        tasks.changeStatus(admin, b.id(), TaskStatus.IN_PROGRESS);

        assertThat(tasks.search(admin, new TeamTaskService.Query(team.id(), null, TaskStatus.IN_PROGRESS, null, false, false, null), 0, 50)
                .getContent()).extracting(TaskResponse::id).containsExactly(b.id());
        assertThat(tasks.search(admin, new TeamTaskService.Query(team.id(), member.getId(), null, null, false, false, null), 0, 50)
                .getContent()).extracting(TaskResponse::id).containsExactly(a.id());
        assertThat(tasks.search(admin, new TeamTaskService.Query(null, null, null, null, false, false, "alpha-" + tag), 0, 50)
                .getContent()).extracting(TaskResponse::id).containsExactly(a.id());
        assertThat(tasks.search(member, new TeamTaskService.Query(null, null, null, null, false, true, null), 0, 50)
                .getContent()).extracting(TaskResponse::id).containsExactly(a.id());
    }

    @Test
    void summaryReportsRealProgressAndOverdueTasks() {
        TeamResponse team = newTeam("Avancement", lead.getId());
        teams.addMember(admin, team.id(), member.getId());
        TaskResponse done = newTask(team, member, "Finie");
        TaskResponse running = newTask(team, member, "En cours");
        TaskResponse todo = newTask(team, lead, "À faire");
        TaskResponse cancelled = newTask(team, lead, "Annulée");
        tasks.changeStatus(admin, done.id(), TaskStatus.DONE);
        tasks.changeStatus(admin, running.id(), TaskStatus.IN_PROGRESS);
        tasks.changeStatus(admin, cancelled.id(), TaskStatus.CANCELLED);

        TeamTask lateEntity = taskRepository.findById(todo.id()).orElseThrow();
        lateEntity.setDueDate(LocalDate.now().minusDays(2));
        taskRepository.saveAndFlush(lateEntity);

        var summary = teams.summary(admin, team.id());
        assertThat(summary.tasks().total()).isEqualTo(4);
        assertThat(summary.tasks().byStatus()).containsEntry("DONE", 1L).containsEntry("IN_PROGRESS", 1L)
                .containsEntry("TODO", 1L).containsEntry("CANCELLED", 1L).containsEntry("ON_HOLD", 0L);
        assertThat(summary.tasks().overdue()).isEqualTo(1);
        assertThat(summary.tasks().completionRate()).isEqualTo(33.3);   // 1 terminée sur 3 hors annulée
        assertThat(summary.members()).extracting("displayName").isNotEmpty();
        assertThat(tasks.summary(admin, team.id(), false).total()).isEqualTo(4);
    }

    @Test
    void teamAndMemberChangesAreTracedInTheAuditLog() {
        TeamResponse team = newTeam("Audit", lead.getId());
        teams.addMember(admin, team.id(), member.getId());
        teams.removeMember(admin, team.id(), member.getId());

        assertThat(actionsOn("Team", team.id()))
                .contains(AuditAction.CREATE, AuditAction.MEMBER_ADD, AuditAction.MEMBER_REMOVE);
    }

    private List<AuditAction> actionsOn(String objectType, Long id) {
        return auditQuery.search(admin, new AuditSearchCriteria(null, null, null, null, objectType, String.valueOf(id),
                        null, null, null, null), 0, 100)
                .getContent().stream().map(e -> AuditAction.valueOf(e.action())).toList();
    }
}
