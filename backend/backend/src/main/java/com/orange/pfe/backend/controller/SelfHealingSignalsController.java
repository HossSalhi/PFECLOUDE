package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Alerts;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.CamundaTrace;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.LogSignals;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.audit.SelfHealingAlertsService;
import com.orange.pfe.backend.service.audit.SelfHealingCamundaTraceService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Alertes, signaux de logs et trace Camunda d'un incident, pour l'onglet Auto-remédiation de l'Audit-trail. Lecture seule,
 * dérivée, réservée à {@code AUDIT_VIEW_ALL} (contrôle fait par les services) ; aucune écriture dans le journal d'audit.
 * Trois appels distincts, chargés à la demande : ils interrogent des outils différents (Prometheus, Loki, Camunda).
 */
@RestController
@RequestMapping("/api/audit-trail/self-healing/{incidentId}")
@Tag(name = "Audit : auto-remédiation", description = "Alertes, signaux de logs et trace Camunda d'un incident (lecture seule)")
public class SelfHealingSignalsController {

    private final SelfHealingAlertsService alerts;
    private final SelfHealingCamundaTraceService camunda;

    public SelfHealingSignalsController(SelfHealingAlertsService alerts, SelfHealingCamundaTraceService camunda) {
        this.alerts = alerts;
        this.camunda = camunda;
    }

    @Operation(summary = "Alertes Prometheus, Alertmanager, Grafana et règles Loki liées à l'incident")
    @GetMapping("/alerts")
    public Alerts alerts(@AuthenticationPrincipal AppUser actor, @PathVariable Long incidentId) {
        return alerts.alerts(actor, incidentId);
    }

    @Operation(summary = "Signaux d'erreur des logs de la VM ou du pod, classés par signification")
    @GetMapping("/log-signals")
    public LogSignals logSignals(@AuthenticationPrincipal AppUser actor, @PathVariable Long incidentId) {
        return alerts.logSignals(actor, incidentId);
    }

    @Operation(summary = "Historique des instances Camunda de l'incident (étapes, incidents), sans les variables")
    @GetMapping("/camunda")
    public CamundaTrace camunda(@AuthenticationPrincipal AppUser actor, @PathVariable Long incidentId) {
        return camunda.trace(actor, incidentId);
    }
}
