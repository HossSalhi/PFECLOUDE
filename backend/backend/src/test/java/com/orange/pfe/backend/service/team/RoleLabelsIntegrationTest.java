package com.orange.pfe.backend.service.team;

import com.orange.pfe.backend.dto.team.TeamRequest;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamMemberResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamResponse;
import com.orange.pfe.backend.dto.user.UserResponse;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.UserService;
import com.orange.pfe.backend.service.org.OrgTestSupport;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/** Badges de rôle calculés (Chef / Technicien + spécialité) : membres d'équipe et liste des utilisateurs. */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class RoleLabelsIntegrationTest {

    @Autowired private TeamService teams;
    @Autowired private UserService userService;
    @Autowired private UserRoleLabelService labels;
    @Autowired private AppUserRepository users;

    private AppUser admin;
    private AppUser chef;
    private AppUser tech;
    private AppUser nobody;
    private String tag;

    @BeforeEach
    void setUp() {
        tag = UUID.randomUUID().toString().substring(0, 8);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
        chef = OrgTestSupport.user(users, "chef-" + tag, UserRole.USER);
        tech = OrgTestSupport.user(users, "tech-" + tag, UserRole.USER);
        nobody = OrgTestSupport.user(users, "none-" + tag, UserRole.USER);
    }

    private UserResponse listed(AppUser u) {
        return userService.findAll().stream().filter(r -> r.id().equals(u.getId())).findFirst().orElseThrow();
    }

    private TeamMemberResponse member(Long teamId, AppUser u) {
        return teams.members(admin, teamId).stream().filter(m -> m.userId().equals(u.getId())).findFirst().orElseThrow();
    }

    @Test
    void labelsAreChefForTheLeadAndTechnicianForMembersWithTheSpecialty() {
        TeamResponse t = teams.create(admin, new TeamRequest("Infra-" + tag, null, chef.getId(), ResourceScope.CLUSTER));
        teams.addMember(admin, t.id(), tech.getId());

        assertThat(member(t.id(), tech).roleLabel()).isEqualTo("Technicien Cluster");
        assertThat(listed(tech).roleLabels()).containsExactly("Technicien Cluster");
        assertThat(listed(chef).roleLabels()).containsExactly("Chef Cluster");
    }

    @Test
    void theLeadWhoIsAlsoAMemberIsChefOnly() {
        TeamResponse t = teams.create(admin, new TeamRequest("Chef-membre-" + tag, null, chef.getId(), ResourceScope.VM));
        // le responsable est déjà membre de son équipe (ajout automatique) : un seul badge « Chef »

        assertThat(member(t.id(), chef).roleLabel()).isEqualTo("Chef VM");
        assertThat(listed(chef).roleLabels()).containsExactly("Chef VM");
    }

    @Test
    void aPersonInSeveralTeamsGetsOneBadgePerTeamWithoutDuplicates() {
        TeamResponse vm = teams.create(admin, new TeamRequest("VM-" + tag, null, chef.getId(), ResourceScope.VM));
        TeamResponse app = teams.create(admin, new TeamRequest("App-" + tag, null, null, ResourceScope.APPLICATION));
        TeamResponse vm2 = teams.create(admin, new TeamRequest("VM2-" + tag, null, null, ResourceScope.VM));
        teams.addMember(admin, app.id(), chef.getId());
        teams.addMember(admin, vm2.id(), chef.getId());

        assertThat(listed(chef).roleLabels()).containsExactly("Chef VM", "Technicien Application", "Technicien VM");
        assertThat(vm.id()).isNotNull();
    }

    @Test
    void aTeamWithoutSpecialtyGivesAGenericBadge() {
        TeamResponse t = teams.create(admin, new TeamRequest("Generale-" + tag, null, chef.getId(), null));
        teams.addMember(admin, t.id(), tech.getId());

        assertThat(listed(chef).roleLabels()).containsExactly("Chef d'équipe");
        assertThat(listed(tech).roleLabels()).containsExactly("Technicien");
    }

    @Test
    void aDeactivatedTeamGivesNoBadge() {
        TeamResponse t = teams.create(admin, new TeamRequest("Inactive-" + tag, null, chef.getId(), ResourceScope.SERVICE));
        teams.addMember(admin, t.id(), tech.getId());
        teams.deactivate(admin, t.id());

        assertThat(listed(chef).roleLabels()).isEmpty();
        assertThat(listed(tech).roleLabels()).isEmpty();
    }

    @Test
    void aUserWithoutTeamHasNoBadgeAndTheRoleIsUntouched() {
        assertThat(listed(nobody).roleLabels()).isEmpty();
        assertThat(listed(nobody).role()).isEqualTo(UserRole.USER);
        assertThat(labels.labelsFor(List.of())).isEmpty();
    }

    @Test
    void theBadgeNeverChangesTheHeliosRole() {
        teams.create(admin, new TeamRequest("Role-" + tag, null, chef.getId(), ResourceScope.VM));

        assertThat(listed(chef).role()).isEqualTo(UserRole.USER);
        assertThat(users.findById(chef.getId()).orElseThrow().getRole()).isEqualTo(UserRole.USER);
    }
}
