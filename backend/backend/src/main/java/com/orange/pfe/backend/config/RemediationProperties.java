package com.orange.pfe.backend.config;

import com.orange.pfe.backend.model.action.ActionType;
import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.LinkedHashSet;
import java.util.Set;

@ConfigurationProperties(prefix = "remediation")
public class RemediationProperties {

    private String labHostIp = "192.168.148.161";
    private boolean automationEnabled = true;
    private Set<ActionType> allowedActions = new LinkedHashSet<>(Set.of(ActionType.RESTART_POD));
    private double approvalConfidenceThreshold = 0.30d;
    private double restartConfidenceThreshold = 0.45d;
    private double scaleConfidenceThreshold = 0.44d;
    private double verificationIncidentConfidenceThreshold = 0.55d;
    private int cooldownMinutes = 30;
    private int verifyDelaySeconds = 120;
    private int verificationWindowMinutes = 1;
    private int verificationRetryAttempts = 10;
    private int verificationRetryDelaySeconds = 30;
    private int maxAutoScaleReplicas = 2;
    private int maxScaleStep = 1;
    private String kubeconfigPath;
    /** "*" allows any app; override with explicit names to restrict auto-remediation scope. */
    private Set<String> allowlistedApps = new LinkedHashSet<>(Set.of("*"));
    /** "*" allows all non-system namespaces; override with explicit names to restrict. */
    private Set<String> allowlistedNamespaces = new LinkedHashSet<>(Set.of("*"));
    private String watchdogBaseUrl = "http://localhost:9099";

    /** When true, automatically execute compensating actions if remediation worsens the situation. */
    private boolean autoRollbackEnabled = true;
    /** AI confidence ratio threshold above which post-remediation state is considered degraded (1.15 = 15% worse). */
    private double degradationThresholdRatio = 1.15d;
    /** Minimum confidence required to auto-execute a rollback. */
    private double rollbackConfidenceThreshold = 0.60d;

    public String getLabHostIp() {
        return labHostIp;
    }

    public void setLabHostIp(String labHostIp) {
        this.labHostIp = labHostIp;
    }

    public boolean isAutomationEnabled() {
        return automationEnabled;
    }

    public void setAutomationEnabled(boolean automationEnabled) {
        this.automationEnabled = automationEnabled;
    }

    public Set<ActionType> getAllowedActions() {
        return allowedActions;
    }

    public void setAllowedActions(Set<ActionType> allowedActions) {
        this.allowedActions = allowedActions;
    }

    public double getApprovalConfidenceThreshold() {
        return approvalConfidenceThreshold;
    }

    public void setApprovalConfidenceThreshold(double approvalConfidenceThreshold) {
        this.approvalConfidenceThreshold = approvalConfidenceThreshold;
    }

    public double getRestartConfidenceThreshold() {
        return restartConfidenceThreshold;
    }

    public void setRestartConfidenceThreshold(double restartConfidenceThreshold) {
        this.restartConfidenceThreshold = restartConfidenceThreshold;
    }

    public double getScaleConfidenceThreshold() {
        return scaleConfidenceThreshold;
    }

    public void setScaleConfidenceThreshold(double scaleConfidenceThreshold) {
        this.scaleConfidenceThreshold = scaleConfidenceThreshold;
    }

    public int getCooldownMinutes() {
        return cooldownMinutes;
    }

    public void setCooldownMinutes(int cooldownMinutes) {
        this.cooldownMinutes = cooldownMinutes;
    }

    public double getVerificationIncidentConfidenceThreshold() {
        return verificationIncidentConfidenceThreshold;
    }

    public void setVerificationIncidentConfidenceThreshold(double verificationIncidentConfidenceThreshold) {
        this.verificationIncidentConfidenceThreshold = verificationIncidentConfidenceThreshold;
    }

    public int getVerifyDelaySeconds() {
        return verifyDelaySeconds;
    }

    public void setVerifyDelaySeconds(int verifyDelaySeconds) {
        this.verifyDelaySeconds = verifyDelaySeconds;
    }

    public int getVerificationWindowMinutes() {
        return verificationWindowMinutes;
    }

    public void setVerificationWindowMinutes(int verificationWindowMinutes) {
        this.verificationWindowMinutes = verificationWindowMinutes;
    }

    public int getVerificationRetryAttempts() {
        return verificationRetryAttempts;
    }

    public void setVerificationRetryAttempts(int verificationRetryAttempts) {
        this.verificationRetryAttempts = verificationRetryAttempts;
    }

    public int getVerificationRetryDelaySeconds() {
        return verificationRetryDelaySeconds;
    }

    public void setVerificationRetryDelaySeconds(int verificationRetryDelaySeconds) {
        this.verificationRetryDelaySeconds = verificationRetryDelaySeconds;
    }

    public int getMaxAutoScaleReplicas() {
        return maxAutoScaleReplicas;
    }

    public void setMaxAutoScaleReplicas(int maxAutoScaleReplicas) {
        this.maxAutoScaleReplicas = maxAutoScaleReplicas;
    }

    public int getMaxScaleStep() {
        return maxScaleStep;
    }

    public void setMaxScaleStep(int maxScaleStep) {
        this.maxScaleStep = maxScaleStep;
    }

    public String getKubeconfigPath() {
        return kubeconfigPath;
    }

    public void setKubeconfigPath(String kubeconfigPath) {
        this.kubeconfigPath = kubeconfigPath;
    }

    public Set<String> getAllowlistedApps() {
        return allowlistedApps;
    }

    public void setAllowlistedApps(Set<String> allowlistedApps) {
        this.allowlistedApps = allowlistedApps;
    }

    public Set<String> getAllowlistedNamespaces() {
        return allowlistedNamespaces;
    }

    public void setAllowlistedNamespaces(Set<String> allowlistedNamespaces) {
        this.allowlistedNamespaces = allowlistedNamespaces;
    }

    public String getWatchdogBaseUrl() {
        return watchdogBaseUrl;
    }

    public void setWatchdogBaseUrl(String watchdogBaseUrl) {
        this.watchdogBaseUrl = watchdogBaseUrl;
    }

    public boolean isAutoRollbackEnabled() {
        return autoRollbackEnabled;
    }

    public void setAutoRollbackEnabled(boolean autoRollbackEnabled) {
        this.autoRollbackEnabled = autoRollbackEnabled;
    }

    public double getDegradationThresholdRatio() {
        return degradationThresholdRatio;
    }

    public void setDegradationThresholdRatio(double degradationThresholdRatio) {
        this.degradationThresholdRatio = degradationThresholdRatio;
    }

    public double getRollbackConfidenceThreshold() {
        return rollbackConfidenceThreshold;
    }

    public void setRollbackConfidenceThreshold(double rollbackConfidenceThreshold) {
        this.rollbackConfidenceThreshold = rollbackConfidenceThreshold;
    }
}
