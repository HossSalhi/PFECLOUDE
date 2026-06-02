package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.AILearnRequest;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.learning.IncidentLearning;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.repository.IncidentLearningRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Auto-learning service: periodically scans resolved/failed AI incidents and records
 * their outcomes. These outcomes are fed back to the LLM as past experience context,
 * enabling in-context learning without fine-tuning.
 */
@Service
public class IncidentLearningService {

    private static final Logger log = LoggerFactory.getLogger(IncidentLearningService.class);

    private final IncidentRepository incidentRepository;
    private final IncidentLearningRepository learningRepository;
    private final IncidentActionRepository actionRepository;
    private final AIOpsClientService aiopsClientService;

    private final LeaderElectionService leaderElectionService;

    public IncidentLearningService(
            IncidentRepository incidentRepository,
            IncidentLearningRepository learningRepository,
            IncidentActionRepository actionRepository,
            AIOpsClientService aiopsClientService,
            LeaderElectionService leaderElectionService
    ) {
        this.incidentRepository = incidentRepository;
        this.learningRepository = learningRepository;
        this.actionRepository = actionRepository;
        this.aiopsClientService = aiopsClientService;
        this.leaderElectionService = leaderElectionService;
    }

    /**
     * Every 5 minutes: scan recently closed AI incidents and record their outcomes.
     * This is the auto-learning loop — runs fully automatically with no human trigger.
     */
    @Scheduled(fixedDelay = 300_000)
    @Transactional
    public void autoRecordOutcomes() {
        // HA: only the leader replica records outcomes (avoids double-learning).
        if (!leaderElectionService.isLeader()) return;
        LocalDateTime since = LocalDateTime.now().minusHours(6);

        List<Incident> candidates = incidentRepository.findBySource(IncidentSource.AI).stream()
                .filter(i -> i.getCreatedAt() != null && i.getCreatedAt().isAfter(since))
                .filter(i -> !learningRepository.existsByIncidentId(i.getId()))
                .filter(i -> i.getStatus() == IncidentStatus.RESOLVED
                        || i.getStatus() == IncidentStatus.CLOSED
                        || isStale(i))
                .toList();

        if (candidates.isEmpty()) {
            return;
        }

        int recorded = 0;
        for (Incident incident : candidates) {
            try {
                recordOutcome(incident);
                recorded++;
            } catch (Exception ex) {
                log.warn("Could not record learning for incident {}: {}", incident.getId(), ex.getMessage());
            }
        }

        if (recorded > 0) {
            log.info("Auto-learning: recorded {} new outcome(s)", recorded);
        }
    }

    private void recordOutcome(Incident incident) {
        boolean success = incident.getStatus() == IncidentStatus.RESOLVED
                || incident.getStatus() == IncidentStatus.CLOSED;

        String actionTaken = resolveActionTaken(incident);
        int resolutionMinutes = resolveResolutionMinutes(incident);
        String incidentType = extractIncidentType(incident);
        String severity = incident.getSeverity() != null ? incident.getSeverity().name() : "UNKNOWN";

        IncidentLearning learning = new IncidentLearning();
        learning.setIncidentId(incident.getId());
        learning.setNamespace(incident.getNamespace());
        learning.setApp(incident.getApp());
        learning.setIncidentType(incidentType);
        learning.setActionTaken(actionTaken);
        learning.setSeverity(severity);
        learning.setSuccess(success);
        learning.setResolutionTimeMinutes(resolutionMinutes);

        learningRepository.save(learning);
        log.debug("Learning recorded: incident={} type={} action={} success={} minutes={}",
                incident.getId(), incidentType, actionTaken, success, resolutionMinutes);

        // Push outcome to AI service ChromaDB for semantic similarity learning
        pushToAiMemory(incident, incidentType, severity, actionTaken, success, resolutionMinutes);
    }

