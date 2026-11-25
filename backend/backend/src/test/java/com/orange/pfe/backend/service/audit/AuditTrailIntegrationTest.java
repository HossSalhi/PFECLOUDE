package com.orange.pfe.backend.service.audit;

import com.orange.pfe.backend.dto.audit.AuditEntryResponse;
import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditLogEntry;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.audit.AuditResult;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.org.OrgAccessService;
import com.orange.pfe.backend.service.org.OrgPermission;
import com.orange.pfe.backend.service.org.OrgTestSupport;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.domain.Page;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.transaction.annotation.Transactional;

import java.io.StringWriter;
import java.time.LocalDateTime;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Audit des activités avec une vraie base (H2 en mémoire) : persistance, identification de l'utilisateur,
 * valeurs avant/après, droits de consultation, export et traçage des refus.
 */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class AuditTrailIntegrationTest {

    @Autowired private AuditTrailService auditTrailService;
    @Autowired private AuditTrailQueryService queryService;
    @Autowired private OrgAccessService access;
    @Autowired private AppUserRepository users;

    private AppUser admin;
    private AppUser alice;
    private AppUser bob;
    private String tag;

    @BeforeEach
    void setUp() {
        tag = "t" + UUID.randomUUID().toString().substring(0, 8);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
        alice = OrgTestSupport.user(users, "ali-" + tag, UserRole.USER);
        bob = OrgTestSupport.user(users, "bob-" + tag, UserRole.USER);
    }

    private AuditLogEntry log(AppUser actor, AuditAction action, String objectType) {
        return auditTrailService.record(AuditEvent.of(AuditModule.TEAMS, action).actor(actor)
                .object(objectType, 1).summary("test " + action));
    }

    private AuditSearchCriteria byType(String objectType) {
        return new AuditSearchCriteria(null, null, null, null, objectType, null, null, null, null, null);
    }

    @Test
    void persistsTheActivityWithItsActorAndOldAndNewValues() {
        AuditLogEntry saved = auditTrailService.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.UPDATE)
                .actor(admin).object("Team-" + tag, 7).summary("Modification")
                .oldValues(Map.of("name", "A", "password", "hunter2")).newValues(Map.of("name", "B")));

        AuditEntryResponse detail = queryService.get(admin, saved.getId());
        assertThat(detail.actorUserId()).isEqualTo(admin.getId());
        assertThat(detail.actorUsername()).isEqualTo("adm-" + tag);
        assertThat(detail.actorRole()).isEqualTo("ADMIN");
        assertThat(detail.module()).isEqualTo("TEAMS");
        assertThat(detail.action()).isEqualTo("UPDATE");
        assertThat(detail.objectType()).isEqualTo("Team-" + tag);
        assertThat(detail.objectId()).isEqualTo("7");
        assertThat(detail.hasChanges()).isTrue();
        assertThat(detail.oldValues()).contains("\"name\":\"A\"").doesNotContain("hunter2");
        assertThat(detail.newValues()).contains("\"name\":\"B\"");
        assertThat(detail.occurredAt()).isBefore(LocalDateTime.now().plusMinutes(1));
    }

    @Test
    void filtersByUserPeriodActionAndModule() {
        log(alice, AuditAction.CREATE, "F-" + tag);
        log(bob, AuditAction.UPDATE, "F-" + tag);

        Page<AuditEntryResponse> byUser = queryService.search(admin,
                new AuditSearchCriteria(alice.getId(), null, null, null, "F-" + tag, null, null, null, null, null), 0, 25);
        assertThat(byUser.getContent()).hasSize(1).allMatch(e -> e.actorUserId().equals(alice.getId()));

        Page<AuditEntryResponse> byAction = queryService.search(admin,
                new AuditSearchCriteria(null, null, AuditModule.TEAMS, AuditAction.UPDATE, "F-" + tag, null, null, null, null, null), 0, 25);
        assertThat(byAction.getContent()).hasSize(1).allMatch(e -> e.action().equals("UPDATE"));

        LocalDateTime now = LocalDateTime.now();
        Page<AuditEntryResponse> inPeriod = queryService.search(admin,
                new AuditSearchCriteria(null, null, null, null, "F-" + tag, null, null, now.minusHours(1), now.plusHours(1), null), 0, 25);
        assertThat(inPeriod.getTotalElements()).isEqualTo(2);

        Page<AuditEntryResponse> inThePast = queryService.search(admin,
                new AuditSearchCriteria(null, null, null, null, "F-" + tag, null, null, now.minusDays(10), now.minusDays(9), null), 0, 25);
        assertThat(inThePast.getTotalElements()).isZero();
    }

    @Test
    void anOrdinaryUserOnlySeesTheirOwnActivityWhateverTheyAskFor() {
        log(alice, AuditAction.CREATE, "O-" + tag);
        log(bob, AuditAction.CREATE, "O-" + tag);

        // même en demandant explicitement l'activité de bob, alice ne voit que la sienne
        Page<AuditEntryResponse> asAlice = queryService.search(alice,
                new AuditSearchCriteria(bob.getId(), "bob-" + tag, null, null, "O-" + tag, null, null, null, null, null), 0, 25);
        assertThat(asAlice.getContent()).hasSize(1).allMatch(e -> e.actorUserId().equals(alice.getId()));

        Page<AuditEntryResponse> asAdmin = queryService.search(admin, byType("O-" + tag), 0, 25);
        assertThat(asAdmin.getTotalElements()).isEqualTo(2);
    }

    @Test
    void anOrdinaryUserCannotOpenSomeoneElsesActivityDetail() {
        AuditLogEntry bobs = log(bob, AuditAction.CREATE, "D-" + tag);
        AuditLogEntry alices = log(alice, AuditAction.CREATE, "D-" + tag);

        assertThatThrownBy(() -> queryService.get(alice, bobs.getId())).isInstanceOf(AccessDeniedException.class);
        assertThat(queryService.get(alice, alices.getId()).id()).isEqualTo(alices.getId());
        assertThat(queryService.get(admin, bobs.getId()).id()).isEqualTo(bobs.getId());
    }

    @Test
    void exportIsReservedAndTracedAndProducesAnInjectionSafeCsv() throws Exception {
        auditTrailService.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.CREATE).actor(alice)
                .object("X-" + tag, 1).summary("=HYPERLINK(\"http://evil\")"));

        assertThatThrownBy(() -> queryService.exportCsv(alice, byType("X-" + tag), new StringWriter()))
                .isInstanceOf(AccessDeniedException.class);

        StringWriter csv = new StringWriter();
        int rows = queryService.exportCsv(admin, byType("X-" + tag), csv);

        assertThat(rows).isEqualTo(1);
        assertThat(csv.toString()).startsWith("﻿id;date;utilisateur");
        assertThat(csv.toString()).contains("'=HYPERLINK").doesNotContain(";=HYPERLINK");

        Page<AuditEntryResponse> exports = queryService.search(admin,
                new AuditSearchCriteria(admin.getId(), null, AuditModule.AUDIT, AuditAction.EXPORT, null, null, null, null, null, null), 0, 10);
        assertThat(exports.getTotalElements()).isGreaterThanOrEqualTo(1);
    }

    @Test
    void aDeniedAttemptIsTracedWithTheDeniedResult() {
        assertThat(access.has(alice, OrgPermission.MAINTENANCE_CREATE)).isFalse();
        assertThatThrownBy(() -> access.require(alice, OrgPermission.MAINTENANCE_CREATE))
                .isInstanceOf(AccessDeniedException.class);

        Page<AuditEntryResponse> denied = queryService.search(admin,
                new AuditSearchCriteria(alice.getId(), null, AuditModule.MAINTENANCE, AuditAction.ACCESS_DENIED,
                        null, null, AuditResult.DENIED, null, null, null), 0, 10);
        assertThat(denied.getTotalElements()).isGreaterThanOrEqualTo(1);
        assertThat(denied.getContent().get(0).summary()).contains("MAINTENANCE_CREATE");
    }

    @Test
    void anAnonymousCallerIsRefusedEverywhere() {
        assertThatThrownBy(() -> queryService.search(null, AuditSearchCriteria.empty(), 0, 10))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> access.require(null, OrgPermission.TEAMS_VIEW))
                .isInstanceOf(AccessDeniedException.class);
    }
}
