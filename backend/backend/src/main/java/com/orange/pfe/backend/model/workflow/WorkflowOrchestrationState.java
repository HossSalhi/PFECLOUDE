package com.orange.pfe.backend.model.workflow;

public enum WorkflowOrchestrationState {
    CREATED,
    WAITING_FOR_APPROVAL,
    APPROVED,
    EXECUTION_PENDING,
    EXECUTING,
    VERIFYING,
    RESOLVED,
    MANUAL_FOLLOW_UP,
    FAILED,
    REJECTED,
    ESCALATED,
    CANCELLED
}
