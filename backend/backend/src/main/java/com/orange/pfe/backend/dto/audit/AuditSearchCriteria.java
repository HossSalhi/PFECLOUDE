package com.orange.pfe.backend.dto.audit;

import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.audit.AuditResult;

import java.time.LocalDateTime;

/** Critères de recherche du journal d'audit ; tous les champs sont optionnels. {@code to} est exclusif. */
public record AuditSearchCriteria(
        Long actorUserId,
        String actorUsername,
        AuditModule module,
        AuditAction action,
        String objectType,
        String objectId,
        AuditResult result,
        LocalDateTime from,
        LocalDateTime to,
        String q
) {
    public static AuditSearchCriteria empty() {
        return new AuditSearchCriteria(null, null, null, null, null, null, null, null, null, null);
    }

    /** Même critères, mais restreints à un seul acteur (identifiant imposé, filtre par nom ignoré). */
    public AuditSearchCriteria restrictedToActor(Long userId) {
        return new AuditSearchCriteria(userId, null, module, action, objectType, objectId, result, from, to, q);
    }
}
