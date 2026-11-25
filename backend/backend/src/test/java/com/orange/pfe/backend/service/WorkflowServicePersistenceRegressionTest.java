package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.workflow.WorkflowLog;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.WorkflowRepository;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

@SpringBootTest(properties = {
    "jwt.secret=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "helios.bridge.api-key=bridge-test-secret"
})
class WorkflowServicePersistenceRegressionTest {

    @Autowired
    private WorkflowService workflowService;

    @Autowired
    private WorkflowRepository workflowRepository;

    @Autowired
    private IncidentRepository incidentRepository;

    private Long incidentId;
    private Long workflowId;

    @Test
    void shouldPersistWorkflowStateLogsAndIncidentAcrossTransactions() {
        String suffix = UUID.randomUUID().toString();
        String app = "workflow-regression-" + suffix;

        Incident incident = new Incident();
        incident.setTitle("Synthetic workflow persistence regression");
        incident.setDescription(
            "Test-only incident. No real infrastructure action is requested."
        );
        incident.setSeverity(IncidentSeverity.MEDIUM);
        incident.setStatus(IncidentStatus.OPEN);
        incident.setSource(IncidentSource.PROMETHEUS);
        incident.setIncidentType("TEST_WORKFLOW_PERSISTENCE");
        incident.setNamespace("workflow-test-ns");
        incident.setApp(app);

        Incident persistedIncident = incidentRepository.saveAndFlush(incident);
        incidentId = persistedIncident.getId();

        // Transaction 1: création du workflow et de ses premiers logs.
        WorkflowInstance created = workflowService.startWorkflow(
            persistedIncident,
            "WorkflowPersistenceRegression",
            "workflow-test-ns",
            app
        );

        workflowId = created.getId();

        assertNotNull(workflowId, "Le workflow doit recevoir un identifiant");
        assertEquals(
            String.valueOf(workflowId),
            created.getCorrelationKey(),
            "La correlation key doit être persistée"
        );

        // Transactions suivantes : rechargement et modification du workflow géré.
        workflowService.attachCamundaProcess(
            created,
            "test-process-definition",
            "test-process-" + suffix
        );

        workflowService.updateOrchestrationState(
            created,
            WorkflowOrchestrationState.EXECUTING,
            IncidentStatus.IN_PROGRESS,
            "Regression state transition persisted"
        );

        workflowService.finalizeWorkflow(
            created,
            WorkflowOrchestrationState.RESOLVED,
            WorkflowStatus.COMPLETED,
            IncidentStatus.RESOLVED,
            "Regression workflow resolved"
        );

        // Nouvelle lecture depuis la base après la fin des transactions de service.
        WorkflowInstance stored = workflowRepository
            .findByIdWithLogs(workflowId)
            .orElseThrow(() ->
                new AssertionError("Workflow absent après les mises à jour")
            );

        assertEquals(WorkflowStatus.COMPLETED, stored.getStatus());
        assertEquals(
            WorkflowOrchestrationState.RESOLVED,
            stored.getOrchestrationState()
        );
        assertEquals(
            "test-process-definition",
            stored.getCamundaProcessDefinitionId()
        );
        assertEquals(
            "test-process-" + suffix,
            stored.getCamundaProcessInstanceKey()
        );

        assertTrue(
            stored.getLogs().stream()
                .map(WorkflowLog::getMessage)
                .anyMatch(message ->
                    message != null &&
                    message.contains("Correlation key assigned")
                ),
            "Le log de correlation doit être conservé"
        );

        assertTrue(
            stored.getLogs().stream()
                .map(WorkflowLog::getMessage)
                .anyMatch(message ->
                    message != null &&
                    message.contains("Camunda process attached")
                ),
            "Le log d'association Camunda doit être conservé"
        );

        assertTrue(
            stored.getLogs().stream()
                .map(WorkflowLog::getMessage)
                .anyMatch(message ->
                    message != null &&
                    message.contains("Regression state transition persisted")
                ),
            "Le log de transition doit être conservé"
        );

        assertTrue(
            stored.getLogs().stream()
                .map(WorkflowLog::getMessage)
                .anyMatch(message ->
                    message != null &&
                    message.contains("Regression workflow resolved")
                ),
            "Le log final doit être conservé"
        );

        Incident storedIncident = incidentRepository
            .findById(incidentId)
            .orElseThrow(() ->
                new AssertionError("Incident absent après finalisation")
            );

        assertEquals(
            IncidentStatus.RESOLVED,
            storedIncident.getStatus(),
            "Le statut de l'incident doit être persisté"
        );
    }

    @AfterEach
    void cleanup() {
        try {
            if (workflowId != null &&
                workflowRepository.existsById(workflowId)) {
                workflowRepository.deleteById(workflowId);
                workflowRepository.flush();
            }
        } finally {
            if (incidentId != null &&
                incidentRepository.existsById(incidentId)) {
                incidentRepository.deleteById(incidentId);
                incidentRepository.flush();
            }
        }
    }
}