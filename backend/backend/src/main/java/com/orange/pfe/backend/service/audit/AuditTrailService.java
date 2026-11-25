package com.orange.pfe.backend.service.audit;

import com.orange.pfe.backend.model.audit.AuditLogEntry;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.AuditLogRepository;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.context.request.RequestAttributes;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.time.LocalDateTime;

/**
 * Écriture du journal d'audit (ajout seul).
 *
 * <ul>
 *   <li>{@link #record} : s'exécute dans la transaction de l'appelant — la trace est atomique avec la modification
 *       (si la modification est annulée, la trace aussi). Utilisé par les nouveaux modules : « pas de changement
 *       sans trace ».</li>
 *   <li>{@link #recordIndependent} : transaction séparée — pour tracer un refus ou un échec alors que la transaction
 *       appelante va être annulée.</li>
 *   <li>{@link #recordQuietly} : ne lève JAMAIS d'exception — pour les flux existants (connexion, gestion des
 *       utilisateurs) qui ne doivent pas être cassés par un incident d'audit.</li>
 * </ul>
 *
 * Aucun mot de passe, token ou secret n'est écrit : voir {@link AuditSanitizer}.
 */
@Service
public class AuditTrailService {

    private static final Logger log = LoggerFactory.getLogger(AuditTrailService.class);
    private static final String REQUEST_ID_MDC_KEY = "requestId";

    private final AuditLogRepository repository;

    public AuditTrailService(AuditLogRepository repository) {
        this.repository = repository;
    }

    @Transactional
    public AuditLogEntry record(AuditEvent event) {
        return repository.save(toEntry(event));
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public AuditLogEntry recordIndependent(AuditEvent event) {
        return repository.save(toEntry(event));
    }

    public void recordQuietly(AuditEvent event) {
        try {
            repository.save(toEntry(event));
        } catch (RuntimeException e) {
            log.error("Audit: écriture impossible (module={}, action={}): {}",
                    event == null ? null : event.module(), event == null ? null : event.action(), e.toString());
        }
    }

    AuditLogEntry toEntry(AuditEvent e) {
        AppUser actor = e.actor() != null ? e.actor() : currentUser();
        String username = e.actorUsername() != null ? e.actorUsername()
                : actor != null ? actor.getUsername() : technicalPrincipal();
        String role = actor != null && actor.getRole() != null ? actor.getRole().name()
                : (username != null && actor == null && isTechnicalContext() ? "SERVICE" : null);
        return new AuditLogEntry(
                LocalDateTime.now(),
                actor == null ? null : actor.getId(),
                limit(username, 50),
                role,
                e.module(),
                e.action(),
                limit(e.objectType(), 60),
                limit(e.objectId(), 64),
                limit(e.summary(), 500),
                AuditSanitizer.toJson(e.oldValues()),
                AuditSanitizer.toJson(e.newValues()),
                e.result(),
                currentClientIp(),
                limit(MDC.get(REQUEST_ID_MDC_KEY), 64));
    }

    private static AppUser currentUser() {
        Authentication a = SecurityContextHolder.getContext().getAuthentication();
        if (a != null && a.getPrincipal() instanceof AppUser u) {
            return u;
        }
        return null;
    }

    private static boolean isTechnicalContext() {
        Authentication a = SecurityContextHolder.getContext().getAuthentication();
        return a != null && a.getPrincipal() instanceof String s && !"anonymousUser".equals(s);
    }

    private static String technicalPrincipal() {
        Authentication a = SecurityContextHolder.getContext().getAuthentication();
        if (a != null && a.getPrincipal() instanceof String s && !"anonymousUser".equals(s)) {
            return s;
        }
        return null;
    }

    private static String currentClientIp() {
        try {
            RequestAttributes ra = RequestContextHolder.getRequestAttributes();
            if (ra instanceof ServletRequestAttributes sra) {
                HttpServletRequest req = sra.getRequest();
                String xff = req.getHeader("X-Forwarded-For");
                String ip = (xff != null && !xff.isBlank()) ? xff.split(",")[0].trim() : req.getRemoteAddr();
                return limit(ip, 45);
            }
        } catch (RuntimeException ignored) {
            // hors requête HTTP (tâche planifiée, test) : pas d'adresse
        }
        return null;
    }

    static String limit(String s, int max) {
        if (s == null) {
            return null;
        }
        return s.length() <= max ? s : s.substring(0, max);
    }
}
