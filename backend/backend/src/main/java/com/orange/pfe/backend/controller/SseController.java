package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.service.SseEventService;
import com.orange.pfe.backend.service.SseTicketService;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.Map;

@RestController
@RequestMapping("/api/events")
public class SseController {

    private final SseEventService sseEventService;
    private final SseTicketService ticketService;

    public SseController(SseEventService sseEventService, SseTicketService ticketService) {
        this.sseEventService = sseEventService;
        this.ticketService = ticketService;
    }

    /** Réservé aux utilisateurs authentifiés (JWT dans l'en-tête, règle {@code anyRequest().authenticated()}). */
    @PostMapping("/ticket")
    public Map<String, Object> ticket() {
        return Map.of("ticket", ticketService.issue(), "expiresInSeconds", ticketService.ttlSeconds());
    }

    /** Public au sens de Spring Security, mais refusé (401) sans ticket valide, non expiré et non utilisé. */
    @GetMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public ResponseEntity<SseEmitter> stream(@RequestParam(name = "ticket", required = false) String ticket) {
        if (!ticketService.consume(ticket)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
        return ResponseEntity.ok(sseEventService.subscribe());
    }
}