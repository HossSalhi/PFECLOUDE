package com.orange.pfe.backend.exception;

public class AIOpsIntegrationException extends RuntimeException {
    public AIOpsIntegrationException(String message) {
        super(message);
    }

    public AIOpsIntegrationException(String message, Throwable cause) {
        super(message, cause);
    }
}
