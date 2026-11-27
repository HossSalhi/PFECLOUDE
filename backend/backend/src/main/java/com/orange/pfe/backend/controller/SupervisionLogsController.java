package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogLines;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogSources;
import com.orange.pfe.backend.service.supervision.LogViewerService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Visualiseur de logs (lignes réelles de Loki) : lecture seule, RÉSERVÉ aux administrateurs (règle dans
 * {@code SecurityConfig}) car les lignes brutes peuvent contenir des informations sensibles (les secrets visibles sont
 * masqués). Les utilisateurs ordinaires gardent les statistiques de logs, qui ne contiennent aucune ligne.
 */
@RestController
@RequestMapping("/api/supervision/logs")
@Tag(name = "Supervision : logs", description = "Lignes de logs d'une machine ou d'une application (administrateurs, lecture seule)")
public class SupervisionLogsController {

    private final LogViewerService viewer;

    public SupervisionLogsController(LogViewerService viewer) {
        this.viewer = viewer;
    }

    @Operation(summary = "Machines et applications dont les logs sont consultables")
    @GetMapping("/sources")
    public LogSources sources() {
        return viewer.sources();
    }

    @Operation(summary = "Lignes de logs d'une source, filtrées par niveau et par mot (200 au plus, 3 h au plus)")
    @GetMapping("/lines")
    public LogLines lines(@RequestParam(required = false) String source, @RequestParam(defaultValue = "ALL") String level,
                          @RequestParam(required = false) String q, @RequestParam(defaultValue = "30") int minutes,
                          @RequestParam(defaultValue = "100") int limit) {
        return viewer.lines(source, level, q, minutes, limit);
    }
}
