package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.enums.AlertSource;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

public class AlertWebhookRequest {

    @NotBlank(message = "Message obligatoire")
    private String message;

    @NotNull(message = "Source obligatoire")
    private AlertSource source;

    private String severity;

    // getters & setters
    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }

    public AlertSource getSource() { return source; }
    public void setSource(AlertSource source) { this.source = source; }

    public String getSeverity() { return severity; }
    public void setSeverity(String severity) { this.severity = severity; }
}
