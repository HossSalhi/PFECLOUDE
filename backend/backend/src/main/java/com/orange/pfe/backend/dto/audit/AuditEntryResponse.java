package com.orange.pfe.backend.dto.audit;

import com.orange.pfe.backend.model.audit.AuditLogEntry;

import java.time.LocalDateTime;

/**
 * Entrée d'audit exposée par l'API. {@code oldValues} / {@code newValues} sont du JSON assaini (chaîne) ; ils ne
 * sont renseignés que dans la vue détail et dans l'export.
 */
public record AuditEntryResponse(
        Long id,
        LocalDateTime occurredAt,
        Long actorUserId,
        String actorUsername,
        String actorRole,
        String module,
        String action,
        String objectType,
        String objectId,
        String summary,
        String result,
        String clientIp,
        String requestId,
        boolean hasChanges,
        String oldValues,
        String newValues
) {
    public static AuditEntryResponse from(AuditLogEntry e, boolean withValues) {
        boolean changes = e.getOldValues() != null || e.getNewValues() != null;
        return new AuditEntryResponse(
                e.getId(), e.getOccurredAt(), e.getActorUserId(), e.getActorUsername(), e.getActorRole(),
                e.getModule() == null ? null : e.getModule().name(),
                e.getAction() == null ? null : e.getAction().name(),
                e.getObjectType(), e.getObjectId(), e.getSummary(),
                e.getResult() == null ? null : e.getResult().name(),
                e.getClientIp(), e.getRequestId(), changes,
                withValues ? e.getOldValues() : null,
                withValues ? e.getNewValues() : null);
    }
}
