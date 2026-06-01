package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.RemediationOperationsSummaryResponse;
import com.orange.pfe.backend.service.remediation.RemediationOperationalSummaryService;
import io.swagger.v3.oas.annotations.Operation;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/operations/remediation")
@Validated
public class RemediationOperationsController {

    private final RemediationOperationalSummaryService remediationOperationalSummaryService;

    public RemediationOperationsController(RemediationOperationalSummaryService remediationOperationalSummaryService) {
        this.remediationOperationalSummaryService = remediationOperationalSummaryService;
    }

    @Operation(summary = "Operational summary of recent remediation reliability and safety signals")
    @GetMapping("/summary")
    public ResponseEntity<RemediationOperationsSummaryResponse> summary(
            @RequestParam(defaultValue = "24") @Min(1) @Max(8760) int hours
    ) {
        return ResponseEntity.ok(remediationOperationalSummaryService.buildSummary(hours));
    }
}
