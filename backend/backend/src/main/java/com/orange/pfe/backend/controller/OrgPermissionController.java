package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.org.OrgAccessService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Expose au Frontend les permissions effectives de l'utilisateur connecté sur les modules « organisation »,
 * afin d'adapter les écrans et les actions. L'application réelle des droits reste côté Backend.
 */
@RestController
@RequestMapping("/api/org/permissions")
@Tag(name = "Permissions des modules", description = "Permissions de l'utilisateur connecté (maintenance, audit, rapports, équipes)")
public class OrgPermissionController {

    private final OrgAccessService access;

    public OrgPermissionController(OrgAccessService access) {
        this.access = access;
    }

    @Operation(summary = "Permissions de l'utilisateur connecté")
    @GetMapping("/me")
    public Map<String, Object> me(@AuthenticationPrincipal AppUser actor) {
        if (actor == null) {
            throw new AccessDeniedException("Authentification requise");
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("username", actor.getUsername());
        body.put("role", actor.getRole().name());
        body.put("permissions", access.permissionNames(actor));
        return body;
    }
}
