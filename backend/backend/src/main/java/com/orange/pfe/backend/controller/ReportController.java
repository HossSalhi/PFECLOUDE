package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.report.ReportDtos.GenerateRequest;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportDetail;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportItem;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportTypeInfo;
import com.orange.pfe.backend.dto.report.ReportDtos.ScheduleRequest;
import com.orange.pfe.backend.dto.report.ReportDtos.ScheduleResponse;
import com.orange.pfe.backend.dto.report.ReportDtos.ScheduleToggleRequest;
import com.orange.pfe.backend.model.report.ReportType;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.report.ReportExporter;
import com.orange.pfe.backend.service.report.ReportService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.data.domain.Page;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

/**
 * Gestion des rapports : génération manuelle et périodique, consultation des rapports déjà générés, statistiques
 * d'activité, export PDF / CSV. Les droits sont appliqués par {@link ReportService}.
 */
@RestController
@RequestMapping("/api/reports")
@Tag(name = "Rapports", description = "Rapports calculés sur les données réelles, plannings, exports PDF et CSV")
public class ReportController {

    private final ReportService reportService;

    public ReportController(ReportService reportService) {
        this.reportService = reportService;
    }

    @Operation(summary = "Types de rapports disponibles")
    @GetMapping("/types")
    public List<ReportTypeInfo> types(@AuthenticationPrincipal AppUser actor) {
        return reportService.types(actor);
    }

    @Operation(summary = "Rapports déjà générés (plus récents d'abord)")
    @GetMapping
    public Page<ReportItem> list(@AuthenticationPrincipal AppUser actor,
                                 @RequestParam(required = false) ReportType type,
                                 @RequestParam(defaultValue = "0") int page,
                                 @RequestParam(defaultValue = "20") int size) {
        return reportService.list(actor, type, page, size);
    }

    @Operation(summary = "Statistiques d'activité de la plateforme sur une période (calculées à la demande)")
    @GetMapping("/statistics")
    public Map<String, Object> statistics(
            @AuthenticationPrincipal AppUser actor,
            @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime from,
            @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime to,
            @RequestParam(defaultValue = "false") boolean compare) {
        return reportService.statistics(actor, from, to, compare);
    }

    @Operation(summary = "Générer un rapport sur une période (avec comparaison optionnelle à la période précédente)")
    @PostMapping("/generate")
    @ResponseStatus(HttpStatus.CREATED)
    public ReportItem generate(@AuthenticationPrincipal AppUser actor, @Valid @RequestBody GenerateRequest request) {
        return reportService.generate(actor, request);
    }

    @Operation(summary = "Plannings de rapports périodiques")
    @GetMapping("/schedules")
    public List<ScheduleResponse> schedules(@AuthenticationPrincipal AppUser actor) {
        return reportService.schedules(actor);
    }

    @Operation(summary = "Créer un planning de rapport périodique")
    @PostMapping("/schedules")
    @ResponseStatus(HttpStatus.CREATED)
    public ScheduleResponse createSchedule(@AuthenticationPrincipal AppUser actor,
                                           @Valid @RequestBody ScheduleRequest request) {
        return reportService.createSchedule(actor, request);
    }

    @Operation(summary = "Activer / désactiver un planning")
    @PatchMapping("/schedules/{id}")
    public ScheduleResponse toggleSchedule(@AuthenticationPrincipal AppUser actor, @PathVariable Long id,
                                           @Valid @RequestBody ScheduleToggleRequest request) {
        return reportService.toggleSchedule(actor, id, request.enabled());
    }

    @Operation(summary = "Supprimer un planning (les rapports déjà générés sont conservés)")
    @DeleteMapping("/schedules/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void deleteSchedule(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        reportService.deleteSchedule(actor, id);
    }

    @Operation(summary = "Détail d'un rapport généré (indicateurs, tableaux, graphiques)")
    @GetMapping("/{id}")
    public ReportDetail get(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return reportService.get(actor, id);
    }

    @Operation(summary = "Exporter un rapport au format PDF ou CSV")
    @GetMapping("/{id}/export")
    public ResponseEntity<byte[]> export(@AuthenticationPrincipal AppUser actor, @PathVariable Long id,
                                         @RequestParam(defaultValue = "pdf") String format) {
        ReportExporter.Export export = reportService.export(actor, id, format);
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + export.filename() + "\"")
                .contentType(MediaType.parseMediaType(export.contentType()))
                .body(export.bytes());
    }
}
