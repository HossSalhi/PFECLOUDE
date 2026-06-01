package com.orange.pfe.backend.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.List;

@ConfigurationProperties(prefix = "app.security")
public class AppSecurityProperties {

    private boolean exposeApiDocs = false;
    private String corsAllowedOrigins = "http://localhost:4200";
    private int maxFailedAttempts = 5;
    private int lockDurationMinutes = 30;

    // Kept for legacy compatibility — no longer used for authentication
    private String apiUsername = "operator";
    private String apiPassword = "operator-change-me";
    private List<UserEntry> users = List.of();

    public record UserEntry(String username, String password, String role) {}

    public boolean isExposeApiDocs() { return exposeApiDocs; }
    public void setExposeApiDocs(boolean exposeApiDocs) { this.exposeApiDocs = exposeApiDocs; }

    public String getCorsAllowedOrigins() { return corsAllowedOrigins; }
    public void setCorsAllowedOrigins(String corsAllowedOrigins) { this.corsAllowedOrigins = corsAllowedOrigins; }

    public int getMaxFailedAttempts() { return maxFailedAttempts; }
    public void setMaxFailedAttempts(int maxFailedAttempts) { this.maxFailedAttempts = maxFailedAttempts; }

    public int getLockDurationMinutes() { return lockDurationMinutes; }
    public void setLockDurationMinutes(int lockDurationMinutes) { this.lockDurationMinutes = lockDurationMinutes; }

    public String getApiUsername() { return apiUsername; }
    public void setApiUsername(String apiUsername) { this.apiUsername = apiUsername; }

    public String getApiPassword() { return apiPassword; }
    public void setApiPassword(String apiPassword) { this.apiPassword = apiPassword; }

    public List<UserEntry> getUsers() { return users; }
    public void setUsers(List<UserEntry> users) { this.users = users; }
}
