package com.orange.pfe.backend.service.team;

import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.dto.team.TeamRequest;
import com.orange.pfe.backend.dto.team.TeamRequests.TaskRequest;
import com.orange.pfe.backend.dto.team.TeamResponses.TaskResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamResponse;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.team.TaskPriority;
import com.orange.pfe.backend.model.team.TaskSourceEvent;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.TeamTask;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.repository.TeamRepository;
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
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Règle : SEUL le chef de l'équipe de la tâche choisit son statut (avec repli pour une tâche sans chef). */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class TaskStatusLeadRuleIntegrationTest {

    @Autowired private TeamService teams;
    @Autowired private TeamTaskService tasks;
    @Autowired private AppUserRepository users;
    @Autowired private TeamRepository teamRepository;
    @Autowired private TeamTaskRepository taskRepository;
    @Autowired private AuditTrailQueryService auditQuery;

    private AppUser admin;
    private AppUser lead;
    private AppUser member;
    private String tag;

    @BeforeEach
    void setUp() {
        tag = UUID.randomUUID().toString().substring(0, 8);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
        lead = OrgTestSupport.user(users, "lead-" + tag, UserRole.USER);
        member = OrgTestSupport.user(users, "mem-" + tag, UserRole.USER);
    }

    private TaskResponse task(TeamResponse team, AppUser assignee) {
        return tasks.create(admin, new TaskRequest("Tâche " + tag, "Détail", team.id(),
                assignee == null ? null : assignee.getId(), TaskPriority.HIGH, LocalDate.now().plusDays(3)));
    }

    @Test
    void anAdministratorCannotChangeTheStatusAndTheRefusalIsAudited() {
        TeamResponse team = teams.create(admin, new TeamRequest("Equipe-" + tag, null, lead.getId()));
        TaskResponse t = task(team, null);

        assertThatThrownBy(() -> tasks.changeStatus(admin, t.id(), TaskStatus.IN_PROGRESS))
                .isInstanceOf(AccessDeniedException.class).hasMessageContaining("chef de l'équipe");

        assertThat(taskRepository.findById(t.id()).orElseThrow().getStatus()).isEqualTo(TaskStatus.TODO);
        List<String> actions = auditQuery.search(admin, new AuditSearchCriteria(null, null, null, null, "TeamTask",
                String.valueOf(t.id()), null, null, null, null), 0, 50).getContent().stream().map(e -> e.action()).toList();
        assertThat(actions).contains(AuditAction.ACCESS_DENIED.name());
    }

    @Test
    void theLeadOfAnotherTeamCannotChangeTheStatus() {
        TeamResponse team = teams.create(admin, new TeamRequest("A-" + tag, null, lead.getId()));
        teams.create(admin, new TeamRequest("B-" + tag, null, member.getId()));
        TaskResponse t = task(team, null);

        assertThatThrownBy(() -> tasks.changeStatus(member, t.id(), TaskStatus.IN_PROGRESS))
                .isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void aTaskWhoseTeamHasNoLeadStaysManageableByAManagerOnly() {
        TeamResponse team = teams.create(admin, new TeamRequest("Sans-chef-" + tag, null, null));
        TaskResponse t = task(team, null);

        assertThatThrownBy(() -> tasks.changeStatus(member, t.id(), TaskStatus.IN_PROGRESS))
                .isInstanceOf(AccessDeniedException.class);
        assertThat(tasks.changeStatus(admin, t.id(), TaskStatus.IN_PROGRESS).status()).isEqualTo(TaskStatus.IN_PROGRESS);
    }

    @Test
    void automaticRoutingTasksFollowTheSameRule() {
        TeamResponse team = teams.create(admin, new TeamRequest("Routage-" + tag, null, lead.getId()));
        TeamTask auto = new TeamTask();
        auto.setTitle("Décision incident " + tag);
        auto.setTeam(teamRepository.findById(team.id()).orElseThrow());
        auto.setAssignee(lead);
        auto.setPriority(TaskPriority.HIGH);
        auto.setStatus(TaskStatus.TODO);
        auto.setCreatedByUsername("system");
        auto.markFromIncident(987_654L, TaskSourceEvent.DECISION);
        auto = taskRepository.saveAndFlush(auto);
        Long id = auto.getId();

        assertThatThrownBy(() -> tasks.changeStatus(admin, id, TaskStatus.DONE)).isInstanceOf(AccessDeniedException.class);
        assertThat(tasks.changeStatus(lead, id, TaskStatus.DONE).status()).isEqualTo(TaskStatus.DONE);
    }

    @Test
    void theLeadCanCancelAndReopenTheirOwnTeamTasks() {
        TeamResponse team = teams.create(admin, new TeamRequest("Annul-" + tag, null, lead.getId()));
        teams.addMember(admin, team.id(), member.getId());
        TaskResponse t = task(team, member);

        assertThat(tasks.changeStatus(lead, t.id(), TaskStatus.CANCELLED).status()).isEqualTo(TaskStatus.CANCELLED);
        assertThat(tasks.changeStatus(lead, t.id(), TaskStatus.TODO).status()).isEqualTo(TaskStatus.TODO);
    }
}
