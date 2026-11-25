package com.orange.pfe.backend.service.report;

import com.orange.pfe.backend.dto.report.ReportDtos.GenerateRequest;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditResult;
import com.orange.pfe.backend.model.report.Report;
import com.orange.pfe.backend.model.report.ReportStatus;
import com.orange.pfe.backend.model.report.ReportType;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.ReportRepository;
import com.orange.pfe.backend.repository.ReportScheduleRepository;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import com.orange.pfe.backend.service.org.OrgAccessService;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Gestion d'erreurs du module Rapports (collaborateurs simulés) : génération en échec, export impossible, droits. */
class ReportServiceUnitTest {

    private final ReportRepository reports = mock(ReportRepository.class);
    private final ReportScheduleRepository schedules = mock(ReportScheduleRepository.class);
    private final ReportBuilders builders = mock(ReportBuilders.class);
    private final ReportExporter exporter = mock(ReportExporter.class);
    private final AuditTrailService audit = mock(AuditTrailService.class);
    private final ReportService service = new ReportService(reports, schedules, builders, exporter,
            new OrgAccessService(audit), audit);

    private static AppUser user(UserRole role) {
        AppUser u = new AppUser();
        u.setUsername("u-" + role);
        u.setRole(role);
        return u;
    }

    private static GenerateRequest request() {
        LocalDateTime now = LocalDateTime.now();
        return new GenerateRequest(ReportType.PERFORMANCE, now.minusDays(1), now, false);
    }

    @Test
    void aFailedGenerationStoresNothingTracesTheFailureAndReturnsAClearError() {
        when(builders.build(any(), any(), org.mockito.ArgumentMatchers.anyBoolean()))
                .thenThrow(new IllegalStateException("table incident illisible"));

        assertThatThrownBy(() -> service.generate(user(UserRole.ADMIN), request()))
                .isInstanceOfSatisfying(ResponseStatusException.class, e -> {
                    assertThat(e.getStatusCode().value()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR.value());
                    assertThat(e.getReason()).contains("La génération du rapport a échoué").contains("table incident illisible");
                });

        verify(reports, never()).saveAndFlush(any());
        verify(reports, never()).save(any());
        ArgumentCaptor<AuditEvent> captor = ArgumentCaptor.forClass(AuditEvent.class);
        verify(audit).recordIndependent(captor.capture());
        assertThat(captor.getValue().result()).isEqualTo(AuditResult.FAILURE);
        assertThat(captor.getValue().action()).isEqualTo(AuditAction.GENERATE);
    }

    @Test
    void generationIsRefusedWithoutThePermissionAndForAnInvalidPeriod() {
        assertThatThrownBy(() -> service.generate(user(UserRole.USER), request())).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.generate(null, request())).isInstanceOf(AccessDeniedException.class);

        LocalDateTime future = LocalDateTime.now().plusDays(2);
        assertThatThrownBy(() -> service.generate(user(UserRole.ADMIN),
                new GenerateRequest(ReportType.PERFORMANCE, future, future.plusDays(1), false)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("futur");
        assertThatThrownBy(() -> service.generate(user(UserRole.ADMIN),
                new GenerateRequest(ReportType.PERFORMANCE, LocalDateTime.now(), LocalDateTime.now().minusDays(1), false)))
                .isInstanceOf(IllegalArgumentException.class);
        verify(builders, never()).build(any(), any(), org.mockito.ArgumentMatchers.anyBoolean());
    }

    @Test
    void exportNeedsThePermissionAKnownFormatAndUsableContent() {
        Report empty = new Report();
        empty.setReportType(ReportType.PERFORMANCE);
        empty.setStatus(ReportStatus.COMPLETED);
        when(reports.findById(1L)).thenReturn(Optional.of(empty));
        when(reports.findById(2L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.export(user(UserRole.USER), 1L, "pdf")).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.export(user(UserRole.ADMIN), 2L, "pdf")).hasMessageContaining("introuvable");
        assertThatThrownBy(() -> service.export(user(UserRole.ADMIN), 1L, "pdf"))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("contenu exploitable");

        empty.setResultJson("{}");
        assertThatThrownBy(() -> service.export(user(UserRole.ADMIN), 1L, "xlsx"))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("csv ou pdf");
    }

    @Test
    void schedulesAreOnlyManagedWithTheSchedulePermission() {
        assertThatThrownBy(() -> service.deleteSchedule(user(UserRole.USER), 1L)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.toggleSchedule(user(UserRole.USER), 1L, false)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.schedules(user(UserRole.USER))).isInstanceOf(AccessDeniedException.class);
    }
}
