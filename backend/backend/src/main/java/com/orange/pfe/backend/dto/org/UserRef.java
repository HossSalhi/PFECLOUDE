package com.orange.pfe.backend.dto.org;

import com.orange.pfe.backend.model.user.AppUser;

/**
 * Référence légère vers un utilisateur HELIOS existant (identifiant + nom d'affichage) : ne divulgue aucune donnée
 * sensible (e-mail, 2FA, tentatives…). Réutilisée par les quatre modules.
 */
public record UserRef(Long id, String username, String displayName) {

    public static UserRef of(AppUser u) {
        if (u == null) {
            return null;
        }
        return new UserRef(u.getId(), u.getUsername(), displayNameOf(u));
    }

    public static String displayNameOf(AppUser u) {
        String first = u.getFirstName() == null ? "" : u.getFirstName().trim();
        String last = u.getLastName() == null ? "" : u.getLastName().trim();
        String full = (first + " " + last).trim();
        return full.isEmpty() ? u.getUsername() : full;
    }
}
