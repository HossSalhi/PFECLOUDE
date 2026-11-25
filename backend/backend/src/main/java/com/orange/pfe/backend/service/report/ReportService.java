package com.orange.pfe.backend.service.report;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.dto.report.ReportDtos.GenerateRequest;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportDetail;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportItem;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportTypeInfo;
import com.orange.pfe.backend.dto.report.ReportDtos.ScheduleRequest;
import com.orange.pfe.backend.dto.report.ReportDtos.ScheduleResponse;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.audit.AuditResult;
import com.orange.pfe.backend.model.report.Report;
import com.orange.pfe.backend.model.report.ReportSchedule;
import com.orange.pfe.backend.model.report.ReportStatus;
import com.orange.pfe.backend.model.report.ReportTrigger;
import com.orange.pfe.backend.model.report.ReportType;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.ReportRepository;
import com.orange.pfe.backend.repository.ReportScheduleRepository;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.org.OrgAccessService;
import com.orange.pfe.backend.service.org.OrgPermission;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Génération, consultation et export des rapports.
 *
 * <ul>
 *   <li>Les statistiques sont calculées à partir des données réelles ({@link ReportBuilders}) ; jamais simulées.</li>
 *   <li>Génération manuelle (REPORTS_GENERATE) ou périodique (plannings, REPORTS_SCHEDULE) : un planning ne produit
 *       qu'un rapport par période (contrainte d'unicité) et ne tourne que sur le leader (voir {@link ReportScheduler}).</li>
 *   <li>Export PDF / CSV (REPORTS_EXPORT) à partir du contenu conservé ; chaque export est tracé.</li>
 *   <li>Une génération en échec n'enregistre rien : le client reçoit une erreur claire et l'échec est tracé dans
 *       le journal d'audit ; un planning réessaie au passage suivant.</li>
 * </ul>
 */
@Service
public class ReportService {

    public static final String SCHEDULER_ACTOR = "system:scheduler";
    static final int MAX_PAGE_SIZE = 100;

