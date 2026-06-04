package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.TenantContext;
import com.orange.pfe.backend.dto.*;
import com.orange.pfe.backend.exception.*;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.rules.IncidentBusinessRules;
import org.springframework.data.domain.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class IncidentServiceImpl implements IncidentService {

    private final IncidentRepository repository;
    private final IncidentHistoryService historyService;
    private final NotificationService notificationService;
    private final TenantContext tenantContext;

    public IncidentServiceImpl(
            IncidentRepository repository,
            IncidentHistoryService historyService,
            NotificationService notificationService,
            TenantContext tenantContext
    ) {
        this.repository = repository;
        this.historyService = historyService;
        this.notificationService = notificationService;
        this.tenantContext = tenantContext;
    }

    // Matches both "confidence=0.44" (legacy) and "confidence 0.44" (explanation prose).
    private static final Pattern CONFIDENCE_PATTERN = Pattern.compile("confidence[=\\s]([0-9.]+)");

    private Double extractConfidence(String text) {
        if (text == null) return null;
        Matcher m = CONFIDENCE_PATTERN.matcher(text);
        if (m.find()) {
            try { return Double.parseDouble(m.group(1)); } catch (NumberFormatException ignored) {}
        }
        return null;
    }

    private IncidentResponse map(Incident i) {
        return new IncidentResponse(
                i.getId(),
                i.getTitle(),
                i.getDescription(),
                i.getExplanation(),
                i.getSeverity(),
                i.getStatus(),
                i.getSource(),
                i.getIncidentType(),
                extractConfidence(i.getExplanation()),
                i.getNamespace(),
                i.getApp(),
                i.getCreatedAt(),
                i.getUpdatedAt()
        );
    }

    @Override
    @Transactional
    public IncidentResponse createIncident(IncidentRequest r) {
        Incident i = new Incident();
        i.setTitle(r.getTitle());
        i.setDescription(r.getDescription());
        i.setSeverity(r.getSeverity());
        i.setSource(r.getSource());
        i.setStatus(IncidentStatus.NEW);

        Incident saved = repository.save(i);

        historyService.record(saved, IncidentStatus.NEW, "Incident créé");
        notificationService.notifyNewIncident(saved);

        return map(saved);
    }

    @Override
    public IncidentResponse getById(Long id) {
        return map(getIncident(id));
    }

    @Override
    public Page<IncidentResponse> getAll(int page, int size, IncidentStatus status) {
        PageRequest pageable = PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "createdAt"));
        Set<String> namespaces = tenantContext.getAccessibleNamespaces();

        if (tenantContext.isRestricted()) {
            if (status != null) {
                return repository.findByStatusAndNamespaceIn(status, namespaces, pageable).map(this::map);
            }
            return repository.findByNamespaceIn(namespaces, pageable).map(this::map);
        }

        if (status != null) {
            return repository.findByStatus(status, pageable).map(this::map);
        }
        return repository.findAll(pageable).map(this::map);
    }

    @Override
    @Transactional
    public IncidentResponse updateStatus(Long id, IncidentStatus status) {
        Incident i = getIncident(id);
        IncidentBusinessRules.checkStatusTransition(i.getStatus(), status);

        IncidentStatus oldStatus = i.getStatus();
        i.setStatus(status);
        Incident saved = repository.save(i);

        historyService.record(saved, status, "Changement de statut : " + oldStatus + " → " + status);
        notificationService.notifyIncidentUpdated(saved);

        return map(saved);
    }

    @Override
    @Transactional
    public IncidentResponse patchIncident(Long id, IncidentPatchRequest r) {
        Incident i = getIncident(id);
        IncidentBusinessRules.checkModifiable(i);
        IncidentBusinessRules.checkSeverityChange(i, r.getSeverity());

        boolean modified = false;
        if (r.getTitle() != null)    { i.setTitle(r.getTitle());       modified = true; }
        if (r.getSeverity() != null) { i.setSeverity(r.getSeverity()); modified = true; }

        Incident saved = repository.save(i);
        if (modified) {
            historyService.record(saved, saved.getStatus(), "Modification manuelle de l'incident");
            notificationService.notifyIncidentUpdated(saved);
        }
        return map(saved);
    }

    @Override
    @Transactional
    public void archiveIncident(Long id) {
        Incident i = getIncident(id);
        if (i.getStatus() == IncidentStatus.ARCHIVED)
            throw new InvalidIncidentStateException("Incident already archived");

        i.setStatus(IncidentStatus.ARCHIVED);
        Incident saved = repository.save(i);
        historyService.record(saved, IncidentStatus.ARCHIVED, "Incident archivé");
    }

    private Incident getIncident(Long id) {
        return repository.findById(id)
                .orElseThrow(() -> new IncidentNotFoundException(id));
    }
}
