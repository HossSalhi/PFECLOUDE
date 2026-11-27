package com.orange.pfe.backend.service.audit;

import com.orange.pfe.backend.dto.audit.SelfHealingDtos.CamundaRun;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.CamundaTrace;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.WorkflowLogLine;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.SelfHealingReadRepository;
import com.orange.pfe.backend.service.org.OrgAccessService;
import com.orange.pfe.backend.service.org.OrgPermission;
import com.orange.pfe.backend.service.supervision.CamundaSupervisionService;
import com.orange.pfe.backend.service.supervision.CamundaSupervisionService.InstanceDetail;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;

/**
 * Trace Camunda de l'incident (Audit-trail, {@code AUDIT_VIEW_ALL}) : DÉRIVÉE et en lecture seule. Pour chaque workflow de
 * l'incident, elle relit l'historique de l'instance Camunda associée (étapes réellement exécutées, incidents) et le journal
 * HELIOS du workflow. Les variables du processus ne sont jamais lues. Camunda injoignable : chaque exécution porte une note,
 * jamais d'erreur serveur ; aucune écriture.
 */
@Service
public class SelfHealingCamundaTraceService {

    static final int LOG_LINES_MAX = 100;

    private final IncidentRepository incidents;
    private final SelfHealingReadRepository read;
    private final CamundaSupervisionService camunda;
    private final OrgAccessService access;

    public SelfHealingCamundaTraceService(IncidentRepository incidents, SelfHealingReadRepository read,
                                          CamundaSupervisionService camunda, OrgAccessService access) {
        this.incidents = incidents;
        this.read = read;
        this.camunda = camunda;
        this.access = access;
    }

    @Transactional(readOnly = true)
    public CamundaTrace trace(AppUser actor, Long incidentId) {
        access.require(actor, OrgPermission.AUDIT_VIEW_ALL);
        incidents.findById(incidentId).orElseThrow(() -> new ResourceNotFoundException("Incident introuvable : " + incidentId));
        List<CamundaRun> runs = new ArrayList<>();
        int keyed = 0, failed = 0;
        String lastError = null;
        for (WorkflowInstance w : read.workflowsOfIncident(incidentId)) {
            List<WorkflowLogLine> logs = read.workflowLogs(w.getId(), LOG_LINES_MAX).stream()
                    .map(l -> new WorkflowLogLine(l.getCreatedAt(), l.getMessage())).toList();
            String key = w.getCamundaProcessInstanceKey();
            if (key == null || key.isBlank()) {
                runs.add(new CamundaRun(w.getId(), w.getWorkflowName(), name(w.getStatus()), null, w.getCamundaProcessDefinitionId(),
                        null, w.getStartedAt(), w.getEndedAt(), false, "Aucune instance Camunda associée à ce workflow", List.of(), List.of(), logs));
                continue;
            }
            keyed++;
            InstanceDetail d;
            try {
                d = camunda.instanceDetail(key.trim());
            } catch (IllegalArgumentException e) {
                d = new InstanceDetail(false, "Clé d'instance Camunda invalide", null, null, null, null, false, List.of(), List.of());
            }
            if (!d.available()) {
                failed++;
                lastError = d.error();
            }
            runs.add(new CamundaRun(w.getId(), w.getWorkflowName(), name(w.getStatus()), key.trim(),
                    d.definitionId() != null ? d.definitionId() : w.getCamundaProcessDefinitionId(), d.state(),
                    d.start() != null ? d.start() : w.getStartedAt(), d.end() != null ? d.end() : w.getEndedAt(), d.hasIncident(),
                    d.available() ? null : d.error(), d.steps(), d.incidents(), logs));
        }
        boolean allFailed = keyed > 0 && failed == keyed;
        return new CamundaTrace(!allFailed, allFailed ? lastError : null, runs);
    }

    private static String name(Enum<?> e) {
        return e == null ? null : e.name();
    }
}
