package com.orange.pfe.backend.service.audit;

import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditLogEntry;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.audit.AuditResult;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AuditLogRepository;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Écriture du journal d'audit : acteur, valeurs assainies, et comportement face à une panne de l'audit. */
class AuditTrailServiceUnitTest {

    private final AuditLogRepository repository = mock(AuditLogRepository.class);
    private final AuditTrailService service = new AuditTrailService(repository);

    @AfterEach
    void clearContext() {
        SecurityContextHolder.clearContext();
    }

    private AppUser admin() {
        AppUser u = new AppUser();
        u.setUsername("alice");
        u.setRole(UserRole.ADMIN);
        return u;
    }

    @Test
    void identifiesTheActorAndStoresSanitizedOldAndNewValues() {
        when(repository.save(any())).thenAnswer(i -> i.getArgument(0));

        service.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.UPDATE).actor(admin())
                .object("Team", 42)
                .summary("Modification de l'équipe")
                .oldValues(Map.of("name", "A", "password", "hunter2"))
                .newValues(Map.of("name", "B")));

        ArgumentCaptor<AuditLogEntry> captor = ArgumentCaptor.forClass(AuditLogEntry.class);
        verify(repository).save(captor.capture());
        AuditLogEntry e = captor.getValue();
        assertThat(e.getActorUsername()).isEqualTo("alice");
        assertThat(e.getActorRole()).isEqualTo("ADMIN");
        assertThat(e.getModule()).isEqualTo(AuditModule.TEAMS);
        assertThat(e.getAction()).isEqualTo(AuditAction.UPDATE);
        assertThat(e.getObjectType()).isEqualTo("Team");
        assertThat(e.getObjectId()).isEqualTo("42");
        assertThat(e.getResult()).isEqualTo(AuditResult.SUCCESS);
        assertThat(e.getOccurredAt()).isNotNull();
        assertThat(e.getOldValues()).contains("\"name\":\"A\"").doesNotContain("hunter2").contains("***");
        assertThat(e.getNewValues()).contains("\"name\":\"B\"");
    }

    @Test
    void resolvesTheActorFromTheSecurityContextWhenNotGiven() {
        AppUser bob = admin();
        bob.setUsername("bob");
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken(bob, null, bob.getAuthorities()));
        when(repository.save(any())).thenAnswer(i -> i.getArgument(0));

        service.record(AuditEvent.of(AuditModule.AUTH, AuditAction.LOGOUT));

        ArgumentCaptor<AuditLogEntry> captor = ArgumentCaptor.forClass(AuditLogEntry.class);
        verify(repository).save(captor.capture());
        assertThat(captor.getValue().getActorUsername()).isEqualTo("bob");
    }

    @Test
    void recordsTechnicalIdentitiesAsServiceAndKeepsAnExplicitUsernameForFailedLogins() {
        SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken(
                "HELIOS_BRIDGE", null, List.of(new SimpleGrantedAuthority("ROLE_HELIOS_BRIDGE"))));
        when(repository.save(any())).thenAnswer(i -> i.getArgument(0));

        AuditLogEntry technical = service.record(AuditEvent.of(AuditModule.AUTH, AuditAction.LOGIN));
        assertThat(technical.getActorUsername()).isEqualTo("HELIOS_BRIDGE");
        assertThat(technical.getActorRole()).isEqualTo("SERVICE");

        SecurityContextHolder.clearContext();
        AuditLogEntry failed = service.record(AuditEvent.of(AuditModule.AUTH, AuditAction.LOGIN_FAILED)
                .actorUsername("inconnu").result(AuditResult.FAILURE));
        assertThat(failed.getActorUsername()).isEqualTo("inconnu");
        assertThat(failed.getActorUserId()).isNull();
        assertThat(failed.getResult()).isEqualTo(AuditResult.FAILURE);
    }

    @Test
    void truncatesOversizedFields() {
        when(repository.save(any())).thenAnswer(i -> i.getArgument(0));
        AuditLogEntry e = service.record(AuditEvent.of(AuditModule.AUDIT, AuditAction.EXPORT)
                .actorUsername("x".repeat(200)).object("T".repeat(200), "9".repeat(200)).summary("s".repeat(900)));
        assertThat(e.getActorUsername()).hasSize(50);
        assertThat(e.getObjectType()).hasSize(60);
        assertThat(e.getObjectId()).hasSize(64);
        assertThat(e.getSummary()).hasSize(500);
    }

    @Test
    void quietRecordingNeverBreaksTheCallerWhereasNormalRecordingReportsTheFailure() {
        when(repository.save(any())).thenThrow(new IllegalStateException("base indisponible"));
        AuditEvent event = AuditEvent.of(AuditModule.AUTH, AuditAction.LOGIN).actor(admin());

        assertThatCode(() -> service.recordQuietly(event)).doesNotThrowAnyException();
        assertThatThrownBy(() -> service.record(event)).isInstanceOf(IllegalStateException.class);
    }
}
