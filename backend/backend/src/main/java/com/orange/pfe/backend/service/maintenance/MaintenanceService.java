package com.orange.pfe.backend.service.maintenance;

import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.HistoryResponse;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceRequest;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceResponse;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.ResourceResponse;
import com.orange.pfe.backend.dto.maintenance.MaintenanceFilter;
import com.orange.pfe.backend.dto.org.UserRef;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.maintenance.Maintenance;
import com.orange.pfe.backend.model.maintenance.MaintenanceChangeType;
import com.orange.pfe.backend.model.maintenance.MaintenanceHistory;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.repository.MaintenanceHistoryRepository;
import com.orange.pfe.backend.repository.MaintenanceRepository;
import com.orange.pfe.backend.repository.MaintenanceSearchRepository;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditSanitizer;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import com.orange.pfe.backend.service.org.OrgAccessService;
import com.orange.pfe.backend.service.org.OrgPermission;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Planification et suivi des opérations de maintenance.
 *
 * <p><b>Module DÉCLARATIF.</b> Ce service ne dépend d'aucun composant de supervision, de remédiation, de
 * pilotage des VM ou d'orchestration, et n'écrit jamais le drapeau de maintenance de l'hôte (lu par le watchdog) :
 * déclarer une ressource « en maintenance » est une information de gestion qui n'arrête ni la supervision ni les
 * mécanismes de sécurité (garanti par {@code MaintenanceIsolationTest}).</p>
 *
 * <p>Règles : période cohérente (fin &gt; début, 31 jours maximum, pas entièrement dans le passé à la création) ;
 * ressource connue (voir {@link MaintenanceResourceCatalog}) ; pas de chevauchement de deux maintenances actives sur
 * la même ressource ; modification complète tant que PLANIFIÉE, limitée (description, motif, responsable, fin) une
 * fois EN COURS, impossible après TERMINÉE/ANNULÉE ; chaque changement est conservé dans l'historique et dans le
 * journal d'audit.</p>
 */
@Service
public class MaintenanceService {

    static final Duration MAX_DURATION = Duration.ofDays(31);
    static final int MAX_PAGE_SIZE = 100;
    static final int CALENDAR_MAX_ROWS = 2_000;
    private static final DateTimeFormatter HUMAN = DateTimeFormatter.ofPattern("dd/MM/yyyy HH:mm");

    private final MaintenanceRepository repository;
    private final MaintenanceSearchRepository searchRepository;
    private final MaintenanceHistoryRepository historyRepository;
    private final AppUserRepository userRepository;
    private final MaintenanceResourceCatalog catalog;
    private final OrgAccessService access;
    private final AuditTrailService audit;

    public MaintenanceService(MaintenanceRepository repository,
                              MaintenanceSearchRepository searchRepository,
                              MaintenanceHistoryRepository historyRepository,
                              AppUserRepository userRepository,
                              MaintenanceResourceCatalog catalog,
                              OrgAccessService access,
                              AuditTrailService audit) {
        this.repository = repository;
        this.searchRepository = searchRepository;
        this.historyRepository = historyRepository;
        this.userRepository = userRepository;
        this.catalog = catalog;
        this.access = access;
        this.audit = audit;
    }

    /** Critères de la liste, tels que reçus de l'API. */
    public record Query(MaintenanceStatus status, MaintenanceResourceType resourceType, String resource,
                        Long responsibleId, LocalDateTime from, LocalDateTime to, String text) {}

