package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.AlertWebhookRequest;
import com.orange.pfe.backend.service.OrchestrationService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/alerts")
public class AlertController {

    private final OrchestrationService orchestrationService;

    public AlertController(OrchestrationService orchestrationService) {
        this.orchestrationService = orchestrationService;
    }

    @PostMapping("/webhook")
    public ResponseEntity<Void> receiveAlert(@Valid @RequestBody AlertWebhookRequest request) {
        orchestrationService.handleIncomingAlert(request);
        return ResponseEntity.accepted().build();
    }
}
