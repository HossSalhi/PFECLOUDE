package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.HostIncidentIngestionRequest;
import com.orange.pfe.backend.dto.HostIncidentIngestionResponse;
import com.orange.pfe.backend.service.HostIncidentIngestionService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/**
 * LOT HELIOS-INCIDENT-CARD-EVENT-INTEGRATION-01 — ingestion interne d'incidents host-mesurés
 * (NODE_HIGH_CPU…) pour qu'ils disposent d'une carte persistée. Monté sous /api/incidents/** (mêmes règles
 * de sécurité que les autres endpoints incident). Destiné à un producteur HELIOS (pont Prometheus→backend
 * ou cycle Camunda-direct) — voir rapport 02 pour le raccordement. Aucune commande système n'est exposée.
 */
@RestController
@RequestMapping("/api/incidents/ingest")
public class HostIncidentIngestionController {

    private final HostIncidentIngestionService service;

    public HostIncidentIngestionController(HostIncidentIngestionService service) {
        this.service = service;
    }

    @PostMapping("/host")
    public ResponseEntity<HostIncidentIngestionResponse> ingestHost(
            @Valid @RequestBody HostIncidentIngestionRequest req) {
        HostIncidentIngestionResponse r = service.ingest(req);
        return ResponseEntity.status(r.created() ? HttpStatus.CREATED : HttpStatus.OK).body(r);
    }
}
