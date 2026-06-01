package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.dto.RemediationOperationsSummaryResponse;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.WorkflowRepository;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

@Service
public class RemediationOperationalSummaryService {

    private final IncidentActionRepository incidentActionRepository;
    private final IncidentRepository incidentRepository;
    private final WorkflowRepository workflowRepository;

    public RemediationOperationalSummaryService(
            IncidentActionRepository incidentActionRepository,
            IncidentRepository incidentRepository,
            WorkflowRepository workflowRepository
    ) {
        this.incidentActionRepository = incidentActionRepository;
        this.incidentRepository = incidentRepository;
        this.workflowRepository = workflowRepository;
    }

    public RemediationOperationsSummaryResponse buildSummary(int lookbackHours) {
        int normalizedLookbackHours = Math.max(1, lookbackHours);
        LocalDateTime cutoff = LocalDateTime.now().minusHours(normalizedLookbackHours);
        List<IncidentAction> recentActions = incidentActionRepository.findRecentActions(cutoff);

        long autoActions = recentActions.stream()
                .filter(action -> action.getAutomationMode() == AutomationMode.AUTO)
                .count();
        long completedActions = countByStatus(recentActions, ActionStatus.COMPLETED);
        long failedActions = countByStatus(recentActions, ActionStatus.FAILED);
        long inProgressActions = countByStatus(recentActions, ActionStatus.IN_PROGRESS);
        long pendingActions = countByStatus(recentActions, ActionStatus.PENDING);

        long terminalActions = completedActions + failedActions;
        double successRate = terminalActions == 0 ? 0.0d : (double) completedActions / terminalActions;
        double autoActionRate = recentActions.isEmpty() ? 0.0d : (double) autoActions / recentActions.size();
        double averageAttempts = recentActions.isEmpty()
                ? 0.0d
                : recentActions.stream().mapToInt(IncidentAction::getAttemptCount).average().orElse(0.0d);
        double averageExecutionSeconds = recentActions.stream()
                .filter(action -> action.getExecutedAt() != null && action.getCreatedAt() != null)
                .mapToLong(action -> Duration.between(action.getCreatedAt(), action.getExecutedAt()).toSeconds())
                .average()
                .orElse(0.0d);

        return new RemediationOperationsSummaryResponse(
                LocalDateTime.now(),
                normalizedLookbackHours,
                recentActions.size(),
                autoActions,
                completedActions,
                failedActions,
                inProgressActions,
                pendingActions,
                workflowRepository.countByOrchestrationState(WorkflowOrchestrationState.WAITING_FOR_APPROVAL),
                workflowRepository.countByStatus(WorkflowStatus.RUNNING),
                workflowRepository.countByStatus(WorkflowStatus.FAILED),
                incidentRepository.countByStatus(IncidentStatus.OPEN),
                incidentRepository.countByStatus(IncidentStatus.WAITING_FOR_APPROVAL),
                round(successRate),
                round(autoActionRate),
                round(averageAttempts),
                round(averageExecutionSeconds),
                summarizeBy(recentActions, action -> action.getType() == null ? "UNKNOWN" : action.getType().name()),
                summarizeBy(
                        recentActions.stream()
                                .filter(action -> action.getStatus() == ActionStatus.FAILED)
                                .toList(),
                        action -> action.getType() == null ? "UNKNOWN" : action.getType().name()
                )
        );
    }

    private long countByStatus(List<IncidentAction> actions, ActionStatus status) {
        return actions.stream()
                .filter(action -> action.getStatus() == status)
                .count();
    }

    private Map<String, Long> summarizeBy(List<IncidentAction> actions, Function<IncidentAction, String> classifier) {
        return actions.stream()
                .collect(Collectors.groupingBy(classifier, Collectors.counting()))
                .entrySet()
                .stream()
                .sorted(Map.Entry.<String, Long>comparingByValue(Comparator.reverseOrder()).thenComparing(Map.Entry.comparingByKey()))
                .collect(Collectors.toMap(
                        Map.Entry::getKey,
                        Map.Entry::getValue,
                        (left, right) -> left,
                        LinkedHashMap::new
                ));
    }

    private double round(double value) {
        return Math.round(value * 1000.0d) / 1000.0d;
    }
}
