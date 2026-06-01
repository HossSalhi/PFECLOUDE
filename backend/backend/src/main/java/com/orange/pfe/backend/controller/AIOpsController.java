package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIOpsAnalyzeResponse;
import com.orange.pfe.backend.service.AIOpsOrchestrationService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/ai")
public class AIOpsController {

    private final AIOpsOrchestrationService aiOpsOrchestrationService;

    public AIOpsController(AIOpsOrchestrationService aiOpsOrchestrationService) {
        this.aiOpsOrchestrationService = aiOpsOrchestrationService;
    }

    @PostMapping("/predict")
    public ResponseEntity<AIOpsAnalyzeResponse> predictAndApply(
            @Valid @RequestBody AIOpsAnalyzeRequest request
    ) {
        return ResponseEntity.ok(aiOpsOrchestrationService.analyze(request));
    }
}
