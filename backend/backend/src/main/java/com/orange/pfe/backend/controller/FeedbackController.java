package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.exception.IncidentNotFoundException;
import com.orange.pfe.backend.repository.IncidentRepository;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/feedback")
@Validated
public class FeedbackController {

    private static final Logger log = LoggerFactory.getLogger(FeedbackController.class);

    private final IncidentRepository incidentRepository;

    public FeedbackController(IncidentRepository incidentRepository) {
        this.incidentRepository = incidentRepository;
    }

    @PostMapping("/incident/{id}")
    public ResponseEntity<Map<String, Object>> submitFeedback(
            @PathVariable Long id,
            @Valid @RequestBody FeedbackRequest request) {

        incidentRepository.findById(id)
                .orElseThrow(() -> new IncidentNotFoundException(id));

        log.info("FEEDBACK incident={} correct={} comment='{}' operator='{}'",
                id, request.correct(), request.comment(), request.operator());

        return ResponseEntity.ok(Map.of(
                "incidentId", id,
                "correct",    request.correct(),
                "recorded",   true,
                "message",    request.correct()
                        ? "Merci — cette décision sera renforcée dans le prochain entraînement"
                        : "Merci — cette décision sera corrigée dans le prochain entraînement"
        ));
    }

    public record FeedbackRequest(
            @NotNull boolean correct,
            String comment,
            String operator
    ) {}
}
