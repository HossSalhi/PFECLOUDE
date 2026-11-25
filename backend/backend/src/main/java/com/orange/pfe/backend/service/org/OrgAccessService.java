package com.orange.pfe.backend.service.org;

import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.audit.AuditResult;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;

import java.util.Arrays;
import java.util.Comparator;
import java.util.List;

/**
 * Applique la matrice {@link OrgPermissionPolicy} côté Backend. Toute action des modules « organisation » passe par
 * {@link #require} : un refus lève {@link AccessDeniedException} (HTTP 403 via le gestionnaire d'exceptions existant)
 * et est tracé dans le journal d'audit (résultat DENIED), indépendamment de la transaction appelante.
 */
@Service
public class OrgAccessService {

    private static final Logger log = LoggerFactory.getLogger(OrgAccessService.class);

    private final AuditTrailService auditTrailService;

    public OrgAccessService(AuditTrailService auditTrailService) {
        this.auditTrailService = auditTrailService;
    }

    public boolean has(AppUser actor, OrgPermission permission) {
        return actor != null && OrgPermissionPolicy.allows(actor.getRole(), permission);
    }

    /** Exige un utilisateur authentifié (principal {@link AppUser}) ; refuse les identités techniques ou anonymes. */
    public AppUser requireUser(AppUser actor) {
        if (actor == null) {
            throw new AccessDeniedException("Authentification requise");
        }
        return actor;
    }

    public void require(AppUser actor, OrgPermission permission) {
        requireUser(actor);
        if (!OrgPermissionPolicy.allows(actor.getRole(), permission)) {
            recordDenied(actor, permission);
            throw new AccessDeniedException("Permission requise : " + permission.name());
        }
    }

    /** Permissions effectives de l'utilisateur, triées, pour l'adaptation de l'interface. */
    public List<String> permissionNames(AppUser actor) {
        if (actor == null) {
            return List.of();
        }
        return OrgPermissionPolicy.permissionsOf(actor.getRole()).stream()
                .map(Enum::name)
                .sorted(Comparator.naturalOrder())
                .toList();
    }

    private void recordDenied(AppUser actor, OrgPermission permission) {
        try {
            String prefix = permission.name().substring(0, permission.name().indexOf('_'));
            AuditModule module = Arrays.stream(AuditModule.values())
                    .filter(m -> m.name().equals(prefix))
                    .findFirst()
                    .orElse(AuditModule.AUDIT);
            auditTrailService.recordIndependent(AuditEvent.of(module, AuditAction.ACCESS_DENIED)
                    .actor(actor)
                    .result(AuditResult.DENIED)
                    .summary("Permission refusée : " + permission.name()));
        } catch (RuntimeException e) {
            // Un refus doit rester un refus même si la trace n'a pas pu être écrite.
            log.error("Audit: refus non tracé ({}): {}", permission, e.toString());
        }
    }
}
