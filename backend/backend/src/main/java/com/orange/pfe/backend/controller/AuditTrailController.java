package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.audit.AuditEntryResponse;
import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.audit.AuditResult;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.audit.AuditTrailQueryService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.data.domain.Page;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.io.IOException;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Journal d'audit des ACTIVITÉS des utilisateurs (connexions, gestion des utilisateurs, équipes, tâches,
 * maintenance, rapports…). Distinct de {@code /api/audit/**} qui reste l'historique des transitions d'incidents.
 *
 * <p>Lecture et export uniquement : aucune API de modification ni de suppression n'existe.
 * Les droits sont appliqués par {@link AuditTrailQueryService}.</p>
 */
@RestController
@RequestMapping("/api/audit-trail")
@Tag(name = "Audit des activités", description = "Historique des activités utilisateurs (lecture seule, export CSV)")
public class AuditTrailController {

    private final AuditTrailQueryService queryService;

    public AuditTrailController(AuditTrailQueryService queryService) {
        this.queryService = queryService;
    }

    @Operation(summary = "Rechercher dans le journal d'audit (filtres : utilisateur, période, action, module…)")
    @GetMapping
    public Page<AuditEntryResponse> search(
            @AuthenticationPrincipal AppUser actor,
            @RequestParam(required = false) Long userId,
            @RequestParam(required = false) String username,
            @RequestParam(required = false) AuditModule module,
            @RequestParam(required = false) AuditAction action,
            @RequestParam(required = false) String objectType,
            @RequestParam(required = false) String objectId,
            @RequestParam(required = false) AuditResult result,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime from,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime to,
            @RequestParam(required = false) String q,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "25") int size) {
        AuditSearchCriteria criteria = new AuditSearchCriteria(userId, username, module, action, objectType, objectId,
                result, from, to, q);
        return queryService.search(actor, criteria, page, size);
    }

    @Operation(summary = "Détail d'une activité (anciennes et nouvelles valeurs)")
    @GetMapping("/{id}")
    public AuditEntryResponse get(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return queryService.get(actor, id);
    }

    @Operation(summary = "Exporter le journal d'audit filtré au format CSV")
    @GetMapping("/export")
    public ResponseEntity<byte[]> export(
            @AuthenticationPrincipal AppUser actor,
            @RequestParam(required = false) Long userId,
            @RequestParam(required = false) String username,
            @RequestParam(required = false) AuditModule module,
            @RequestParam(required = false) AuditAction action,
            @RequestParam(required = false) String objectType,
            @RequestParam(required = false) String objectId,
            @RequestParam(required = false) AuditResult result,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime from,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime to,
            @RequestParam(required = false) String q) throws IOException {
        AuditSearchCriteria criteria = new AuditSearchCriteria(userId, username, module, action, objectType, objectId,
                result, from, to, q);
        // Le CSV est produit en mémoire AVANT de toucher à la réponse : un refus (403) ou une erreur reste une
        // réponse d'erreur JSON normale, sans conflit avec un type de contenu déjà fixé.
        StringWriter csv = new StringWriter();
        queryService.exportCsv(actor, criteria, csv);
        String name = "audit-" + DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss").format(LocalDateTime.now()) + ".csv";
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + name + "\"")
                .contentType(new MediaType("text", "csv", StandardCharsets.UTF_8))
                .body(csv.toString().getBytes(StandardCharsets.UTF_8));
    }

    @Operation(summary = "Valeurs disponibles pour les filtres (modules, actions, résultats)")
    @GetMapping("/meta")
    public Map<String, Object> meta() {
        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("modules", Arrays.stream(AuditModule.values()).map(Enum::name).toList());
        meta.put("actions", Arrays.stream(AuditAction.values()).map(Enum::name).toList());
        meta.put("results", Arrays.stream(AuditResult.values()).map(Enum::name).toList());
        return meta;
    }
}
