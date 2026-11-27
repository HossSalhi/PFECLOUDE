package com.orange.pfe.backend.service.maintenance;

import com.orange.pfe.backend.dto.audit.AuditEntryResponse;
import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.HistoryResponse;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceRequest;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceResponse;
import com.orange.pfe.backend.dto.team.TeamRequest;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamResponse;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.audit.AuditTrailQueryService;
import com.orange.pfe.backend.service.org.OrgTestSupport;
import com.orange.pfe.backend.service.team.TeamService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Maintenance liée à une équipe, suspension (pause) et reprise : règles d'équipe, transitions, occupation de la
 * ressource pendant la suspension, permissions, historique et audit.
 */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class MaintenanceTeamSuspensionIntegrationTest {

    @Autowired private MaintenanceService service;
    @Autowired private TeamService teams;
    @Autowired private AppUserRepository users;
    @Autowired private IncidentRepository incidents;
    @Autowired private AuditTrailQueryService auditQuery;

    private AppUser admin;
    private AppUser user;
    private String tag;
    private String resource;
    private LocalDateTime base;

    @BeforeEach
    void setUp() {
        tag = UUID.randomUUID().toString().substring(0, 8);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
        user = OrgTestSupport.user(users, "usr-" + tag, UserRole.USER);
        resource = "node-" + tag;
        base = LocalDateTime.now().plusDays(2).withSecond(0).withNano(0);
    }

    // ── Équipe ────────────────────────────────────────────────────────────────

    private MaintenanceRequest request(String title, String res, Long teamId) {
        return new MaintenanceRequest(title, null, "Motif", MaintenanceResourceType.NODE, res,
                base, base.plusHours(4), null, teamId);
    }

    private TeamResponse team(String name, ResourceScope specialty) {
        return teams.create(admin, new TeamRequest(name + "-" + tag, null, admin.getId(), specialty));
    }

    private MaintenanceResponse running(Long teamId) {
        MaintenanceResponse m = service.create(admin, request("En cours", resource, teamId));
        return service.start(admin, m.id());
    }

    private Incident incident() {
        Incident incident = new Incident();
        incident.setTitle("CPU saturé " + tag);
        incident.setDescription("Saturation CPU du nœud");
        incident.setSeverity(IncidentSeverity.HIGH);
        incident.setStatus(IncidentStatus.OPEN);
        incident.setSource(IncidentSource.MANUAL);
        return incidents.saveAndFlush(incident);
    }

    @Test
    void createsAMaintenanceCarriedByATeamAndShowsItsLead() {
        TeamResponse t = team("Cluster", ResourceScope.CLUSTER);

        MaintenanceResponse m = service.create(admin, request("Avec équipe", resource, t.id()));

        assertThat(m.team()).isNotNull();
        assertThat(m.team().id()).isEqualTo(t.id());
        assertThat(m.team().name()).isEqualTo(t.name());
        assertThat(m.team().specialty()).isEqualTo(ResourceScope.CLUSTER);
        assertThat(m.team().lead().id()).isEqualTo(admin.getId());
        assertThat(service.get(admin, m.id()).team().id()).isEqualTo(t.id());
    }

    @Test
    void aMaintenanceWithoutTeamStaysValidAndHasNoTeam() {
        MaintenanceResponse legacy = service.create(admin, new MaintenanceRequest("Ancienne", null, null,
                MaintenanceResourceType.NODE, resource, base, base.plusHours(2), null));

        assertThat(legacy.team()).isNull();
        assertThat(service.get(admin, legacy.id()).team()).isNull();
        assertThat(service.start(admin, legacy.id()).status()).isEqualTo(MaintenanceStatus.IN_PROGRESS);
    }

    @Test
    void anUnspecializedTeamCanCarryAnyResourceType() {
        TeamResponse general = team("Generale", null);

        assertThat(service.create(admin, request("Generaliste", resource, general.id())).team().id()).isEqualTo(general.id());
    }

    @Test
    void rejectsAnUnknownTeam() {
        assertThatThrownBy(() -> service.create(admin, request("Equipe inconnue", resource, 999_999_999L)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("introuvable");
    }

    @Test
    void rejectsADeactivatedTeam() {
        TeamResponse t = team("Inactive", null);
        teams.deactivate(admin, t.id());

        assertThatThrownBy(() -> service.create(admin, request("Equipe inactive", resource, t.id())))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("désactivée");
    }

    @Test
    void rejectsATeamWhoseSpecialtyDoesNotMatchTheResourceType() {
        TeamResponse vmTeam = team("Vm", ResourceScope.VM);   // une ressource NODE a la portée CLUSTER

        assertThatThrownBy(() -> service.create(admin, request("Incohérente", resource, vmTeam.id())))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("spécialisée en VM").hasMessageContaining("NODE");
    }

    @Test
    void updateChangesTheTeamAndTracesItInTheHistory() {
        TeamResponse a = team("A", ResourceScope.CLUSTER);
        TeamResponse b = team("B", ResourceScope.CLUSTER);
        MaintenanceResponse m = service.create(admin, request("Change d'équipe", resource, a.id()));

        MaintenanceResponse changed = service.update(admin, m.id(), request("Change d'équipe", resource, b.id()));

        assertThat(changed.team().id()).isEqualTo(b.id());
        HistoryResponse last = service.history(admin, m.id()).get(service.history(admin, m.id()).size() - 1);
        assertThat(last.changeType()).isEqualTo("UPDATED");
        assertThat(last.details()).contains(a.name()).contains(b.name());
    }

    @Test
    void anUnchangedTeamIsKeptEvenIfItWasDeactivatedAfterwards() {
        TeamResponse t = team("Desactivee-apres", ResourceScope.CLUSTER);
        MaintenanceResponse m = service.create(admin, request("Equipe gardée", resource, t.id()));
        teams.deactivate(admin, t.id());

        MaintenanceResponse updated = service.update(admin, m.id(), new MaintenanceRequest("Equipe gardée", "Nouvelle description", "Motif",
                MaintenanceResourceType.NODE, resource, base, base.plusHours(4), null, t.id()));

        assertThat(updated.team().id()).isEqualTo(t.id());
    }

    // ── Pause / reprise ───────────────────────────────────────────────────────

    @Test
    void pauseKeepsTheTriggeringIncidentAndTheReason() {
        MaintenanceResponse m = running(null);
        Incident incident = incident();

        MaintenanceResponse paused = service.pause(admin, m.id(), incident.getId(), "Incident CPU sur la ressource");

        assertThat(paused.status()).isEqualTo(MaintenanceStatus.SUSPENDED);
        assertThat(paused.suspendIncidentId()).isEqualTo(incident.getId());
        assertThat(paused.suspendReason()).isEqualTo("Incident CPU sur la ressource");
        assertThat(paused.suspendedAt()).isNotNull();
        assertThat(service.get(admin, m.id()).suspendIncidentId()).isEqualTo(incident.getId());
    }

    @Test
    void theThreeArgumentPauseUsedByIncidentRoutingKeepsTheIncidentWithoutAReason() {
        MaintenanceResponse m = running(null);
        Incident incident = incident();

        MaintenanceResponse paused = service.pause(admin, m.id(), incident.getId());

        assertThat(paused.status()).isEqualTo(MaintenanceStatus.SUSPENDED);
        assertThat(paused.suspendIncidentId()).isEqualTo(incident.getId());
        assertThat(paused.suspendReason()).isNull();
    }

    @Test
    void resumeBringsItBackToInProgressAndClearsTheSuspension() {
        MaintenanceResponse m = running(null);
        service.pause(admin, m.id(), null, "Intervention d'urgence ailleurs");

        MaintenanceResponse resumed = service.resume(admin, m.id());

        assertThat(resumed.status()).isEqualTo(MaintenanceStatus.IN_PROGRESS);
        assertThat(resumed.suspendedAt()).isNull();
        assertThat(resumed.suspendReason()).isNull();
        assertThat(resumed.suspendIncidentId()).isNull();
    }

    @Test
    void aMaintenanceCanBePausedAndResumedSeveralTimes() {
        MaintenanceResponse m = running(null);
        for (int i = 0; i < 3; i++) {
            assertThat(service.pause(admin, m.id(), null, "pause " + i).status()).isEqualTo(MaintenanceStatus.SUSPENDED);
            assertThat(service.resume(admin, m.id()).status()).isEqualTo(MaintenanceStatus.IN_PROGRESS);
        }
        assertThat(service.complete(admin, m.id()).status()).isEqualTo(MaintenanceStatus.COMPLETED);
    }

    @Test
    void pauseRequiresAReasonOrAnIncident() {
        MaintenanceResponse m = running(null);

        assertThatThrownBy(() -> service.pause(admin, m.id(), null, "   "))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("motif");
        assertThat(service.get(admin, m.id()).status()).isEqualTo(MaintenanceStatus.IN_PROGRESS);
    }

    @Test
    void pauseRejectsAnUnknownIncident() {
        MaintenanceResponse m = running(null);

        assertThatThrownBy(() -> service.pause(admin, m.id(), 999_999_999L, null))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("Incident introuvable");
    }

    // ── Transitions invalides ─────────────────────────────────────────────────

    @Test
    void onlyAnInProgressMaintenanceCanBePaused() {
        MaintenanceResponse planned = service.create(admin, request("Planifiée", resource, null));
        assertThatThrownBy(() -> service.pause(admin, planned.id(), null, "x")).isInstanceOf(IllegalStateException.class);

        service.start(admin, planned.id());
        service.pause(admin, planned.id(), null, "x");
        assertThatThrownBy(() -> service.pause(admin, planned.id(), null, "x"))   // déjà suspendue
                .isInstanceOf(IllegalStateException.class);

        service.resume(admin, planned.id());
        service.complete(admin, planned.id());
        assertThatThrownBy(() -> service.pause(admin, planned.id(), null, "x")).isInstanceOf(IllegalStateException.class);
    }

    @Test
    void onlyASuspendedMaintenanceCanBeResumed() {
        MaintenanceResponse m = service.create(admin, request("Planifiée", resource, null));
        assertThatThrownBy(() -> service.resume(admin, m.id())).isInstanceOf(IllegalStateException.class);

        service.start(admin, m.id());
        assertThatThrownBy(() -> service.resume(admin, m.id())).isInstanceOf(IllegalStateException.class);

        service.complete(admin, m.id());
        assertThatThrownBy(() -> service.resume(admin, m.id())).isInstanceOf(IllegalStateException.class);
    }

    @Test
    void aSuspendedMaintenanceCannotBeStartedCompletedOrDeleted() {
        MaintenanceResponse m = running(null);
        service.pause(admin, m.id(), null, "Pause");
        AppUser superAdmin = OrgTestSupport.user(users, "sup-" + tag, UserRole.SUPER_ADMIN);

        assertThatThrownBy(() -> service.start(admin, m.id())).isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> service.complete(admin, m.id()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("reprise");
        assertThatThrownBy(() -> service.delete(superAdmin, m.id())).isInstanceOf(IllegalStateException.class);
        assertThat(service.get(admin, m.id()).status()).isEqualTo(MaintenanceStatus.SUSPENDED);
    }

    @Test
    void aSuspendedMaintenanceIsNotTreatedAsFinalAndCanBeCancelledWhichClearsTheSuspension() {
        MaintenanceResponse m = running(null);
        Incident incident = incident();
        service.pause(admin, m.id(), incident.getId(), "Incident");

        assertThat(MaintenanceStatus.SUSPENDED.isFinal()).isFalse();
        assertThat(MaintenanceStatus.SUSPENDED.isActive()).isTrue();
        MaintenanceResponse cancelled = service.cancel(admin, m.id(), "Abandonnée");

        assertThat(cancelled.status()).isEqualTo(MaintenanceStatus.CANCELLED);
        assertThat(cancelled.suspendedAt()).isNull();
        assertThat(cancelled.suspendIncidentId()).isNull();
        assertThat(service.history(admin, m.id()).stream().map(HistoryResponse::changeType).toList())
                .contains("PAUSED", "CANCELLED");
    }

    @Test
    void aSuspendedMaintenanceKeepsItsResourceAndStartDateLocked() {
        MaintenanceResponse m = running(null);
        service.pause(admin, m.id(), null, "Pause");

        assertThatThrownBy(() -> service.update(admin, m.id(), request("Autre ressource", "autre-" + tag, null)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("ne peuvent plus être modifiées");
    }

    // ── La ressource reste occupée ────────────────────────────────────────────

    @Test
    void aSuspendedMaintenanceStillOccupiesItsResourceAndBlocksPlanningConflicts() {
        MaintenanceResponse m = running(null);
        service.pause(admin, m.id(), null, "Pause");

        assertThatThrownBy(() -> service.create(admin, request("Concurrente", resource, null)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("Conflit de planification");

        service.resume(admin, m.id());
        assertThatThrownBy(() -> service.create(admin, request("Concurrente", resource, null)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("Conflit de planification");
    }

    @Test
    void theResourceIsFreedOnlyOnceTheSuspendedMaintenanceIsCancelled() {
        MaintenanceResponse m = running(null);
        service.pause(admin, m.id(), null, "Pause");
        service.cancel(admin, m.id(), null);

        assertThat(service.create(admin, request("Maintenant libre", resource, null)).status()).isEqualTo(MaintenanceStatus.PLANNED);
    }

    @Test
    void aSuspendedMaintenanceDoesNotAppearAmongTheInProgressOnes() {
        MaintenanceResponse m = running(null);
        assertThat(service.inProgress(admin)).extracting(MaintenanceResponse::id).contains(m.id());

        service.pause(admin, m.id(), null, "Pause");

        assertThat(service.inProgress(admin)).extracting(MaintenanceResponse::id).doesNotContain(m.id());
    }

    // ── Permissions, historique et audit ──────────────────────────────────────

    @Test
    void anOrdinaryUserCanNeitherPauseNorResume() {
        MaintenanceResponse m = running(null);

        assertThatThrownBy(() -> service.pause(user, m.id(), null, "x")).isInstanceOf(AccessDeniedException.class);
        service.pause(admin, m.id(), null, "Pause légitime");
        assertThatThrownBy(() -> service.resume(user, m.id())).isInstanceOf(AccessDeniedException.class);

        assertThat(service.get(admin, m.id()).status()).isEqualTo(MaintenanceStatus.SUSPENDED);
    }

    @Test
    void everyTransitionIsRecordedInTheHistoryInOrder() {
        MaintenanceResponse m = service.create(admin, request("Historique", resource, null));
        service.start(admin, m.id());
        service.pause(admin, m.id(), null, "Pause");
        service.resume(admin, m.id());
        service.complete(admin, m.id());

        assertThat(service.history(admin, m.id()).stream().map(HistoryResponse::changeType).toList())
                .containsExactly("CREATED", "STARTED", "PAUSED", "RESUMED", "COMPLETED");
        HistoryResponse paused = service.history(admin, m.id()).get(2);
        assertThat(paused.changedBy()).isEqualTo(admin.getUsername());
        assertThat(paused.details()).contains("SUSPENDED").contains("Pause");
    }

    @Test
    void pauseAndResumeAreTracedInTheAuditTrailWithTheIncident() {
        MaintenanceResponse m = running(null);
        Incident incident = incident();
        service.pause(admin, m.id(), incident.getId(), "Incident");
        service.resume(admin, m.id());

        List<AuditEntryResponse> entries = auditQuery.search(admin, new AuditSearchCriteria(null, null, null, null, "Maintenance",
                String.valueOf(m.id()), null, null, null, null), 0, 100).getContent();
        assertThat(entries.stream().map(e -> AuditAction.valueOf(e.action()))).contains(AuditAction.PAUSE, AuditAction.RESUME);
        AuditEntryResponse pause = entries.stream().filter(e -> "PAUSE".equals(e.action())).findFirst().orElseThrow();
        assertThat(auditQuery.get(admin, pause.id()).newValues()).contains("\"incidentId\":" + incident.getId());
        assertThat(pause.module()).isEqualTo("MAINTENANCE");
    }
}
