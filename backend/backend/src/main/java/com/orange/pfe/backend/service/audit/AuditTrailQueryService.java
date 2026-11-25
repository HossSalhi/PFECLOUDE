package com.orange.pfe.backend.service.audit;

import com.orange.pfe.backend.dto.audit.AuditEntryResponse;
import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditLogEntry;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.AuditLogRepository;
import com.orange.pfe.backend.repository.AuditLogSearchRepository;
import com.orange.pfe.backend.service.org.CsvSupport;
import com.orange.pfe.backend.service.org.OrgAccessService;
import com.orange.pfe.backend.service.org.OrgPermission;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.io.Writer;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Consultation et export du journal d'audit, avec contrôle des droits :
 * un utilisateur ordinaire ne voit QUE ses propres activités (le filtre est imposé côté serveur, quel que soit
 * ce que le client envoie) ; l'export est réservé aux rôles autorisés et est lui-même tracé.
 */
@Service
public class AuditTrailQueryService {

    public static final int EXPORT_MAX_ROWS = 50_000;
    static final int MAX_PAGE_SIZE = 200;

    private final AuditLogSearchRepository searchRepository;
    private final AuditLogRepository repository;
    private final OrgAccessService access;
    private final AuditTrailService auditTrailService;

    public AuditTrailQueryService(AuditLogSearchRepository searchRepository,
                                  AuditLogRepository repository,
                                  OrgAccessService access,
                                  AuditTrailService auditTrailService) {
        this.searchRepository = searchRepository;
        this.repository = repository;
        this.access = access;
        this.auditTrailService = auditTrailService;
    }

    @Transactional(readOnly = true)
    public Page<AuditEntryResponse> search(AppUser actor, AuditSearchCriteria criteria, int page, int size) {
        AuditSearchCriteria effective = restrict(actor, criteria);
        int safeSize = Math.max(1, Math.min(size, MAX_PAGE_SIZE));
        int safePage = Math.max(0, page);
        return searchRepository.search(effective, PageRequest.of(safePage, safeSize))
                .map(e -> AuditEntryResponse.from(e, false));
    }

    @Transactional(readOnly = true)
    public AuditEntryResponse get(AppUser actor, Long id) {
        access.require(actor, OrgPermission.AUDIT_VIEW_OWN);
        AuditLogEntry entry = repository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("Entrée d'audit introuvable : " + id));
        if (!access.has(actor, OrgPermission.AUDIT_VIEW_ALL) && !actor.getId().equals(entry.getActorUserId())) {
            throw new AccessDeniedException("Activité non autorisée");
        }
        return AuditEntryResponse.from(entry, true);
    }

    /**
     * Écrit l'export CSV dans {@code out} (BOM UTF-8 inclus) et trace l'opération. Retourne le nombre de lignes.
     */
    @Transactional
    public int exportCsv(AppUser actor, AuditSearchCriteria criteria, Writer out) throws IOException {
        access.require(actor, OrgPermission.AUDIT_EXPORT);
        AuditSearchCriteria effective = restrict(actor, criteria);
        List<AuditLogEntry> rows = searchRepository.searchForExport(effective, EXPORT_MAX_ROWS);

        out.write(CsvSupport.BOM);
        CsvSupport.writeRow(out, "id", "date", "utilisateur", "rôle", "module", "action", "type d'objet",
                "id objet", "résumé", "résultat", "adresse IP", "anciennes valeurs", "nouvelles valeurs");
        for (AuditLogEntry e : rows) {
            CsvSupport.writeRow(out, e.getId(), e.getOccurredAt(), e.getActorUsername(), e.getActorRole(),
                    e.getModule(), e.getAction(), e.getObjectType(), e.getObjectId(), e.getSummary(),
                    e.getResult(), e.getClientIp(), e.getOldValues(), e.getNewValues());
        }
        out.flush();

        Map<String, Object> filters = new LinkedHashMap<>();
        filters.put("lignes", rows.size());
        filters.put("module", effective.module());
        filters.put("action", effective.action());
        filters.put("utilisateur", effective.actorUsername());
        filters.put("du", effective.from());
        filters.put("au", effective.to());
        auditTrailService.record(AuditEvent.of(AuditModule.AUDIT, AuditAction.EXPORT)
                .actor(actor)
                .object("AuditLog", null)
                .summary("Export CSV du journal d'audit (" + rows.size() + " ligne(s))")
                .newValues(filters));
        return rows.size();
    }

    /** Impose les droits de consultation : un utilisateur sans « voir tout » est limité à ses propres activités. */
    AuditSearchCriteria restrict(AppUser actor, AuditSearchCriteria criteria) {
        access.require(actor, OrgPermission.AUDIT_VIEW_OWN);
        AuditSearchCriteria c = criteria == null ? AuditSearchCriteria.empty() : criteria;
        if (!access.has(actor, OrgPermission.AUDIT_VIEW_ALL)) {
            return c.restrictedToActor(actor.getId());
        }
        return c;
    }
}