    // ── Lecture ───────────────────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public Page<MaintenanceResponse> search(AppUser actor, Query q, int page, int size) {
        access.require(actor, OrgPermission.MAINTENANCE_VIEW);
        MaintenanceFilter filter = new MaintenanceFilter(q.status(), q.resourceType(), q.resource(),
                q.responsibleId(), q.from(), q.to(), q.text());
        int safeSize = Math.max(1, Math.min(size, MAX_PAGE_SIZE));
        return searchRepository.search(filter, PageRequest.of(Math.max(0, page), safeSize)).map(this::toResponse);
    }

    @Transactional(readOnly = true)
    public List<MaintenanceResponse> calendar(AppUser actor, LocalDateTime from, LocalDateTime to) {
        access.require(actor, OrgPermission.MAINTENANCE_VIEW);
        if (from == null || to == null || !to.isAfter(from)) {
            throw new IllegalArgumentException("Intervalle du calendrier invalide : 'to' doit suivre 'from'");
        }
        if (Duration.between(from, to).toDays() > 93) {
            throw new IllegalArgumentException("Le calendrier est limité à 93 jours");
        }
        return searchRepository.findInRange(MaintenanceFilter.range(from, to), CALENDAR_MAX_ROWS).stream()
                .map(this::toResponse).toList();
    }

    @Transactional(readOnly = true)
    public MaintenanceResponse get(AppUser actor, Long id) {
        access.require(actor, OrgPermission.MAINTENANCE_VIEW);
        return toResponse(getOrThrow(id));
    }

    @Transactional(readOnly = true)
    public List<HistoryResponse> history(AppUser actor, Long id) {
        access.require(actor, OrgPermission.MAINTENANCE_VIEW);
        getOrThrow(id);
        return historyRepository.findByMaintenanceIdOrderByChangedAtAscIdAsc(id).stream()
                .map(h -> new HistoryResponse(h.getId(), h.getChangedAt(), h.getChangedByUsername(),
                        h.getChangeType().name(), h.getDetails()))
                .toList();
    }

    /** Maintenances actuellement EN COURS (information de gestion uniquement). */
    @Transactional(readOnly = true)
    public List<MaintenanceResponse> inProgress(AppUser actor) {
        access.require(actor, OrgPermission.MAINTENANCE_VIEW);
        return repository.findByStatusOrderByStartAtAsc(MaintenanceStatus.IN_PROGRESS).stream()
                .map(this::toResponse).toList();
    }

    @Transactional(readOnly = true)
    public List<ResourceResponse> resources(AppUser actor) {
        access.require(actor, OrgPermission.MAINTENANCE_VIEW);
        return catalog.list();
    }

    // ── Écriture ──────────────────────────────────────────────────────────────

    @Transactional
    public MaintenanceResponse create(AppUser actor, MaintenanceRequest request) {
        access.require(actor, OrgPermission.MAINTENANCE_CREATE);
        String title = requireTitle(request.title());
        String resource = catalog.validateAndNormalize(request.resourceType(), request.resourceName());
        validatePeriod(request.startAt(), request.endAt(), true);
        AppUser responsible = request.responsibleId() == null ? null : requireEnabledUser(request.responsibleId());
        checkConflicts(request.resourceType(), resource, request.startAt(), request.endAt(), null);

        Maintenance m = new Maintenance();
        m.setTitle(title);
        m.setDescription(blankToNull(request.description()));
        m.setReason(blankToNull(request.reason()));
        m.setResourceType(request.resourceType());
        m.setResourceName(resource);
        m.setStartAt(request.startAt());
        m.setEndAt(request.endAt());
        m.setStatus(MaintenanceStatus.PLANNED);
        m.setCreatedBy(actor);
        m.setCreatedByUsername(actor.getUsername());
        m.setResponsible(responsible);
        m = repository.save(m);

        Map<String, Object> now = values(m);
        history(m, actor, MaintenanceChangeType.CREATED, null, now);
        audit.record(AuditEvent.of(AuditModule.MAINTENANCE, AuditAction.CREATE).actor(actor)
                .object("Maintenance", m.getId())
                .summary("Planification de la maintenance « " + title + " » sur " + resource)
                .newValues(now));
        return toResponse(m);
    }

    @Transactional
    public MaintenanceResponse update(AppUser actor, Long id, MaintenanceRequest request) {
        access.require(actor, OrgPermission.MAINTENANCE_UPDATE);
        Maintenance m = getOrThrow(id);
        if (m.getStatus().isFinal()) {
            throw new IllegalStateException("Une maintenance terminée ou annulée ne peut plus être modifiée");
        }
        String title = requireTitle(request.title());
        String resource = catalog.validateAndNormalize(request.resourceType(), request.resourceName());
        validatePeriod(request.startAt(), request.endAt(), false);
        if (m.getStatus() == MaintenanceStatus.IN_PROGRESS) {
            boolean sameResource = request.resourceType() == m.getResourceType()
                    && resource.equalsIgnoreCase(m.getResourceName());
            boolean sameStart = Objects.equals(seconds(request.startAt()), seconds(m.getStartAt()));
            if (!sameResource || !sameStart) {
                throw new IllegalStateException(
                        "La ressource et la date de début ne peuvent plus être modifiées une fois la maintenance démarrée");
            }
            if (!request.endAt().isAfter(LocalDateTime.now())) {
                throw new IllegalArgumentException("La nouvelle date de fin doit être dans le futur");
            }
        }
        AppUser responsible = request.responsibleId() == null ? null : requireEnabledUser(request.responsibleId());
        checkConflicts(request.resourceType(), resource, request.startAt(), request.endAt(), m.getId());

        Map<String, Object> before = values(m);
        m.setTitle(title);
        m.setDescription(blankToNull(request.description()));
        m.setReason(blankToNull(request.reason()));
        m.setResourceType(request.resourceType());
        m.setResourceName(resource);
        m.setStartAt(request.startAt());
        m.setEndAt(request.endAt());
        m.setResponsible(responsible);
        Map<String, Object> after = values(m);

        Map<String, Object> oldChanged = new LinkedHashMap<>();
        Map<String, Object> newChanged = new LinkedHashMap<>();
        for (Map.Entry<String, Object> e : after.entrySet()) {
            Object previous = before.get(e.getKey());
            if (!Objects.equals(String.valueOf(previous), String.valueOf(e.getValue()))) {
                oldChanged.put(e.getKey(), previous);
                newChanged.put(e.getKey(), e.getValue());
            }
        }
        if (newChanged.isEmpty()) {
            return toResponse(m);
        }
        m = repository.save(m);
        history(m, actor, MaintenanceChangeType.UPDATED, oldChanged, newChanged);
        audit.record(AuditEvent.of(AuditModule.MAINTENANCE, AuditAction.UPDATE).actor(actor)
                .object("Maintenance", id)
                .summary("Modification de la maintenance « " + title + " »")
                .oldValues(oldChanged).newValues(newChanged));
        return toResponse(m);
    }

    /** Déclare la ressource « en maintenance » (information de gestion : aucune action sur la supervision). */
    @Transactional
    public MaintenanceResponse start(AppUser actor, Long id) {
        access.require(actor, OrgPermission.MAINTENANCE_UPDATE);
        Maintenance m = getOrThrow(id);
        if (m.getStatus() != MaintenanceStatus.PLANNED) {
            throw new IllegalStateException("Seule une maintenance planifiée peut être démarrée");
        }
        m.setStatus(MaintenanceStatus.IN_PROGRESS);
        m.setActualStartAt(LocalDateTime.now());
        m = repository.save(m);
        history(m, actor, MaintenanceChangeType.STARTED, single("status", MaintenanceStatus.PLANNED),
                single("status", MaintenanceStatus.IN_PROGRESS));
        audit.record(AuditEvent.of(AuditModule.MAINTENANCE, AuditAction.START).actor(actor)
                .object("Maintenance", id)
                .summary("Démarrage de la maintenance « " + m.getTitle() + " » sur " + m.getResourceName())
                .oldValues(single("status", MaintenanceStatus.PLANNED))
                .newValues(single("status", MaintenanceStatus.IN_PROGRESS)));
        return toResponse(m);
    }

    @Transactional
    public MaintenanceResponse complete(AppUser actor, Long id) {
        access.require(actor, OrgPermission.MAINTENANCE_UPDATE);
        Maintenance m = getOrThrow(id);
        if (m.getStatus() != MaintenanceStatus.IN_PROGRESS) {
            throw new IllegalStateException("Seule une maintenance en cours peut être terminée");
        }
        m.setStatus(MaintenanceStatus.COMPLETED);
        m.setActualEndAt(LocalDateTime.now());
        m = repository.save(m);
        history(m, actor, MaintenanceChangeType.COMPLETED, single("status", MaintenanceStatus.IN_PROGRESS),
                single("status", MaintenanceStatus.COMPLETED));
        audit.record(AuditEvent.of(AuditModule.MAINTENANCE, AuditAction.COMPLETE).actor(actor)
                .object("Maintenance", id)
                .summary("Fin de la maintenance « " + m.getTitle() + " » sur " + m.getResourceName())
                .oldValues(single("status", MaintenanceStatus.IN_PROGRESS))
                .newValues(single("status", MaintenanceStatus.COMPLETED)));
        return toResponse(m);
    }

    @Transactional
    public MaintenanceResponse cancel(AppUser actor, Long id, String reason) {
        access.require(actor, OrgPermission.MAINTENANCE_CANCEL);
        Maintenance m = getOrThrow(id);
        if (m.getStatus().isFinal()) {
            throw new IllegalStateException("Cette maintenance est déjà terminée ou annulée");
        }
        MaintenanceStatus old = m.getStatus();
        m.setStatus(MaintenanceStatus.CANCELLED);
        m.setCancelReason(blankToNull(reason));
        m = repository.save(m);
        Map<String, Object> after = single("status", MaintenanceStatus.CANCELLED);
        after.put("reason", m.getCancelReason());
        history(m, actor, MaintenanceChangeType.CANCELLED, single("status", old), after);
        audit.record(AuditEvent.of(AuditModule.MAINTENANCE, AuditAction.CANCEL).actor(actor)
                .object("Maintenance", id)
                .summary("Annulation de la maintenance « " + m.getTitle() + " »")
                .oldValues(single("status", old)).newValues(after));
        return toResponse(m);
    }

    /** Suppression réservée (SUPER_ADMIN), uniquement pour une maintenance planifiée ou annulée ; la trace d'audit reste. */
    @Transactional
    public void delete(AppUser actor, Long id) {
        access.require(actor, OrgPermission.MAINTENANCE_DELETE);
        Maintenance m = getOrThrow(id);
        if (m.getStatus() != MaintenanceStatus.PLANNED && m.getStatus() != MaintenanceStatus.CANCELLED) {
            throw new IllegalStateException("Seule une maintenance planifiée ou annulée peut être supprimée");
        }
        Map<String, Object> before = values(m);
        historyRepository.deleteByMaintenanceId(id);
        repository.delete(m);
        audit.record(AuditEvent.of(AuditModule.MAINTENANCE, AuditAction.DELETE).actor(actor)
                .object("Maintenance", id)
                .summary("Suppression de la maintenance « " + m.getTitle() + " »")
                .oldValues(before));
    }

    // ── Règles ────────────────────────────────────────────────────────────────

    static void validatePeriod(LocalDateTime start, LocalDateTime end, boolean creation) {
        if (start == null || end == null) {
            throw new IllegalArgumentException("Les dates de début et de fin sont obligatoires");
        }
        if (!end.isAfter(start)) {
            throw new IllegalArgumentException("La date de fin doit être postérieure à la date de début");
        }
        if (Duration.between(start, end).compareTo(MAX_DURATION) > 0) {
            throw new IllegalArgumentException("Une maintenance ne peut pas dépasser 31 jours");
        }
        if (creation && !end.isAfter(LocalDateTime.now())) {
            throw new IllegalArgumentException("La période de maintenance est entièrement dans le passé");
        }
    }

    private void checkConflicts(MaintenanceResourceType type, String resource,
                                LocalDateTime start, LocalDateTime end, Long excludeId) {
        List<Maintenance> overlaps = repository.findOverlapping(type, resource, MaintenanceStatus.ACTIVE, start, end)
                .stream().filter(o -> excludeId == null || !o.getId().equals(excludeId)).toList();
        if (!overlaps.isEmpty()) {
            Maintenance o = overlaps.get(0);
            throw new IllegalStateException("Conflit de planification : la ressource « " + resource
                    + " » a déjà la maintenance « " + o.getTitle() + " » du " + HUMAN.format(o.getStartAt())
                    + " au " + HUMAN.format(o.getEndAt()));
        }
    }

    // ── Accès / mapping ───────────────────────────────────────────────────────

    private Maintenance getOrThrow(Long id) {
        return repository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("Maintenance introuvable : " + id));
    }

    private AppUser requireEnabledUser(Long id) {
        AppUser user = userRepository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("Responsable introuvable : " + id));
        if (!user.isEnabled()) {
            throw new IllegalArgumentException("L'utilisateur « " + user.getUsername() + " » est désactivé");
        }
        return user;
    }

    private void history(Maintenance m, AppUser actor, MaintenanceChangeType type,
                         Map<String, Object> oldValues, Map<String, Object> newValues) {
        Map<String, Object> details = new LinkedHashMap<>();
        if (oldValues != null) {
            details.put("old", oldValues);
        }
        if (newValues != null) {
            details.put("new", newValues);
        }
        historyRepository.save(new MaintenanceHistory(m, actor.getId(), actor.getUsername(), type,
                AuditSanitizer.toJson(details)));
    }

    private MaintenanceResponse toResponse(Maintenance m) {
        LocalDateTime now = LocalDateTime.now();
        UserRef creator = m.getCreatedBy() != null ? UserRef.of(m.getCreatedBy())
                : (m.getCreatedByUsername() == null ? null : new UserRef(null, m.getCreatedByUsername(), m.getCreatedByUsername()));
        return new MaintenanceResponse(m.getId(), m.getTitle(), m.getDescription(), m.getReason(),
                m.getResourceType(), m.getResourceName(), m.getStartAt(), m.getEndAt(), m.getStatus(),
                creator, UserRef.of(m.getResponsible()), m.getActualStartAt(), m.getActualEndAt(), m.getCancelReason(),
                m.getStatus() == MaintenanceStatus.PLANNED && !now.isBefore(m.getStartAt()),
                m.getStatus() == MaintenanceStatus.IN_PROGRESS && now.isAfter(m.getEndAt()),
                m.getCreatedAt(), m.getUpdatedAt());
    }

    private static Map<String, Object> values(Maintenance m) {
        Map<String, Object> v = new LinkedHashMap<>();
        v.put("title", m.getTitle());
        v.put("resource", m.getResourceType() + ":" + m.getResourceName());
        v.put("startAt", m.getStartAt());
        v.put("endAt", m.getEndAt());
        v.put("status", m.getStatus());
        v.put("responsible", m.getResponsible() == null ? null : m.getResponsible().getUsername());
        v.put("reason", m.getReason());
        v.put("description", m.getDescription());
        return v;
    }

    private static Map<String, Object> single(String key, Object value) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put(key, value);
        return m;
    }

    private static LocalDateTime seconds(LocalDateTime t) {
        return t == null ? null : t.truncatedTo(ChronoUnit.SECONDS);
    }

    private static String requireTitle(String title) {
        if (title == null || title.isBlank()) {
            throw new IllegalArgumentException("Le titre de la maintenance est obligatoire");
        }
        return title.trim();
    }

    private static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s.trim();
    }
}
