package com.orange.pfe.backend.service.routing;

import com.orange.pfe.backend.dto.audit.AuditEntryResponse;
import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceRequest;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceResponse;
import com.orange.pfe.backend.dto.routing.RoutingDtos.Decision;
import com.orange.pfe.backend.dto.routing.RoutingDtos.DecisionRequest;
import com.orange.pfe.backend.dto.routing.RoutingDtos.RoutingState;
import com.orange.pfe.backend.dto.routing.RoutingDtos.RoutingView;
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
import com.orange.pfe.backend.model.team.TaskPriority;
import com.orange.pfe.backend.model.team.TaskSourceEvent;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.TeamTask;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.TeamTaskRepository;
import com.orange.pfe.backend.service.EmailNotificationService;
import com.orange.pfe.backend.service.audit.AuditTrailQueryService;
import com.orange.pfe.backend.service.maintenance.MaintenanceService;
import com.orange.pfe.backend.service.org.OrgTestSupport;
import com.orange.pfe.backend.service.routing.IncidentTeamRoutingService.Outcome;
import com.orange.pfe.backend.service.team.TeamService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

/**
 * Routage incident → équipe de bout en bout, avec une vraie base : incident pendant une maintenance → notification →
 * pause → résolution → notification de reprise → reprise, avec une tâche d'équipe créée à chaque étape, l'audit et les
 * permissions. Le responsable d'équipe est un utilisateur ordinaire (rôle USER) : il décide sans droit global.
 */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class IncidentTeamRoutingIntegrationTest {

    @Autowired private IncidentTeamRoutingService routing;
    @Autowired private MaintenanceService maintenanceService;
    @Autowired private TeamService teams;
    @Autowired private AppUserRepository users;
    @Autowired private IncidentRepository incidents;
    @Autowired private TeamTaskRepository tasks;
    @Autowired private AuditTrailQueryService auditQuery;
    @MockitoBean private EmailNotificationService email;

    private AppUser admin;
    private AppUser lead;
    private AppUser outsider;
    private String tag;
    private String node;

    @BeforeEach
    void setUp() {
        tag = UUID.randomUUID().toString().substring(0, 8);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
        lead = OrgTestSupport.user(users, "lead-" + tag, UserRole.USER);       // responsable d'équipe SANS droit global
        outsider = OrgTestSupport.user(users, "out-" + tag, UserRole.USER);
        node = "node-" + tag;
    }

    // ── Données ───────────────────────────────────────────────────────────────

    private TeamResponse vmTeam() {
        return teams.create(admin, new TeamRequest("Infra-VM-" + tag, null, lead.getId(), ResourceScope.VM));
    }

    private MaintenanceResponse runningMaintenance(MaintenanceResourceType type, String resource, Long teamId) {
        LocalDateTime start = LocalDateTime.now().plusMinutes(1).withNano(0);
        MaintenanceResponse m = maintenanceService.create(admin, new MaintenanceRequest("Patch " + tag, null, "Motif",
                type, resource, start, start.plusHours(4), null, teamId));
        return maintenanceService.start(admin, m.id());
    }

    private Incident incident(String type, String namespace, String app) {
        Incident i = new Incident();
        i.setTitle("Incident " + type + " " + tag);
        i.setDescription("Description");
        i.setSeverity(IncidentSeverity.HIGH);
        i.setStatus(IncidentStatus.OPEN);
        i.setSource(IncidentSource.MANUAL);
        i.setIncidentType(type);
        i.setNamespace(namespace);
        i.setApp(app);
        return incidents.saveAndFlush(i);
    }

    private Incident hostIncident() {
        return incident("NODE_HIGH_CPU", "k8s-nodes", node);
    }

    private void resolve(Incident incident) {
        incident.setStatus(IncidentStatus.RESOLVED);
        incidents.saveAndFlush(incident);
    }

    private TeamTask task(Incident i, TaskSourceEvent ev) {
        return tasks.findBySourceIncidentIdAndSourceEvent(i.getId(), ev).orElseThrow();
    }

    private List<AuditAction> auditActionsOn(String objectType, Long id) {
        return auditQuery.search(admin, new AuditSearchCriteria(null, null, null, null, objectType, String.valueOf(id),
                null, null, null, null), 0, 100).getContent().stream().map(e -> AuditAction.valueOf(e.action())).toList();
    }

    // ── Scénario complet ──────────────────────────────────────────────────────

    @Test
    void fullScenarioIncidentDuringMaintenancePauseResolutionAndResume() {
        TeamResponse team = vmTeam();
        MaintenanceResponse m = runningMaintenance(MaintenanceResourceType.NODE, node, null);
        Incident incident = hostIncident();

        // 1. Détection : tâche DECISION + notification au responsable
        assertThat(routing.newIncidentIds()).contains(incident.getId());
        assertThat(routing.routeIncident(incident.getId())).isEqualTo(Outcome.ROUTED);

        TeamTask decision = task(incident, TaskSourceEvent.DECISION);
        assertThat(decision.getTeam().getId()).isEqualTo(team.id());
        assertThat(decision.getAssignee().getId()).isEqualTo(lead.getId());
        assertThat(decision.getSourceIncidentId()).isEqualTo(incident.getId());
        assertThat(decision.getCreatedByUsername()).isEqualTo("system");
        assertThat(decision.getPriority()).isEqualTo(TaskPriority.HIGH);
        assertThat(decision.getStatus()).isEqualTo(TaskStatus.TODO);
        verify(email).sendTeamNotification(eq(lead.getEmail()), contains("décision requise"),
                contains("/incidents/" + incident.getId() + "/maintenance?action=pause"));
        assertThat(routing.newIncidentIds()).doesNotContain(incident.getId());

        RoutingView awaiting = routing.view(lead, incident.getId());
        assertThat(awaiting.state()).isEqualTo(RoutingState.AWAITING_DECISION);
        assertThat(awaiting.canDecide()).isTrue();
        assertThat(awaiting.maintenance().id()).isEqualTo(m.id());

        // 2. Le responsable (USER) choisit de suspendre
        RoutingView paused = routing.decide(lead, incident.getId(), new DecisionRequest(m.id(), Decision.PAUSE));
        assertThat(paused.state()).isEqualTo(RoutingState.PAUSED);
        MaintenanceResponse suspended = maintenanceService.get(admin, m.id());
        assertThat(suspended.status()).isEqualTo(MaintenanceStatus.SUSPENDED);
        assertThat(suspended.suspendIncidentId()).isEqualTo(incident.getId());
        assertThat(task(incident, TaskSourceEvent.DECISION).getStatus()).isEqualTo(TaskStatus.DONE);
        TeamTask followUp = task(incident, TaskSourceEvent.FOLLOW_UP);
        assertThat(followUp.getAssignee().getId()).isEqualTo(lead.getId());
        assertThat(followUp.getStatus()).isEqualTo(TaskStatus.TODO);
        assertThat(auditActionsOn("Maintenance", m.id())).contains(AuditAction.PAUSE);
        AuditEntryResponse pauseEntry = auditQuery.search(admin, new AuditSearchCriteria(null, null, null, null, "Maintenance",
                String.valueOf(m.id()), null, null, null, null), 0, 50).getContent().stream()
                .filter(e -> "PAUSE".equals(e.action())).findFirst().orElseThrow();
        assertThat(pauseEntry.actorUsername()).isEqualTo(lead.getUsername());

        // 3. Incident pas encore résolu : aucune proposition de reprise, reprise refusée
        assertThat(routing.resolvedIncidentIdsAwaitingResume()).doesNotContain(incident.getId());
        assertThatThrownBy(() -> routing.resume(lead, incident.getId(), m.id()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("pas encore résolu");

        // 4. Résolution : tâche RESUME + notification de reprise
        resolve(incident);
        assertThat(routing.resolvedIncidentIdsAwaitingResume()).contains(incident.getId());
        assertThat(routing.notifyResolution(incident.getId())).isTrue();
        TeamTask resumeTask = task(incident, TaskSourceEvent.RESUME);
        assertThat(resumeTask.getAssignee().getId()).isEqualTo(lead.getId());
        verify(email).sendTeamNotification(eq(lead.getEmail()), contains("résolu"),
                contains("/incidents/" + incident.getId() + "/maintenance?action=resume"));
        assertThat(routing.view(lead, incident.getId()).state()).isEqualTo(RoutingState.AWAITING_RESUME);
        assertThat(routing.view(lead, incident.getId()).canResume()).isTrue();
        assertThat(routing.notifyResolution(incident.getId())).isFalse();   // idempotent

        // 5. Reprise par le responsable
        RoutingView resumed = routing.resume(lead, incident.getId(), m.id());
        assertThat(resumed.state()).isEqualTo(RoutingState.RESUMED);
        assertThat(maintenanceService.get(admin, m.id()).status()).isEqualTo(MaintenanceStatus.IN_PROGRESS);
        assertThat(maintenanceService.get(admin, m.id()).suspendIncidentId()).isNull();
        assertThat(task(incident, TaskSourceEvent.RESUME).getStatus()).isEqualTo(TaskStatus.DONE);
        assertThat(task(incident, TaskSourceEvent.FOLLOW_UP).getStatus()).isEqualTo(TaskStatus.DONE);
        assertThat(auditActionsOn("Maintenance", m.id())).contains(AuditAction.PAUSE, AuditAction.RESUME);

        // Une tâche automatique à chaque étape (décision, suivi, reprise), toutes rattachées à l'incident
        assertThat(tasks.findBySourceIncidentIdOrderByIdAsc(incident.getId()))
                .extracting(TeamTask::getSourceEvent)
                .containsExactly(TaskSourceEvent.DECISION, TaskSourceEvent.FOLLOW_UP, TaskSourceEvent.RESUME);
        assertThat(routing.view(lead, incident.getId()).tasks()).hasSize(3);
    }

    @Test
    void continuingKeepsTheMaintenanceRunningAndClosesTheDecisionTask() {
        vmTeam();
        MaintenanceResponse m = runningMaintenance(MaintenanceResourceType.NODE, node, null);
        Incident incident = hostIncident();
        routing.routeIncident(incident.getId());

        RoutingView view = routing.decide(lead, incident.getId(), new DecisionRequest(m.id(), Decision.CONTINUE));

        assertThat(view.state()).isEqualTo(RoutingState.CONTINUED);
        assertThat(maintenanceService.get(admin, m.id()).status()).isEqualTo(MaintenanceStatus.IN_PROGRESS);
        assertThat(task(incident, TaskSourceEvent.DECISION).getStatus()).isEqualTo(TaskStatus.DONE);
        assertThat(tasks.existsBySourceIncidentIdAndSourceEvent(incident.getId(), TaskSourceEvent.FOLLOW_UP)).isFalse();
        assertThat(auditActionsOn("Maintenance", m.id())).contains(AuditAction.STATUS_CHANGE).doesNotContain(AuditAction.PAUSE);
    }

    // ── Comportement inchangé quand le routage ne s'applique pas ──────────────

    @Test
    void anIncidentWithoutMaintenanceInProgressChangesNothing() {
        vmTeam();
        Incident incident = hostIncident();

        assertThat(routing.routeIncident(incident.getId())).isEqualTo(Outcome.NO_MAINTENANCE);

        assertThat(tasks.findBySourceIncidentIdOrderByIdAsc(incident.getId())).isEmpty();
        verify(email, never()).sendTeamNotification(anyString(), anyString(), anyString());
        assertThat(routing.view(lead, incident.getId()).state()).isEqualTo(RoutingState.NONE);
    }

    @Test
    void aMaintenanceStartedAfterTheIncidentDoesNotTriggerRouting() {
        vmTeam();
        Incident incident = hostIncident();
        runningMaintenance(MaintenanceResourceType.NODE, node, null);   // démarrée APRÈS l'incident

        assertThat(routing.routeIncident(incident.getId())).isEqualTo(Outcome.NO_MAINTENANCE);
        verify(email, never()).sendTeamNotification(anyString(), anyString(), anyString());
    }

    @Test
    void aMaintenanceOnAnotherResourceIsIgnored() {
        vmTeam();
        runningMaintenance(MaintenanceResourceType.NODE, "autre-" + tag, null);
        Incident incident = hostIncident();

        assertThat(routing.routeIncident(incident.getId())).isEqualTo(Outcome.NO_MAINTENANCE);
    }

    @Test
    void aSuspendedMaintenanceIsNotConsideredInProgress() {
        vmTeam();
        MaintenanceResponse m = runningMaintenance(MaintenanceResourceType.NODE, node, null);
        maintenanceService.pause(admin, m.id(), null, "Déjà suspendue");
        Incident incident = hostIncident();

        assertThat(routing.routeIncident(incident.getId())).isEqualTo(Outcome.NO_MAINTENANCE);
    }

    @Test
    void anIncidentTypeOutsideTheKnownScopesIsNotRouted() {
        vmTeam();
        runningMaintenance(MaintenanceResourceType.NODE, node, null);
        Incident incident = incident("SOMETHING_NEW", "k8s-nodes", node);

        assertThat(routing.routeIncident(incident.getId())).isEqualTo(Outcome.NOT_ROUTABLE);
    }

    @Test
    void noSpecializedTeamMeansNoRouting() {
        runningMaintenance(MaintenanceResourceType.NODE, node, null);
        Incident incident = hostIncident();

        assertThat(routing.routeIncident(incident.getId())).isEqualTo(Outcome.NO_TEAM);
        assertThat(tasks.findBySourceIncidentIdOrderByIdAsc(incident.getId())).isEmpty();
    }

    @Test
    void aDeactivatedTeamIsNeverRouted() {
        TeamResponse team = vmTeam();
        teams.deactivate(admin, team.id());
        runningMaintenance(MaintenanceResourceType.NODE, node, null);

        assertThat(routing.routeIncident(hostIncident().getId())).isEqualTo(Outcome.NO_TEAM);
    }

    @Test
    void aSpecializedTeamWithoutLeadMeansNoRouting() {
        teams.create(admin, new TeamRequest("Sans-chef-" + tag, null, null, ResourceScope.VM));
        runningMaintenance(MaintenanceResourceType.NODE, node, null);

        assertThat(routing.routeIncident(hostIncident().getId())).isEqualTo(Outcome.NO_LEAD);
    }

    // ── Idempotence ───────────────────────────────────────────────────────────

    @Test
    void routingTheSameIncidentTwiceCreatesOneTaskAndOneNotification() {
        vmTeam();
        runningMaintenance(MaintenanceResourceType.NODE, node, null);
        Incident incident = hostIncident();

        assertThat(routing.routeIncident(incident.getId())).isEqualTo(Outcome.ROUTED);
        assertThat(routing.routeIncident(incident.getId())).isEqualTo(Outcome.ALREADY_ROUTED);

        assertThat(tasks.findBySourceIncidentIdOrderByIdAsc(incident.getId())).hasSize(1);
        verify(email, times(1)).sendTeamNotification(anyString(), anyString(), anyString());
    }

    // ── Permissions ───────────────────────────────────────────────────────────

    @Test
    void aUserWhoIsNotTheLeadCannotDecideAndNothingChanges() {
        vmTeam();
        MaintenanceResponse m = runningMaintenance(MaintenanceResourceType.NODE, node, null);
        Incident incident = hostIncident();
        routing.routeIncident(incident.getId());

        assertThatThrownBy(() -> routing.decide(outsider, incident.getId(), new DecisionRequest(m.id(), Decision.PAUSE)))
                .isInstanceOf(AccessDeniedException.class);

        assertThat(maintenanceService.get(admin, m.id()).status()).isEqualTo(MaintenanceStatus.IN_PROGRESS);
        assertThat(task(incident, TaskSourceEvent.DECISION).getStatus()).isEqualTo(TaskStatus.TODO);
        assertThat(routing.view(outsider, incident.getId()).canDecide()).isFalse();
    }

    @Test
    void anAdministratorWithMaintenanceUpdateCanDecideInsteadOfTheLead() {
        vmTeam();
        MaintenanceResponse m = runningMaintenance(MaintenanceResourceType.NODE, node, null);
        Incident incident = hostIncident();
        routing.routeIncident(incident.getId());

        assertThat(routing.decide(admin, incident.getId(), new DecisionRequest(m.id(), Decision.PAUSE)).state())
                .isEqualTo(RoutingState.PAUSED);
    }

    @Test
    void aDecisionCanOnlyBeTakenOnce() {
        vmTeam();
        MaintenanceResponse m = runningMaintenance(MaintenanceResourceType.NODE, node, null);
        Incident incident = hostIncident();
        routing.routeIncident(incident.getId());
        routing.decide(lead, incident.getId(), new DecisionRequest(m.id(), Decision.CONTINUE));

        assertThatThrownBy(() -> routing.decide(lead, incident.getId(), new DecisionRequest(m.id(), Decision.PAUSE)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("déjà été prise");
    }

    @Test
    void aDecisionOnAnotherMaintenanceIsRefused() {
        vmTeam();
        runningMaintenance(MaintenanceResourceType.NODE, node, null);
        Incident incident = hostIncident();
        routing.routeIncident(incident.getId());

        assertThatThrownBy(() -> routing.decide(lead, incident.getId(), new DecisionRequest(999_999_999L, Decision.PAUSE)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("plus en cours");
    }

    @Test
    void anOutsiderCannotResumeAfterResolution() {
        vmTeam();
        MaintenanceResponse m = runningMaintenance(MaintenanceResourceType.NODE, node, null);
        Incident incident = hostIncident();
        routing.routeIncident(incident.getId());
        routing.decide(lead, incident.getId(), new DecisionRequest(m.id(), Decision.PAUSE));
        resolve(incident);

        assertThatThrownBy(() -> routing.resume(outsider, incident.getId(), m.id())).isInstanceOf(AccessDeniedException.class);
        assertThat(maintenanceService.get(admin, m.id()).status()).isEqualTo(MaintenanceStatus.SUSPENDED);
    }

    @Test
    void anIncidentWithNoRoutingHasNothingToDecide() {
        Incident incident = hostIncident();

        assertThatThrownBy(() -> routing.decide(admin, incident.getId(), new DecisionRequest(1L, Decision.PAUSE)))
                .isInstanceOf(com.orange.pfe.backend.exception.ResourceNotFoundException.class);
    }

    // ── Correspondance de ressource et destinataires ──────────────────────────

    @Test
    void aMaintenanceDeclaredAsVmMatchesAHostIncidentOnTheSameMachine() {
        vmTeam();
        runningMaintenance(MaintenanceResourceType.VM, node, null);

        assertThat(routing.routeIncident(hostIncident().getId())).isEqualTo(Outcome.ROUTED);
    }

    @Test
    void aServiceIncidentTypedVmDownIsRoutedToTheServiceTeamNotTheVmTeam() {
        vmTeam();
        AppUser serviceLead = OrgTestSupport.user(users, "svc-" + tag, UserRole.USER);
        TeamResponse serviceTeam = teams.create(admin, new TeamRequest("Supervision-" + tag, null, serviceLead.getId(), ResourceScope.SERVICE));
        String service = "loki-" + tag;
        runningMaintenance(MaintenanceResourceType.SERVICE, service, null);
        Incident incident = incident("VM_DOWN", "infra", service);   // le nom du composant est dans « app »

        // « loki » seul est reconnu comme composant de supervision ; un autre nom reste un incident VM_DOWN d'hôte
        assertThat(ResourceScope.fromIncident("VM_DOWN", "infra", service)).contains(ResourceScope.SERVICE);
        assertThat(routing.routeIncident(incident.getId())).isEqualTo(Outcome.ROUTED);
        assertThat(task(incident, TaskSourceEvent.DECISION).getTeam().getId()).isEqualTo(serviceTeam.id());
        verify(email).sendTeamNotification(eq(serviceLead.getEmail()), anyString(), anyString());
    }

    @Test
    void theLeadOfTheMaintenanceTeamIsAlsoNotifiedWhenItDiffersFromTheRoutedTeamLead() {
        vmTeam();
        AppUser clusterLead = OrgTestSupport.user(users, "cl-" + tag, UserRole.USER);
        TeamResponse clusterTeam = teams.create(admin, new TeamRequest("Cluster-" + tag, null, clusterLead.getId(), ResourceScope.CLUSTER));
        runningMaintenance(MaintenanceResourceType.NODE, node, clusterTeam.id());   // maintenance portée par l'équipe cluster
        Incident incident = hostIncident();

        assertThat(routing.routeIncident(incident.getId())).isEqualTo(Outcome.ROUTED);

        verify(email).sendTeamNotification(eq(lead.getEmail()), anyString(), anyString());
        verify(email).sendTeamNotification(eq(clusterLead.getEmail()), anyString(), anyString());
    }

    @Test
    void theViewIsReadableByAnyAuthenticatedRoleButOnlyDecidableByAuthorizedOnes() {
        vmTeam();
        runningMaintenance(MaintenanceResourceType.NODE, node, null);
        Incident incident = hostIncident();
        routing.routeIncident(incident.getId());

        assertThat(routing.view(outsider, incident.getId()).state()).isEqualTo(RoutingState.AWAITING_DECISION);
        assertThat(routing.view(outsider, incident.getId()).canDecide()).isFalse();
        assertThat(routing.view(admin, incident.getId()).canDecide()).isTrue();
        assertThat(routing.view(lead, incident.getId()).canDecide()).isTrue();
    }
}
