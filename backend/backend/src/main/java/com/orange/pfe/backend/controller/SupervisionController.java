package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.supervision.SupervisionDtos.Correlation;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.HostsOverview;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogStats;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.MeaningEntry;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.Metric;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.SeriesResponse;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.TopErrors;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaIncidents;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaInstances;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaOverview;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaSteps;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.GrafanaConfig;
import com.orange.pfe.backend.service.supervision.CamundaSupervisionService;
import com.orange.pfe.backend.service.supervision.GrafanaConfigService;
import com.orange.pfe.backend.service.supervision.SupervisionService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * Supervision en lecture seule, ouverte à tous les utilisateurs connectés (règle dans {@code SecurityConfig}). Aucune
 * requête libre : la période, la métrique et le nombre de lignes sont les seuls paramètres, et sont bornés.
 */
@RestController
@RequestMapping("/api/supervision")
@Tag(name = "Supervision", description = "Métriques Prometheus, statistiques de logs Loki, rapprochement avec les incidents")
public class SupervisionController {

    private final SupervisionService service;

    private final CamundaSupervisionService camunda;
    private final GrafanaConfigService grafana;

    public SupervisionController(SupervisionService service, CamundaSupervisionService camunda, GrafanaConfigService grafana) {
        this.camunda = camunda;
        this.grafana = grafana;
        this.service = service;
    }

    @Operation(summary = "Machines (CPU, mémoire, disque) et cibles surveillées")
    @GetMapping("/hosts")
    public HostsOverview hosts() {
        return service.hosts();
    }

    @Operation(summary = "Série de métriques par machine sur la période (5 à 1440 minutes)")
    @GetMapping("/series")
    public SeriesResponse series(@RequestParam(defaultValue = "CPU") Metric metric, @RequestParam(defaultValue = "60") int minutes) {
        return service.series(metric, minutes);
    }

    @Operation(summary = "Volume de logs par niveau, par machine et par application")
    @GetMapping("/logs/stats")
    public LogStats logStats(@RequestParam(defaultValue = "60") int minutes) {
        return service.logStats(minutes);
    }

    @Operation(summary = "Messages d'erreur les plus fréquents, avec leur signification")
    @GetMapping("/logs/top-errors")
    public TopErrors topErrors(@RequestParam(defaultValue = "60") int minutes, @RequestParam(defaultValue = "10") int limit) {
        return service.topErrors(minutes, limit);
    }

    @Operation(summary = "Catalogue des significations de messages de log")
    @GetMapping("/logs/meanings")
    public List<MeaningEntry> meanings() {
        return service.meanings();
    }

    @Operation(summary = "Erreurs de logs et CPU rapprochés des incidents HELIOS de la période")
    @GetMapping("/correlation")
    public Correlation correlation(@RequestParam(defaultValue = "360") int minutes) {
        return service.correlation(minutes);
    }

    // ── Camunda (lecture seule) ───────────────────────────────────────────────

    @Operation(summary = "Camunda : état du moteur, instances par état, définitions de processus")
    @GetMapping("/camunda/overview")
    public CamundaOverview camundaOverview() {
        return camunda.overview();
    }

    @Operation(summary = "Camunda : instances de processus récentes (état facultatif : ACTIVE, COMPLETED, TERMINATED)")
    @GetMapping("/camunda/instances")
    public CamundaInstances camundaInstances(@RequestParam(required = false) String state, @RequestParam(defaultValue = "20") int limit) {
        return camunda.instances(state, limit);
    }

    @Operation(summary = "Camunda : incidents actifs")
    @GetMapping("/camunda/incidents")
    public CamundaIncidents camundaIncidents(@RequestParam(defaultValue = "20") int limit) {
        return camunda.incidents(limit);
    }

    @Operation(summary = "Camunda : étapes d'un processus, lues dans son BPMN")
    @GetMapping("/camunda/definitions/{key}/steps")
    public CamundaSteps camundaSteps(@PathVariable String key) {
        return camunda.steps(key);
    }

    // ── Grafana ───────────────────────────────────────────────────────────────

    @Operation(summary = "Grafana : dashboards proposés et intégration activée ou non")
    @GetMapping("/grafana")
    public GrafanaConfig grafana() {
        return grafana.config();
    }
}
