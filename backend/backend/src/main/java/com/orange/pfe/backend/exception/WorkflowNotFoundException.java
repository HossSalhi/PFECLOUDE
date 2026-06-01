package com.orange.pfe.backend.exception;

public class WorkflowNotFoundException extends RuntimeException {

    public WorkflowNotFoundException(Long id) {
        super("Workflow not found: " + id);
    }
}
