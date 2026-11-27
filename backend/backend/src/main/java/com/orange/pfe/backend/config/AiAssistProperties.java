package com.orange.pfe.backend.config;

import com.orange.pfe.backend.service.aiassist.AiFeature;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Assistance IA des modules de gestion (préfixe {@code helios.ai.assist}). DÉSACTIVÉE par défaut : le drapeau global ET
 * celui de la fonction doivent être vrais. La clé API vient d'une variable d'environnement (secret Kubernetes) ; elle
 * n'est jamais écrite dans un fichier versionné, jamais journalisée, jamais renvoyée par l'API.
 *
 * <p>Fournisseurs compatibles « chat/completions » : {@code mistral} (mistral-small-latest) ou {@code groq}
 * (llama-3.1-8b-instant). Adresse et modèle ont une valeur par défaut selon le fournisseur et restent surchargeables.</p>
 */
@ConfigurationProperties(prefix = "helios.ai.assist")
public class AiAssistProperties {

    /** Drapeau d'une fonction ; désactivé par défaut. */
    public static class Flag {
        private boolean enabled = false;

        public boolean isEnabled() { return enabled; }
        public void setEnabled(boolean enabled) { this.enabled = enabled; }
    }

    private boolean enabled = false;
    private String provider = "mistral";
    private String baseUrl = "";
    private String apiKey = "";
    private String model = "";
    private int timeoutSeconds = 20;
    private int maxTokens = 500;
    private double temperature = 0.2;

    private Flag reportsSummary = new Flag();
    private Flag auditAnomalies = new Flag();
    private Flag taskAssignment = new Flag();
    private Flag maintenanceWindow = new Flag();

    public boolean isFeatureEnabled(AiFeature feature) {
        if (!enabled || feature == null) {
            return false;
        }
        return switch (feature) {
            case REPORTS_SUMMARY -> reportsSummary.isEnabled();
            case AUDIT_ANOMALIES -> auditAnomalies.isEnabled();
            case TASK_ASSIGNMENT -> taskAssignment.isEnabled();
            case MAINTENANCE_WINDOW -> maintenanceWindow.isEnabled();
        };
    }

    public boolean isConfigured() {
        return apiKey != null && !apiKey.isBlank() && !effectiveBaseUrl().isBlank();
    }

    public String effectiveBaseUrl() {
        if (baseUrl != null && !baseUrl.isBlank()) {
            return baseUrl.trim().replaceAll("/+$", "");
        }
        return switch (provider == null ? "" : provider.trim().toLowerCase()) {
            case "mistral" -> "https://api.mistral.ai/v1";
            case "groq" -> "https://api.groq.com/openai/v1";
            default -> "";
        };
    }

    public String effectiveModel() {
        if (model != null && !model.isBlank()) {
            return model.trim();
        }
        return "groq".equalsIgnoreCase(provider == null ? "" : provider.trim()) ? "llama-3.1-8b-instant" : "mistral-small-latest";
    }

    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }
    public String getProvider() { return provider; }
    public void setProvider(String provider) { this.provider = provider; }
    public String getBaseUrl() { return baseUrl; }
    public void setBaseUrl(String baseUrl) { this.baseUrl = baseUrl; }
    public String getApiKey() { return apiKey; }
    public void setApiKey(String apiKey) { this.apiKey = apiKey; }
    public String getModel() { return model; }
    public void setModel(String model) { this.model = model; }
    public int getTimeoutSeconds() { return timeoutSeconds; }
    public void setTimeoutSeconds(int timeoutSeconds) { this.timeoutSeconds = Math.max(1, timeoutSeconds); }
    public int getMaxTokens() { return maxTokens; }
    public void setMaxTokens(int maxTokens) { this.maxTokens = Math.max(16, maxTokens); }
    public double getTemperature() { return temperature; }
    public void setTemperature(double temperature) { this.temperature = temperature; }
    public Flag getReportsSummary() { return reportsSummary; }
    public void setReportsSummary(Flag f) { this.reportsSummary = f; }
    public Flag getAuditAnomalies() { return auditAnomalies; }
    public void setAuditAnomalies(Flag f) { this.auditAnomalies = f; }
    public Flag getTaskAssignment() { return taskAssignment; }
    public void setTaskAssignment(Flag f) { this.taskAssignment = f; }
    public Flag getMaintenanceWindow() { return maintenanceWindow; }
    public void setMaintenanceWindow(Flag f) { this.maintenanceWindow = f; }
}
