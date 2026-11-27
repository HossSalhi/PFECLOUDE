package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.audit.SelfHealingDtos.ActionLogs;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Listing;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Trace;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.audit.SelfHealingTraceService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDateTime;

/**
 * Trace d'auto-remédiation de l'Audit-trail : lecture seule, dérivée, réservée à {@code AUDIT_VIEW_ALL} (contrôle fait
 * par {@link SelfHealingTraceService}). Aucune écriture dans le journal d'audit.
 */
@RestController
@RequestMapping("/api/audit-trail/self-healing")
@Tag(name = "Audit : auto-remédiation", description = "Trace dérivée du cycle d'auto-remédiation, avec les logs de la VM (lecture seule)")
public class SelfHealingTraceController {

    private final SelfHealingTraceService service;

    public SelfHealingTraceController(SelfHealingTraceService service) {
        this.service = service;
    }

    @Operation(summary = "Incidents ayant déclenché une remédiation dans la période (100 au plus)")
    @GetMapping
    public Listing list(@AuthenticationPrincipal AppUser actor,
                        @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime from,
                        @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime to,
                        @RequestParam(defaultValue = "50") int limit) {
        return service.list(actor, from, to, limit);
    }

    @Operation(summary = "Chronologie, actions, vérifications et relevés VM d'un incident")
    @GetMapping("/{incidentId}")
    public Trace trace(@AuthenticationPrincipal AppUser actor, @PathVariable Long incidentId) {
        return service.trace(actor, incidentId);
    }

    @Operation(summary = "Logs de la VM ou du pod concerné, ±5 minutes autour de l'exécution de l'action")
    @GetMapping("/{incidentId}/actions/{actionId}/logs")
    public ActionLogs logs(@AuthenticationPrincipal AppUser actor, @PathVariable Long incidentId, @PathVariable Long actionId) {
        return service.actionLogs(actor, incidentId, actionId);
    }
}
