package com.orange.pfe.backend.exception;

public class InvalidWorkflowStateException extends RuntimeException {

    public InvalidWorkflowStateException(String message) {
        super(message);
    }
}
