package com.orange.pfe.backend.config;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** Faux actuateurs (la tranche @WebMvcTest n'en contient pas) : permet de distinguer un accès AUTORISÉ (200) d'un refus. */
@RestController
class ActuatorStubController {
    @GetMapping("/actuator/prometheus") String prometheus() { return "metrics"; }
    @GetMapping("/actuator/metrics") String metrics() { return "metrics-list"; }
    @GetMapping("/actuator/health") String health() { return "{\"status\":\"UP\"}"; }
    @GetMapping("/actuator/health/readiness") String readiness() { return "{\"status\":\"UP\"}"; }
}