    private void pushToAiMemory(Incident incident, String incidentType, String severity,
                                String action, boolean success, int minutes) {
        try {
            AILearnRequest req = new AILearnRequest();
            req.setIncidentType(incidentType);
            req.setSeverity(severity);
            req.setAction(action);
            req.setExecutable(mapToExecutable(action));
            req.setSuccess(success);
            req.setMinutesToResolve(minutes);
            req.setApp(incident.getApp() != null ? incident.getApp() : "backend");
            req.setNamespace(incident.getNamespace() != null ? incident.getNamespace() : "pfe");
            req.setConfidence(0.6);
            // Build minimal feature map from incident description fields
            Map<String, Double> features = new HashMap<>();
            features.put("overall_risk_score", success ? 0.6 : 0.4);
            req.setFeatures(features);
            aiopsClientService.learn(req);
        } catch (Exception ex) {
            log.debug("Could not push outcome to AI memory for incident {}: {}", incident.getId(), ex.getMessage());
        }
    }

    private String mapToExecutable(String action) {
        if (action == null) return "INVESTIGATE";
        return switch (action.toUpperCase()) {
            case "RESTART_POD" -> "RESTART_POD";
            case "SCALE_DEPLOYMENT" -> "SCALE_DEPLOYMENT";
            case "ESCALATE" -> "ESCALATE";
            case "NO_ACTION" -> "NO_ACTION";
            default -> "INVESTIGATE";
        };
    }

    /**
     * Returns the past learning experiences for a specific app as a list of context maps.
     * The result is sent to the AI service and included in the LLM prompt.
     */
    public List<Map<String, Object>> getLearningContext(String namespace, String app) {
        List<IncidentLearning> appHistory = learningRepository
                .findTop20ByNamespaceAndAppOrderByCreatedAtDesc(namespace, app);

        List<IncidentLearning> globalHistory = learningRepository.findLatest();

        // Merge: app-specific first, then global (deduplicated by id)
        List<IncidentLearning> merged = new ArrayList<>(appHistory);
        for (IncidentLearning g : globalHistory) {
            if (merged.stream().noneMatch(m -> m.getId().equals(g.getId()))) {
                merged.add(g);
            }
        }

        List<Map<String, Object>> result = new ArrayList<>();
        for (IncidentLearning l : merged.stream().limit(15).toList()) {
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("incident_type", l.getIncidentType());
            entry.put("action", l.getActionTaken());
            entry.put("severity", l.getSeverity());
            entry.put("success", l.getSuccess());
            entry.put("minutes", l.getResolutionTimeMinutes());
            entry.put("app", l.getApp());
            result.add(entry);
        }
        return result;
    }

    private String extractIncidentType(Incident incident) {
        if (incident.getIncidentType() != null && !incident.getIncidentType().isBlank()) {
            return incident.getIncidentType();
        }
        String title = incident.getTitle();
        if (title != null && title.startsWith("AI Incident: ")) {
            return title.replace("AI Incident: ", "").trim();
        }
        return "UNKNOWN";
    }

    private String resolveActionTaken(Incident incident) {
        try {
            LocalDateTime cutoff = (incident.getCreatedAt() != null ? incident.getCreatedAt() : LocalDateTime.now().minusHours(1))
                    .minusMinutes(5);
            List<IncidentAction> actions = actionRepository.findRecentActionsForTarget(
                    incident.getNamespace(), incident.getApp(), cutoff);
            if (!actions.isEmpty()) {
                IncidentAction last = actions.get(0);
                return last.getType() != null ? last.getType().name() : "NO_ACTION";
            }
        } catch (Exception ignored) {}
        return "NO_ACTION";
    }

    private int resolveResolutionMinutes(Incident incident) {
        if (incident.getCreatedAt() == null) return -1;
        long minutes = ChronoUnit.MINUTES.between(incident.getCreatedAt(), LocalDateTime.now());
        return (int) Math.min(minutes, 9999);
    }

    private boolean isStale(Incident incident) {
        if (incident.getCreatedAt() == null) return false;
        long ageMinutes = ChronoUnit.MINUTES.between(incident.getCreatedAt(), LocalDateTime.now());
        return ageMinutes > 120
                && (incident.getStatus() == IncidentStatus.OPEN
                || incident.getStatus() == IncidentStatus.NEW);
    }
}
