package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
public class RemediationHistoryContextService {

    private final IncidentActionRepository incidentActionRepository;
    private final RemediationProperties remediationProperties;
    private final IncidentLearningService incidentLearningService;

    public RemediationHistoryContextService(
            IncidentActionRepository incidentActionRepository,
            RemediationProperties remediationProperties,
            IncidentLearningService incidentLearningService
    ) {
        this.incidentActionRepository = incidentActionRepository;
        this.remediationProperties = remediationProperties;
        this.incidentLearningService = incidentLearningService;
    }

    public Map<String, Object> buildContext(String namespace, String app) {
        Map<String, Object> context = new LinkedHashMap<>();
        // Full-auto: zero human intervention for AI-detected incidents
        context.put("autonomy_mode", remediationProperties.isAutomationEnabled() ? "full-auto" : "manual");
        context.put("innovation_mode", "creative-advisory");
        context.put("strategy_nonce", System.currentTimeMillis() / 1000L);
        context.put("cooldown_minutes", remediationProperties.getCooldownMinutes());

        if (namespace == null || namespace.isBlank() || app == null || app.isBlank()) {
            context.put("recent_action_counts", Map.of());
            context.put("recent_failed_action_counts", Map.of());
            context.put("recent_success_action_counts", Map.of());
            context.put("recent_action_sequence", List.of());
            return context;
        }

        LocalDateTime cutoff = LocalDateTime.now().minusMinutes(Math.max(30, remediationProperties.getCooldownMinutes() * 6));
        List<IncidentAction> recentActions = incidentActionRepository.findRecentActionsForTarget(namespace, app, cutoff);

        Map<String, Integer> recentActionCounts = new LinkedHashMap<>();
        Map<String, Integer> recentFailedActionCounts = new LinkedHashMap<>();
        Map<String, Integer> recentSuccessActionCounts = new LinkedHashMap<>();
        List<String> recentActionSequence = new ArrayList<>();

        for (IncidentAction action : recentActions) {
            if (action.getType() == null) {
                continue;
            }

            String actionType = action.getType().name();
            recentActionCounts.merge(actionType, 1, Integer::sum);
            recentActionSequence.add(actionType);

            if (action.getStatus() == ActionStatus.FAILED) {
                recentFailedActionCounts.merge(actionType, 1, Integer::sum);
            } else if (action.getStatus() == ActionStatus.COMPLETED) {
                recentSuccessActionCounts.merge(actionType, 1, Integer::sum);
            }
        }

        context.put("recent_action_counts", recentActionCounts);
        context.put("recent_failed_action_counts", recentFailedActionCounts);
        context.put("recent_success_action_counts", recentSuccessActionCounts);
        context.put("recent_action_sequence", recentActionSequence);

        // Auto-learning: inject past incident outcomes so the LLM can learn from experience
        context.put("past_incidents", incidentLearningService.getLearningContext(namespace, app));

        return context;
    }
}
