package com.orange.pfe.backend.config;

import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;

import java.util.Collections;
import java.util.Set;

/**
 * Reads the current authenticated user's namespace scope from SecurityContextHolder.
 *
 * SUPER_ADMIN and ADMIN users are unrestricted (see all namespaces).
 * USER accounts with a non-empty allowedNamespaces set only see data in those namespaces.
 */
@Component
public class TenantContext {

    /**
     * Returns the set of namespaces the current user may access.
     * An empty set means the user is unrestricted (sees everything).
     */
    public Set<String> getAccessibleNamespaces() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth == null || !auth.isAuthenticated()) {
            return Collections.emptySet();
        }
        Object principal = auth.getPrincipal();
        if (!(principal instanceof AppUser user)) {
            return Collections.emptySet();
        }
        if (user.getRole() == UserRole.SUPER_ADMIN || user.getRole() == UserRole.ADMIN) {
            return Collections.emptySet();
        }
        Set<String> ns = user.getAllowedNamespaces();
        return ns == null ? Collections.emptySet() : Collections.unmodifiableSet(ns);
    }

    /** Returns {@code true} when the current user is restricted to a specific namespace set. */
    public boolean isRestricted() {
        return !getAccessibleNamespaces().isEmpty();
    }
}
