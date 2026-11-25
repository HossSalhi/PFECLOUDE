package com.orange.pfe.backend.service.maintenance;

import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.HistoryResponse;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceRequest;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceResponse;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.audit.AuditTrailQueryService;
import com.orange.pfe.backend.service.org.OrgTestSupport;
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
 * Module Maintenance avec une vraie base : création, modification, cohérence des dates, conflits de planification,
 * cycle de vie, historique, permissions et traçage. Le module est déclaratif : rien ne touche la supervision.
 */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class MaintenanceServiceIntegrationTest {

    @Autowired private MaintenanceService service;
    @Autowired private AppUserRepository users;
    @Autowired private AuditTrailQueryService auditQuery;

    private AppUser superAdmin;
    private AppUser admin;
    private AppUser user;
    private String tag;
    private String resource;
    private LocalDateTime base;

    @BeforeEach
    void setUp() {
        tag = UUID.randomUUID().toString().substring(0, 8);
        superAdmin = OrgTestSupport.user(users, "sup-" + tag, UserRole.SUPER_ADMIN);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
        user = OrgTestSupport.user(users, "usr-" + tag, UserRole.USER);
        resource = "node-" + tag;
        base = LocalDateTime.now().plusDays(2).withSecond(0).withNano(0);
    }

    private MaintenanceRequest request(String title, String res, LocalDateTime start, LocalDateTime end, Long responsibleId) {
        return new MaintenanceRequest(title, "Description de " + title, "Motif de " + title,
                MaintenanceResourceType.NODE, res, start, end, responsibleId);
    }

    private MaintenanceResponse plan(String title, LocalDateTime start, LocalDateTime end) {
        return service.create(admin, request(title, resource, start, end, null));
    }

    // ── Création ──────────────────────────────────────────────────────────────

    @Test
    void createsAPlannedMaintenanceLinkedToItsCreatorAndResponsible() {
        MaintenanceResponse m = service.create(admin, request("Mise à jour noyau", resource, base, base.plusHours(4), user.getId()));

        assertThat(m.status()).isEqualTo(MaintenanceStatus.PLANNED);
        assertThat(m.title()).isEqualTo("Mise à jour noyau");
        assertThat(m.resourceType()).isEqualTo(MaintenanceResourceType.NODE);
        assertThat(m.resourceName()).isEqualTo(resource);
        assertThat(m.startAt()).isEqualTo(base);
        assertThat(m.endAt()).isEqualTo(base.plusHours(4));
        assertThat(m.createdBy().id()).isEqualTo(admin.getId());
        assertThat(m.responsible().id()).isEqualTo(user.getId());
        assertThat(m.createdAt()).isNotNull();
        assertThat(m.updatedAt()).isNotNull();

        List<HistoryResponse> history = service.history(admin, m.id());
        assertThat(history).singleElement().satisfies(h -> {
            assertThat(h.changeType()).isEqualTo("CREATED");
            assertThat(h.changedBy()).isEqualTo(admin.getUsername());
        });
        assertThat(actionsOn(m.id())).contains(AuditAction.CREATE);
    }

    @Test
    void rejectsInconsistentOrInvalidPeriods() {
        assertThatThrownBy(() -> plan("Fin avant début", base, base.minusHours(1)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("postérieure");
        assertThatThrownBy(() -> plan("Durée nulle", base, base))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> plan("Trop longue", base, base.plusDays(40)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("31 jours");
        assertThatThrownBy(() -> plan("Dans le passé", LocalDateTime.now().minusDays(3), LocalDateTime.now().minusDays(2)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("passé");
        assertThatThrownBy(() -> service.create(admin, request(" ", resource, base, base.plusHours(1), null)))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.create(admin, request("Sans ressource", "  ", base, base.plusHours(1), null)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void theResponsibleMustBeAnEnabledExistingUser() {
        assertThatThrownBy(() -> service.create(admin, request("Inconnu", resource, base, base.plusHours(1), 9_999_999L)))
                .isInstanceOf(IllegalArgumentException.class);
        user.setEnabled(false);
        users.saveAndFlush(user);
        assertThatThrownBy(() -> service.create(admin, request("Désactivé", resource, base, base.plusHours(1), user.getId())))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("désactivé");
    }

    // ── Conflits de planification ─────────────────────────────────────────────

    @Test
    void detectsPlanningConflictsOnTheSameResourceOnly() {
        plan("Première", base, base.plusHours(4));

        assertThatThrownBy(() -> plan("Chevauche", base.plusHours(2), base.plusHours(6)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("Conflit de planification");
        assertThatThrownBy(() -> plan("Englobe", base.minusHours(1), base.plusHours(10)))
                .isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> plan("Contenue", base.plusHours(1), base.plusHours(2)))
                .isInstanceOf(IllegalStateException.class);

        // bout à bout : autorisé ; autre ressource : autorisé
        assertThat(plan("Juste après", base.plusHours(4), base.plusHours(6)).status()).isEqualTo(MaintenanceStatus.PLANNED);
        assertThat(service.create(admin, request("Autre ressource", "autre-" + tag, base, base.plusHours(4), null)).id()).isNotNull();
    }

    @Test
    void aCancelledMaintenanceFreesTheSlotAndAnUpdateIgnoresItself() {
        MaintenanceResponse first = plan("À annuler", base, base.plusHours(4));
        service.cancel(admin, first.id(), "Reportée");
        MaintenanceResponse second = plan("Même créneau", base, base.plusHours(4));

        // la modifier sans changer le créneau ne la met pas en conflit avec elle-même
        MaintenanceResponse edited = service.update(admin, second.id(),
                request("Même créneau (modifiée)", resource, base, base.plusHours(4), null));
        assertThat(edited.title()).isEqualTo("Même créneau (modifiée)");

        MaintenanceResponse third = plan("Plus tard", base.plusDays(1), base.plusDays(1).plusHours(2));
        assertThatThrownBy(() -> service.update(admin, third.id(), request("Plus tard", resource, base.plusHours(1), base.plusHours(3), null)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("Conflit");
    }

    // ── Modification ──────────────────────────────────────────────────────────

    @Test
    void updatingRecordsOldAndNewValuesInTheHistoryAndSkipsNoOps() {
        MaintenanceResponse m = plan("Initiale", base, base.plusHours(2));

        service.update(admin, m.id(), request("Modifiée", resource, base.plusHours(1), base.plusHours(3), user.getId()));

        List<HistoryResponse> history = service.history(admin, m.id());
        assertThat(history).extracting(HistoryResponse::changeType).containsExactly("CREATED", "UPDATED");
        String details = history.get(1).details();
        assertThat(details).contains("\"old\"").contains("\"new\"").contains("Initiale").contains("Modifiée");

        // même contenu : aucun nouvel historique
        service.update(admin, m.id(), request("Modifiée", resource, base.plusHours(1), base.plusHours(3), user.getId()));
        assertThat(service.history(admin, m.id())).hasSize(2);
        assertThat(actionsOn(m.id())).contains(AuditAction.CREATE, AuditAction.UPDATE);
    }

    @Test
    void onceStartedOnlyTheEndDescriptionAndResponsibleCanChange() {
        MaintenanceResponse m = service.create(admin, request("En cours", resource, LocalDateTime.now().minusHours(1),
                LocalDateTime.now().plusHours(2), null));
        service.start(admin, m.id());

        assertThatThrownBy(() -> service.update(admin, m.id(), request("En cours", "autre-" + tag, m.startAt(), m.endAt(), null)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("ne peuvent plus être modifiées");
        assertThatThrownBy(() -> service.update(admin, m.id(), request("En cours", resource, m.startAt().plusMinutes(5), m.endAt(), null)))
                .isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> service.update(admin, m.id(), request("En cours", resource, m.startAt(), LocalDateTime.now().minusMinutes(5), null)))
                .isInstanceOf(IllegalArgumentException.class);

        MaintenanceResponse extended = service.update(admin, m.id(),
                request("En cours", resource, m.startAt(), m.endAt().plusHours(3), user.getId()));
        assertThat(extended.endAt()).isEqualTo(m.endAt().plusHours(3));
        assertThat(extended.responsible().id()).isEqualTo(user.getId());
    }

    // ── Cycle de vie ──────────────────────────────────────────────────────────

    @Test
    void followsThePlannedInProgressCompletedLifecycleAndKeepsItsHistory() {
        MaintenanceResponse m = plan("Cycle", base, base.plusHours(2));

        MaintenanceResponse started = service.start(admin, m.id());
        assertThat(started.status()).isEqualTo(MaintenanceStatus.IN_PROGRESS);
        assertThat(started.actualStartAt()).isNotNull();
        assertThat(service.inProgress(user)).extracting(MaintenanceResponse::id).contains(m.id());

        MaintenanceResponse done = service.complete(admin, m.id());
        assertThat(done.status()).isEqualTo(MaintenanceStatus.COMPLETED);
        assertThat(done.actualEndAt()).isNotNull();
        assertThat(service.inProgress(user)).extracting(MaintenanceResponse::id).doesNotContain(m.id());

        assertThat(service.history(admin, m.id())).extracting(HistoryResponse::changeType)
                .containsExactly("CREATED", "STARTED", "COMPLETED");
        assertThat(actionsOn(m.id())).contains(AuditAction.START, AuditAction.COMPLETE);

        assertThatThrownBy(() -> service.update(admin, m.id(), request("Terminée", resource, base, base.plusHours(2), null)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("ne peut plus être modifiée");
        assertThatThrownBy(() -> service.cancel(admin, m.id(), null)).isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> service.start(admin, m.id())).isInstanceOf(IllegalStateException.class);
    }

    @Test
    void onlyAPlannedMaintenanceCanStartAndOnlyAnInProgressOneCanComplete() {
        MaintenanceResponse m = plan("Ordre", base, base.plusHours(2));
        assertThatThrownBy(() -> service.complete(admin, m.id())).isInstanceOf(IllegalStateException.class);
        service.start(admin, m.id());
        assertThatThrownBy(() -> service.start(admin, m.id())).isInstanceOf(IllegalStateException.class);
    }

    @Test
    void cancellingKeepsTheReason() {
        MaintenanceResponse m = plan("Annulation", base, base.plusHours(2));
        MaintenanceResponse cancelled = service.cancel(admin, m.id(), "Fenêtre refusée par le métier");
        assertThat(cancelled.status()).isEqualTo(MaintenanceStatus.CANCELLED);
        assertThat(cancelled.cancelReason()).isEqualTo("Fenêtre refusée par le métier");
        assertThat(actionsOn(m.id())).contains(AuditAction.CANCEL);
    }

    // ── Permissions ───────────────────────────────────────────────────────────

    @Test
    void anOrdinaryUserCanViewButNeverChangeAMaintenance() {
        MaintenanceResponse m = plan("Lecture seule", base, base.plusHours(2));

        assertThat(service.get(user, m.id()).id()).isEqualTo(m.id());
        assertThat(service.search(user, new MaintenanceService.Query(null, null, null, null, null, null, null), 0, 20)
                .getContent()).extracting(MaintenanceResponse::id).contains(m.id());

        assertThatThrownBy(() -> service.create(user, request("x", resource, base.plusDays(5), base.plusDays(5).plusHours(1), null)))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.update(user, m.id(), request("x", resource, base, base.plusHours(2), null)))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.start(user, m.id())).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.cancel(user, m.id(), null)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.delete(user, m.id())).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void deletionIsReservedToSuperAdminAndLimitedToPlannedOrCancelledMaintenances() {
        MaintenanceResponse planned = plan("À supprimer", base, base.plusHours(2));
        MaintenanceResponse running = plan("En cours", base.plusDays(3), base.plusDays(3).plusHours(2));
        service.start(admin, running.id());

        assertThatThrownBy(() -> service.delete(admin, planned.id())).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.delete(superAdmin, running.id()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("planifiée ou annulée");

        service.delete(superAdmin, planned.id());
        assertThatThrownBy(() -> service.get(admin, planned.id())).hasMessageContaining("introuvable");
        // la suppression laisse une trace d'audit, avec les anciennes valeurs
        assertThat(actionsOn(planned.id())).contains(AuditAction.CREATE, AuditAction.DELETE);
    }

    // ── Consultation ──────────────────────────────────────────────────────────

    @Test
    void filtersByStatusResourceResponsibleAndPeriodAndBuildsTheCalendar() {
        MaintenanceResponse a = service.create(admin, request("Alpha", resource, base, base.plusHours(2), user.getId()));
        MaintenanceResponse b = service.create(admin, request("Bravo", "autre-" + tag, base.plusDays(10), base.plusDays(10).plusHours(2), null));
        service.cancel(admin, b.id(), null);

        assertThat(search(MaintenanceStatus.CANCELLED, null, null, null, null)).extracting(MaintenanceResponse::id)
                .contains(b.id()).doesNotContain(a.id());
        assertThat(search(null, resource, null, null, null)).extracting(MaintenanceResponse::id).containsExactly(a.id());
        assertThat(search(null, null, user.getId(), null, null)).extracting(MaintenanceResponse::id).containsExactly(a.id());
        assertThat(search(null, null, null, base.plusDays(9), base.plusDays(11))).extracting(MaintenanceResponse::id)
                .contains(b.id()).doesNotContain(a.id());
        assertThat(service.search(admin, new MaintenanceService.Query(null, null, null, null, null, null, "alpha"), 0, 10)
                .getContent()).extracting(MaintenanceResponse::id).contains(a.id());

        assertThat(service.calendar(user, base.minusDays(1), base.plusDays(1))).extracting(MaintenanceResponse::id)
                .contains(a.id()).doesNotContain(b.id());
        assertThatThrownBy(() -> service.calendar(user, base, base.minusDays(1))).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.calendar(user, base, base.plusDays(200)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("93 jours");
    }

    private List<MaintenanceResponse> search(MaintenanceStatus status, String res, Long responsible,
                                             LocalDateTime from, LocalDateTime to) {
        return service.search(admin, new MaintenanceService.Query(status, null, res, responsible, from, to, null), 0, 100)
                .getContent();
    }

    private List<AuditAction> actionsOn(Long id) {
        return auditQuery.search(admin, new AuditSearchCriteria(null, null, null, null, "Maintenance", String.valueOf(id),
                        null, null, null, null), 0, 100)
                .getContent().stream().map(e -> AuditAction.valueOf(e.action())).toList();
    }
}
