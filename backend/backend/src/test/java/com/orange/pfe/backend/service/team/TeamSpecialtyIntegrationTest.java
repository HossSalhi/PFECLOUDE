package com.orange.pfe.backend.service.team;

import com.orange.pfe.backend.dto.audit.AuditEntryResponse;
import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.dto.team.TeamRequest;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamResponse;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.audit.AuditTrailQueryService;
import com.orange.pfe.backend.service.org.OrgTestSupport;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/** Spécialité d'équipe : création, modification, effacement, compatibilité des appels existants et trace d'audit. */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class TeamSpecialtyIntegrationTest {

    @Autowired private TeamService teams;
    @Autowired private AppUserRepository users;
    @Autowired private AuditTrailQueryService auditQuery;

    private AppUser admin;
    private String tag;

    @BeforeEach
    void setUp() {
        tag = UUID.randomUUID().toString().substring(0, 8);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
    }

    @Test
    void createsATeamWithASpecialty() {
        TeamResponse team = teams.create(admin, new TeamRequest("Infra-" + tag, "Hôtes", null, ResourceScope.VM));

        assertThat(team.specialty()).isEqualTo(ResourceScope.VM);
        assertThat(teams.get(admin, team.id()).specialty()).isEqualTo(ResourceScope.VM);
    }

    @Test
    void existingThreeArgumentRequestStillCreatesAnUnspecializedTeam() {
        TeamResponse team = teams.create(admin, new TeamRequest("Generale-" + tag, "Sans spécialité", null));

        assertThat(team.specialty()).isNull();
    }

    @Test
    void everyScopeCanBeStoredAndReadBack() {
        for (ResourceScope scope : ResourceScope.values()) {
            TeamResponse team = teams.create(admin, new TeamRequest(scope.name() + "-" + tag, null, null, scope));
            assertThat(teams.get(admin, team.id()).specialty()).as("scope %s", scope).isEqualTo(scope);
        }
    }

    @Test
    void updateChangesThenClearsTheSpecialty() {
        TeamResponse team = teams.create(admin, new TeamRequest("Evolutive-" + tag, "d", null, ResourceScope.CLUSTER));

        TeamResponse changed = teams.update(admin, team.id(), new TeamRequest("Evolutive-" + tag, "d", null, ResourceScope.APPLICATION));
        assertThat(changed.specialty()).isEqualTo(ResourceScope.APPLICATION);

        TeamResponse cleared = teams.update(admin, team.id(), new TeamRequest("Evolutive-" + tag, "d", null, null));
        assertThat(cleared.specialty()).isNull();
        assertThat(teams.get(admin, team.id()).specialty()).isNull();
    }

    @Test
    void listExposesTheSpecialtyOfEachTeam() {
        TeamResponse team = teams.create(admin, new TeamRequest("Liste-" + tag, null, null, ResourceScope.SERVICE));

        List<TeamResponse> listed = teams.list(admin, true, null);

        assertThat(listed).filteredOn(t -> t.id().equals(team.id())).singleElement()
                .extracting(TeamResponse::specialty).isEqualTo(ResourceScope.SERVICE);
    }

    @Test
    void specialtyChangeIsTracedInTheAuditTrailWithBeforeAndAfter() {
        TeamResponse team = teams.create(admin, new TeamRequest("Audit-" + tag, null, null, ResourceScope.VM));
        teams.update(admin, team.id(), new TeamRequest("Audit-" + tag, null, null, ResourceScope.CLUSTER));

        AuditEntryResponse update = auditQuery.search(admin, new AuditSearchCriteria(null, null, null, null, "Team",
                        String.valueOf(team.id()), null, null, null, null), 0, 50)
                .getContent().stream().filter(e -> AuditAction.UPDATE.name().equals(e.action())).findFirst().orElseThrow();
        AuditEntryResponse detail = auditQuery.get(admin, update.id());

        assertThat(detail.oldValues()).contains("\"specialty\":\"VM\"");
        assertThat(detail.newValues()).contains("\"specialty\":\"CLUSTER\"");
    }
}
