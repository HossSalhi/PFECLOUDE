package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.AnomalyResponse;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.AssignmentFeedback;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.AssignmentRecommendation;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Capabilities;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.MaintenanceWindowRequest;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.MaintenanceWindowResponse;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.ReportSummaryResponse;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TaskRecommendationRequest;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TeamSummaryAiResponse;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TelemetryAnalysisRequest;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TelemetryAnalysisView;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TelemetryRunSummary;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.aiassist.AiCapabilitiesService;
import com.orange.pfe.backend.service.aiassist.AiSummaryService;
import com.orange.pfe.backend.service.aiassist.AuditAnomalyService;
import com.orange.pfe.backend.service.aiassist.MaintenanceWindowAdvisor;
import com.orange.pfe.backend.service.aiassist.TaskAssignmentAdvisor;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryAnalysisService;
import org.springframework.data.domain.Page;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * Assistance IA des modules de gestion : suggestions et textes explicatifs UNIQUEMENT (aucune action). Chaque appel est
 * déclenché par un bouton ; le droit (administrateur, ou chef actif de l'équipe visée) est vérifié par les services.
 */
@RestController
@RequestMapping("/api/ai-assist")
@Tag(name = "Assistance IA", description = "Suggestions IA en lecture seule, à la demande, désactivées par défaut")
public class AiAssistController {

    private final AiCapabilitiesService capabilities;
    private final AiSummaryService summaries;
    private final AuditAnomalyService anomalies;
    private final TaskAssignmentAdvisor assignment;
    private final MaintenanceWindowAdvisor windows;
    private final TelemetryAnalysisService telemetry;

    public AiAssistController(AiCapabilitiesService capabilities, AiSummaryService summaries, AuditAnomalyService anomalies,
                              TaskAssignmentAdvisor assignment, MaintenanceWindowAdvisor windows, TelemetryAnalysisService telemetry) {
        this.capabilities = capabilities;
        this.summaries = summaries;
        this.anomalies = anomalies;
        this.assignment = assignment;
        this.windows = windows;
        this.telemetry = telemetry;
    }

    @Operation(summary = "Fonctions IA proposées à l'utilisateur (aucun appel IA)")
    @GetMapping("/capabilities")
    public Capabilities capabilities(@AuthenticationPrincipal AppUser actor) {
        return capabilities.capabilities(actor);
    }

    @Operation(summary = "Résumé IA d'un rapport généré (administrateurs)")
    @PostMapping("/reports/{id}/summary")
    public ReportSummaryResponse reportSummary(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return summaries.reportSummary(actor, id);
    }

    @Operation(summary = "Résumé IA de l'activité d'une équipe (administrateurs ou chef actif de l'équipe)")
    @PostMapping("/teams/{teamId}/summary")
    public TeamSummaryAiResponse teamSummary(@AuthenticationPrincipal AppUser actor, @PathVariable Long teamId) {
        return summaries.teamSummary(actor, teamId);
    }

    @Operation(summary = "Anomalies du journal d'audit détectées par règles (sans IA)")
    @GetMapping("/audit/anomalies")
    public AnomalyResponse anomalies(@AuthenticationPrincipal AppUser actor, @RequestParam(required = false) Long teamId,
                                     @RequestParam(defaultValue = "7") int days) {
        return anomalies.detect(actor, teamId, days);
    }

    @Operation(summary = "Mêmes anomalies avec une phrase IA par constat (appel IA, à la demande)")
    @PostMapping("/audit/anomalies/explain")
    public AnomalyResponse explain(@AuthenticationPrincipal AppUser actor, @RequestParam(required = false) Long teamId,
                                   @RequestParam(defaultValue = "7") int days) {
        return anomalies.explain(actor, teamId, days);
    }

    @Operation(summary = "Recommandation d'assignation d'une tâche (score déterministe + justification IA)")
    @PostMapping("/tasks/recommendation")
    public AssignmentRecommendation recommend(@AuthenticationPrincipal AppUser actor, @RequestBody TaskRecommendationRequest request) {
        return assignment.recommend(actor, request);
    }

    @Operation(summary = "Trace l'acceptation ou le refus d'une suggestion d'assignation")
    @PostMapping("/tasks/recommendation/feedback")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void feedback(@AuthenticationPrincipal AppUser actor, @RequestBody AssignmentFeedback feedback) {
        assignment.feedback(actor, feedback);
    }

    @Operation(summary = "Analyse IA des logs et métriques : règles déterministes, synthèse IA, enregistrée dans l'historique")
    @PostMapping("/telemetry/analyses")
    public TelemetryAnalysisView analyse(@AuthenticationPrincipal AppUser actor, @RequestBody TelemetryAnalysisRequest request) {
        return telemetry.analyse(actor, request);
    }

    @Operation(summary = "Historique des analyses (administrateurs : tout ; chef d'équipe : ses équipes)")
    @GetMapping("/telemetry/analyses")
    public Page<TelemetryRunSummary> analyses(@AuthenticationPrincipal AppUser actor, @RequestParam(required = false) Long teamId,
                                              @RequestParam(defaultValue = "0") int page, @RequestParam(defaultValue = "10") int size) {
        return telemetry.history(actor, teamId, page, size);
    }

    @Operation(summary = "Relit une analyse enregistrée, à l'identique")
    @GetMapping("/telemetry/analyses/{id}")
    public TelemetryAnalysisView analysis(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return telemetry.get(actor, id);
    }

    @Operation(summary = "Fenêtres de maintenance recommandées (charge Prometheus + conflits + justification IA)")
    @PostMapping("/maintenance/window")
    public MaintenanceWindowResponse window(@AuthenticationPrincipal AppUser actor, @RequestBody MaintenanceWindowRequest request) {
        return windows.recommend(actor, request);
    }
}
