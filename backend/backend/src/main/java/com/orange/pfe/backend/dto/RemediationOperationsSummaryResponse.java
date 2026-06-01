package com.orange.pfe.backend.dto;

import java.time.LocalDateTime;
import java.util.Map;

public record RemediationOperationsSummaryResponse(
        LocalDateTime generatedAt,
        int lookbackHours,
        long recentActions,
        long autoActions,
        long completedActions,
        long failedActions,
        long inProgressActions,
        long pendingActions,
        long approvalPendingWorkflows,
        long runningWorkflows,
        long failedWorkflows,
        long openIncidents,
        long waitingApprovalIncidents,
        double successRate,
        double autoActionRate,
        double averageAttemptsPerAction,
        double averageExecutionSeconds,
        Map<String, Long> actionTypeBreakdown,
        Map<String, Long> failureTypeBreakdown
) {
}
