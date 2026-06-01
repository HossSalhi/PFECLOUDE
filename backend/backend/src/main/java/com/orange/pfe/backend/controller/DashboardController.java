package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.human.TaskDecision;
import com.orange.pfe.backend.repository.HumanTaskRepository;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.WorkflowRepository;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDateTime;
import java.util.Map;

@RestController
@RequestMapping("/api/dashboard")
public class DashboardController {

    private final IncidentRepository incidentRepository;
    private final IncidentActionRepository actionRepository;
    private final WorkflowRepository workflowRepository;
    private final HumanTaskRepository humanTaskRepository;

    public DashboardController(IncidentRepository incidentRepository,
                               IncidentActionRepository actionRepository,
                               WorkflowRepository workflowRepository,
                               HumanTaskRepository humanTaskRepository) {
        this.incidentRepository = incidentRepository;
        this.actionRepository = actionRepository;
        this.workflowRepository = workflowRepository;
        this.humanTaskRepository = humanTaskRepository;
    }

    @GetMapping("/summary")
    public ResponseEntity<Map<String, Object>> summary() {
        long totalIncidents      = incidentRepository.count();
        long openIncidents       = incidentRepository.countByStatus(IncidentStatus.OPEN);
        long inProgressIncidents = incidentRepository.countByStatus(IncidentStatus.IN_PROGRESS);
        long resolvedIncidents   = incidentRepository.countByStatus(IncidentStatus.RESOLVED);
        long criticalIncidents   = incidentRepository.countBySeverity(IncidentSeverity.CRITICAL);
        long pendingTasks        = humanTaskRepository.countByDecision(TaskDecision.PENDING);
        long totalActions        = actionRepository.count();

        double resolutionRate = totalIncidents == 0 ? 0.0
                : Math.round((resolvedIncidents * 100.0 / totalIncidents) * 10) / 10.0;

        return ResponseEntity.ok(Map.of(
                "generatedAt",         LocalDateTime.now().toString(),
                "totalIncidents",      totalIncidents,
                "openIncidents",       openIncidents,
                "inProgressIncidents", inProgressIncidents,
                "resolvedIncidents",   resolvedIncidents,
                "criticalIncidents",   criticalIncidents,
                "pendingApprovals",    pendingTasks,
                "totalActions",        totalActions,
                "resolutionRate",      resolutionRate
        ));
    }
}
