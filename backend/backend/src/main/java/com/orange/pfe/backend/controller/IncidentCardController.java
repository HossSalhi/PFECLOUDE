package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.ExecutionEventRequest;
import com.orange.pfe.backend.dto.IncidentCardResponse;
import com.orange.pfe.backend.dto.IncidentVerificationRequest;
import com.orange.pfe.backend.dto.IncidentVerificationResponse;
import com.orange.pfe.backend.dto.TerminalEvidenceCollectRequest;
import com.orange.pfe.backend.dto.TerminalEvidenceRequest;
import com.orange.pfe.backend.dto.TerminalEvidenceResponse;
import com.orange.pfe.backend.service.IncidentCardService;
import com.orange.pfe.backend.service.TerminalEvidenceCollectorService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * LOT HELIOS-INCIDENT-IDENTITY-CARD-01 — API de la carte d'identité d'incident.
 * Montée sous /api/incidents/{id} (mêmes règles de sécurité que {@link IncidentController}).
 * Lecture : carte agrégée, preuves terminal, vérification. Écriture INTERNE (preuves/vérification)
 * destinée aux composants HELIOS producteurs ; aucune exécution de commande shell n'est exposée.
 */
@RestController
@RequestMapping("/api/incidents/{id}")
public class IncidentCardController {

    private final IncidentCardService cardService;
    private final TerminalEvidenceCollectorService collector;

    public IncidentCardController(IncidentCardService cardService,
                                  TerminalEvidenceCollectorService collector) {
        this.cardService = cardService;
        this.collector = collector;
    }

    @GetMapping("/card")
    public ResponseEntity<IncidentCardResponse> getCard(@PathVariable Long id) {
        return ResponseEntity.ok(cardService.buildCard(id));
    }

    @GetMapping("/evidence")
    public ResponseEntity<List<TerminalEvidenceResponse>> getEvidence(@PathVariable Long id) {
        return ResponseEntity.ok(cardService.listEvidence(id));
    }

    @PostMapping("/evidence")
    public ResponseEntity<TerminalEvidenceResponse> recordEvidence(
            @PathVariable Long id, @Valid @RequestBody TerminalEvidenceRequest req) {
        return ResponseEntity.status(HttpStatus.CREATED).body(cardService.recordEvidence(id, req));
    }

    @GetMapping("/verification")
    public ResponseEntity<IncidentVerificationResponse> getVerification(@PathVariable Long id) {
        IncidentVerificationResponse v = cardService.getVerification(id);
        return v == null ? ResponseEntity.noContent().build() : ResponseEntity.ok(v);
    }

    @PostMapping("/verification")
    public ResponseEntity<IncidentVerificationResponse> recordVerification(
            @PathVariable Long id, @Valid @RequestBody IncidentVerificationRequest req) {
        return ResponseEntity.status(HttpStatus.CREATED).body(cardService.recordVerification(id, req));
    }

    // LOT 04 — étape d'exécution réelle (success explicite, jamais déduit du code HTTP).
    @PostMapping("/execution-event")
    public ResponseEntity<Void> recordExecutionEvent(
            @PathVariable Long id, @Valid @RequestBody ExecutionEventRequest req) {
        cardService.recordExecutionEvent(id, req);
        return ResponseEntity.status(HttpStatus.CREATED).build();
    }

    // LOT 04 — enregistrement de preuves terminal à partir de sorties DÉJÀ collectées (aucune exécution ici).
    @PostMapping("/evidence/collect")
    public ResponseEntity<List<TerminalEvidenceResponse>> collectEvidence(
            @PathVariable Long id, @Valid @RequestBody TerminalEvidenceCollectRequest req) {
        List<TerminalEvidenceResponse> saved = collector.recordFromOutputs(
                id, req.vm(), req.phase(), req.scenario(), req.source(), req.outputs());
        return ResponseEntity.status(HttpStatus.CREATED).body(saved);
    }

    // LOT 04 — catalogue FIXE des commandes diagnostiques autorisées (lecture seule, non destructives).
    @GetMapping("/diagnostics")
    public ResponseEntity<List<String>> diagnostics(
            @PathVariable Long id, @RequestParam(defaultValue = "CPU") String scenario) {
        return ResponseEntity.ok(collector.catalog(scenario));
    }
}