    private static final Logger log = LoggerFactory.getLogger(ReportService.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final DateTimeFormatter DAY = DateTimeFormatter.ofPattern("dd/MM/yyyy");

    private final ReportRepository reportRepository;
    private final ReportScheduleRepository scheduleRepository;
    private final ReportBuilders builders;
    private final ReportExporter exporter;
    private final OrgAccessService access;
    private final AuditTrailService audit;

    public ReportService(ReportRepository reportRepository,
                         ReportScheduleRepository scheduleRepository,
                         ReportBuilders builders,
                         ReportExporter exporter,
                         OrgAccessService access,
                         AuditTrailService audit) {
        this.reportRepository = reportRepository;
        this.scheduleRepository = scheduleRepository;
        this.builders = builders;
        this.exporter = exporter;
        this.access = access;
        this.audit = audit;
    }

    // ── Lecture ───────────────────────────────────────────────────────────────

    public List<ReportTypeInfo> types(AppUser actor) {
        access.require(actor, OrgPermission.REPORTS_VIEW);
        return Arrays.stream(ReportType.values()).map(t -> new ReportTypeInfo(t, t.label(), t.description())).toList();
    }

    @Transactional(readOnly = true)
    public Page<ReportItem> list(AppUser actor, ReportType type, int page, int size) {
        access.require(actor, OrgPermission.REPORTS_VIEW);
        PageRequest pageable = PageRequest.of(Math.max(0, page), Math.max(1, Math.min(size, MAX_PAGE_SIZE)));
        Page<Report> reports = type == null
                ? reportRepository.findAllByOrderByGeneratedAtDescIdDesc(pageable)
                : reportRepository.findByReportTypeOrderByGeneratedAtDescIdDesc(type, pageable);
        return reports.map(this::toItem);
    }

    @Transactional(readOnly = true)
    public ReportDetail get(AppUser actor, Long id) {
        access.require(actor, OrgPermission.REPORTS_VIEW);
        Report r = getOrThrow(id);
        return new ReportDetail(toItem(r), r.getErrorMessage(), r.getResultJson());
    }

    /** Statistiques d'activité de la plateforme calculées à la demande (non enregistrées). */
    @Transactional(readOnly = true)
    public Map<String, Object> statistics(AppUser actor, LocalDateTime from, LocalDateTime to, boolean compare) {
        access.require(actor, OrgPermission.REPORTS_VIEW);
        ReportPeriod period = new ReportPeriod(from, to);
        ReportContent content = builders.build(ReportType.PLATFORM_ACTIVITY, period, compare);
        return content.toMap(ReportType.PLATFORM_ACTIVITY.name(), ReportType.PLATFORM_ACTIVITY.label(),
                titleFor(ReportType.PLATFORM_ACTIVITY, period), period, compare ? period.previous() : null,
                actor.getUsername(), LocalDateTime.now());
    }

    // ── Génération ────────────────────────────────────────────────────────────

    @Transactional
    public ReportItem generate(AppUser actor, GenerateRequest request) {
        access.require(actor, OrgPermission.REPORTS_GENERATE);
        ReportPeriod period = new ReportPeriod(request.from(), request.to());
        if (period.from().isAfter(LocalDateTime.now())) {
            throw new IllegalArgumentException("La période d'analyse ne peut pas commencer dans le futur");
        }
        Report report = createReport(request.type(), period, request.compare(), ReportTrigger.MANUAL, null,
                actor, actor.getUsername());
        audit.record(AuditEvent.of(AuditModule.REPORTS, AuditAction.GENERATE).actor(actor)
                .object("Report", report.getId())
                .summary("Génération du rapport « " + report.getTitle() + " »")
                .newValues(auditValues(report)));
        return toItem(report);
    }

    /**
     * Génère le rapport d'un planning pour une période donnée. Retourne {@code false} si rien n'est à faire
     * (planning absent/désactivé ou rapport déjà produit pour cette période). Appelé par {@link ReportScheduler}.
     */
    @Transactional
    public boolean generateScheduled(Long scheduleId, ReportPeriod period) {
        ReportSchedule schedule = scheduleRepository.findById(scheduleId).orElse(null);
        if (schedule == null || !schedule.isEnabled()) {
            return false;
        }
        if (reportRepository.existsByScheduleIdAndPeriodStartAndPeriodEnd(scheduleId, period.from(), period.to())) {
            return false;
        }
        Report report = createReport(schedule.getReportType(), period, true, ReportTrigger.SCHEDULED, schedule,
                null, SCHEDULER_ACTOR);
        audit.record(AuditEvent.of(AuditModule.REPORTS, AuditAction.GENERATE).actorUsername(SCHEDULER_ACTOR)
                .object("Report", report.getId())
                .summary("Génération périodique du rapport « " + report.getTitle() + " »")
                .newValues(auditValues(report)));
        return true;
    }

    // ── Export ────────────────────────────────────────────────────────────────

    @Transactional
    public ReportExporter.Export export(AppUser actor, Long id, String format) {
        access.require(actor, OrgPermission.REPORTS_EXPORT);
        Report report = getOrThrow(id);
        if (report.getStatus() != ReportStatus.COMPLETED || report.getResultJson() == null) {
            throw new IllegalStateException("Ce rapport n'a pas de contenu exploitable");
        }
        String f = format == null ? "pdf" : format.trim().toLowerCase(Locale.ROOT);
        ReportExporter.Export export;
        switch (f) {
            case "csv" -> export = exporter.toCsv(report);
            case "pdf" -> export = exporter.toPdf(report);
            default -> throw new IllegalArgumentException("Format d'export non pris en charge : « " + format + " » (csv ou pdf)");
        }
        Map<String, Object> details = new LinkedHashMap<>();
        details.put("format", f);
        details.put("report", report.getTitle());
        details.put("octets", export.bytes().length);
        audit.record(AuditEvent.of(AuditModule.REPORTS, AuditAction.EXPORT).actor(actor)
                .object("Report", id)
                .summary("Export " + f.toUpperCase(Locale.ROOT) + " du rapport « " + report.getTitle() + " »")
                .newValues(details));
        return export;
    }

    // ── Plannings ─────────────────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public List<ScheduleResponse> schedules(AppUser actor) {
        access.require(actor, OrgPermission.REPORTS_VIEW);
        return scheduleRepository.findAllByOrderByReportTypeAscFrequencyAsc().stream().map(this::toSchedule).toList();
    }

    @Transactional
    public ScheduleResponse createSchedule(AppUser actor, ScheduleRequest request) {
        access.require(actor, OrgPermission.REPORTS_SCHEDULE);
        if (scheduleRepository.existsByReportTypeAndFrequency(request.type(), request.frequency())) {
            throw new IllegalStateException("Un planning « " + request.type().label() + " » "
                    + request.frequency() + " existe déjà");
        }
        ReportSchedule s = new ReportSchedule();
        s.setReportType(request.type());
        s.setFrequency(request.frequency());
        s.setEnabled(request.enabled() == null || request.enabled());
        s.setCreatedBy(actor);
        s.setCreatedByUsername(actor.getUsername());
        s = scheduleRepository.save(s);
        audit.record(AuditEvent.of(AuditModule.REPORTS, AuditAction.SCHEDULE).actor(actor)
                .object("ReportSchedule", s.getId())
                .summary("Planning de rapport « " + s.getReportType().label() + " » (" + s.getFrequency() + ")")
                .newValues(scheduleValues(s)));
        return toSchedule(s);
    }

