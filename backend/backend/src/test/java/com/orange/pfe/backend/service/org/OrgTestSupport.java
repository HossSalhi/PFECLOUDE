package com.orange.pfe.backend.service.org;

import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;

/** Outils communs aux tests des modules « organisation » : création d'utilisateurs HELIOS de test. */
public final class OrgTestSupport {

    /** Même propriété que les autres tests d'intégration du projet : le contexte Spring de test est ainsi partagé. */
    public static final String JWT_TEST_PROPERTY = "jwt.secret=test-only-secret-0123456789-0123456789-0123456789-0123456789";

    private OrgTestSupport() {
    }

    public static AppUser user(AppUserRepository repo, String username, UserRole role) {
        AppUser u = new AppUser();
        u.setUsername(username);
        u.setEmail(username + "@test.local");
        u.setPassword("not-a-real-hash");
        u.setRole(role);
        u.setEnabled(true);
        u.setFirstName("Prénom-" + username);
        u.setLastName("Nom");
        return repo.saveAndFlush(u);
    }
}
