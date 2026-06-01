package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.FaultCommandRequest;
import com.orange.pfe.backend.dto.FaultStatusResponse;
import com.orange.pfe.backend.service.FaultInjectionService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/test/faults")
public class FaultInjectionController {

    private final FaultInjectionService faultInjectionService;

    public FaultInjectionController(FaultInjectionService faultInjectionService) {
        this.faultInjectionService = faultInjectionService;
    }

    @GetMapping
    public FaultStatusResponse getFaults() {
        return new FaultStatusResponse(faultInjectionService.getAllStates());
    }

    @PostMapping
    public FaultStatusResponse toggleFault(@Valid @RequestBody FaultCommandRequest request) {
        faultInjectionService.setFault(request.getScenario(), request.getEnabled());
        return new FaultStatusResponse(faultInjectionService.getAllStates());
    }

    @PostMapping("/reset")
    public FaultStatusResponse resetFaults() {
        faultInjectionService.resetAll();
        return new FaultStatusResponse(faultInjectionService.getAllStates());
    }
}