    @Transactional
    public ScheduleResponse toggleSchedule(AppUser actor, Long id, boolean enabled) {
        access.require(actor, OrgPermission.REPORTS_SCHEDULE);
        ReportSchedule s = scheduleRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("Planning introuvable : " + id));
        Map<String, Object> before = scheduleValues(s);
        s.setEnabled(enabled);
        s = scheduleRepository.save(s);
        audit.record(AuditEvent.of(AuditModule.REPORTS, AuditAction.SCHEDULE).actor(actor)
                .object("ReportSchedule", id)
                .summary((enabled ? "Activation" : "Désactivation") + " du planning « " + s.getReportType().label() + " »")
                .oldValues(before).newValues(scheduleValues(s)));
        return toSchedule(s);
    }

    @Transactional
    public void deleteSchedule(AppUser actor, Long id) {
        access.require(actor, OrgPermission.REPORTS_SCHEDULE);
        ReportSchedule s = scheduleRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("Planning introuvable : " + id));
        Map<String, Object> before = scheduleValues(s);
        reportRepository.detachSchedule(id);
        scheduleRepository.delete(s);
        audit.record(AuditEvent.of(AuditModule.REPORTS, AuditAction.DELETE).actor(actor)
                .object("ReportSchedule", id)
                .summary("Suppression du planning « " + s.getReportType().label() + " » (les rapports déjà générés sont conservés)")
                .oldValues(before));
    }

    // ── Interne ───────────────────────────────────────────────────────────────

    private Report createReport(ReportType type, ReportPeriod period, boolean compare, ReportTrigger trigger,
                                ReportSchedule schedule, AppUser actor, String actorName) {
        LocalDateTime now = LocalDateTime.now();
        ReportContent content;
        String json;
        try {
            content = builders.build(type, period, compare);
            Map<String, Object> root = content.toMap(type.name(), type.label(), titleFor(type, period), period,
                    compare ? period.previous() : null, actorName, now);
            json = MAPPER.writeValueAsString(root);
        } catch (Exception e) {
            log.error("Génération du rapport {} impossible ({} → {}): {}", type, period.from(), period.to(), e.toString());
            tryAuditFailure(type, period, actor, actorName, e);
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "La génération du rapport a échoué : " + (e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage()));
        }
        Report r = new Report();
        r.setReportType(type);
        r.setTitle(titleFor(type, period));
        r.setPeriodStart(period.from());
        r.setPeriodEnd(period.to());
        r.setComparePrevious(compare);
        r.setTrigger(trigger);
        r.setSchedule(schedule);
        r.setStatus(ReportStatus.COMPLETED);
        r.setSummary(truncate(content.summaryLine(), 1000));
        r.setResultJson(json);
        r.setGeneratedBy(actor);
        r.setGeneratedByUsername(actorName);
        r.setGeneratedAt(now);
        return reportRepository.saveAndFlush(r);
    }

    private void tryAuditFailure(ReportType type, ReportPeriod period, AppUser actor, String actorName, Exception e) {
        try {
            AuditEvent event = AuditEvent.of(AuditModule.REPORTS, AuditAction.GENERATE)
                    .object("ReportType", type.name())
                    .result(AuditResult.FAILURE)
                    .summary("Échec de génération du rapport « " + type.label() + " » du "
                            + DAY.format(period.from()) + " au " + DAY.format(period.to().minusNanos(1)));
            if (actor != null) {
                event.actor(actor);
            } else {
                event.actorUsername(actorName);
            }
            audit.recordIndependent(event);
        } catch (RuntimeException ex) {
            log.error("Audit: échec de génération non tracé: {}", ex.toString());
        }
    }

    static String titleFor(ReportType type, ReportPeriod period) {
        return type.label() + " — du " + DAY.format(period.from()) + " au " + DAY.format(period.to().minusNanos(1));
    }

    private Report getOrThrow(Long id) {
        return reportRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("Rapport introuvable : " + id));
    }

    private ReportItem toItem(Report r) {
        String by = r.getGeneratedByUsername() != null ? r.getGeneratedByUsername()
                : (r.getGeneratedBy() != null ? r.getGeneratedBy().getUsername() : null);
        return new ReportItem(r.getId(), r.getReportType(), r.getReportType().label(), r.getTitle(),
                r.getPeriodStart(), r.getPeriodEnd(), r.isComparePrevious(), r.getTrigger(), r.getStatus(),
                r.getSummary(), by, r.getGeneratedAt());
    }

    private ScheduleResponse toSchedule(ReportSchedule s) {
        LocalDateTime last = reportRepository.findTopByScheduleIdOrderByGeneratedAtDesc(s.getId())
                .map(Report::getGeneratedAt).orElse(null);
        String by = s.getCreatedByUsername() != null ? s.getCreatedByUsername()
                : (s.getCreatedBy() != null ? s.getCreatedBy().getUsername() : null);
        return new ScheduleResponse(s.getId(), s.getReportType(), s.getReportType().label(), s.getFrequency(),
                s.isEnabled(), by, s.getCreatedAt(), last);
    }

    private static Map<String, Object> auditValues(Report r) {
        Map<String, Object> v = new LinkedHashMap<>();
        v.put("type", r.getReportType());
        v.put("periodStart", r.getPeriodStart());
        v.put("periodEnd", r.getPeriodEnd());
        v.put("compare", r.isComparePrevious());
        v.put("trigger", r.getTrigger());
        return v;
    }

    private static Map<String, Object> scheduleValues(ReportSchedule s) {
        Map<String, Object> v = new LinkedHashMap<>();
        v.put("type", s.getReportType());
        v.put("frequency", s.getFrequency());
        v.put("enabled", s.isEnabled());
        return v;
    }

    private static String truncate(String s, int max) {
        return s == null || s.length() <= max ? s : s.substring(0, max);
    }
}
