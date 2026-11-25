package com.orange.pfe.backend.config;

import com.orange.pfe.backend.model.action.ActionType;
import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.LinkedHashSet;
import java.util.Set;

@ConfigurationProperties(prefix = "remediation")
public class RemediationProperties {
    private boolean bridgeEnabled = false;
    private String bridgeUrl = "";
    private String bridgeToken = "";
    private boolean hostDiskRemediationEnabled = false;
    // DISK-E2E-158-02 : activation DEDIEE du scenario DISK_E2E_158 (k8s-worker1 + NODE_DISK_FULL + HOST_RECLAIM_DISK, executee
    // par le Bridge). Defaut FALSE (fail-closed). Sans aucun lien avec hostDiskRemediationEnabled (ancien drapeau global) :
    // ni l'un n'active l'autre, ni l'inverse. Voir HostDiskScenario.
    private boolean hostDiskE2e158Enabled = false;
    // CPU-02 : proprietaire unique = Camunda v25. Defaut TRUE pour PRESERVER le
    // comportement historique (remediation CPU backend active, prouvee #3713) ;
    // l operateur passe HOST_CPU_REMEDIATION_ENABLED=false au basculement vers v25.
    private boolean hostCpuRemediationEnabled = true;

    // RAM — remediation memoire de LABORATOIRE (modele CPU). Defaut FALSE (fail-closed) : tant que le
    // service de charge memoire de labo (helios-memload) et l'action memoire du Bridge ne sont pas
    // provisionnes+deployes, l'incident RAM ESCALADE (jamais de succes fabrique). L'operateur pose
    // HOST_MEMORY_REMEDIATION_ENABLED=true une fois la cible labo + la capacite Bridge en place.
    private boolean hostMemoryRemediationEnabled = false;

    // Arrêt automatique du générateur de charge CPU de LABORATOIRE (helios-cpuload.service) après une
    // résolution CPU confirmée (vérif OK + RESOLVED). Borné aux workers de labo via l'action dédiée du
    // Bridge (STOP_CPU_LOAD_SERVICE, cible résolue côté serveur). Défaut TRUE = comportement demandé ;
    // l'opérateur peut poser REMEDIATION_AUTO_STOP_CPU_LOAD_ENABLED=false pour le désactiver.
    private boolean autoStopCpuLoadEnabled = true;


    private String labHostIp = "192.168.148.161";
    private boolean automationEnabled = true;
    private Set<ActionType> allowedActions = new LinkedHashSet<>(Set.of(ActionType.RESTART_POD));
    private double approvalConfidenceThreshold = 0.30d;
    private double restartConfidenceThreshold = 0.45d;
    private double scaleConfidenceThreshold = 0.44d;
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
    // MASTER-VM-DOWN-20261029 — watchdog MAÎTRE (:9099, helios-node-autoheal) pour le control-plane,
    // DISTINCT de l'agent worker :9999 (watchdogBaseUrl). Vides par défaut => rallumage master REFUSÉ
    // (fail-closed, aucun repli). Injectés par REMEDIATION_MASTER_WATCHDOG_URL (configmap) et
    // REMEDIATION_MASTER_WATCHDOG_TOKEN (secret) ; le jeton n'est JAMAIS en dur dans le code.
    private String masterWatchdogUrl = "";
    private String masterWatchdogToken = "";

    /** Minimum confidence required to auto-execute a rollback. */
    private double rollbackConfidenceThreshold = 0.60d;

    public boolean isHostDiskRemediationEnabled() {
        return hostDiskRemediationEnabled;
    }

    public void setHostDiskRemediationEnabled(boolean hostDiskRemediationEnabled) {
        this.hostDiskRemediationEnabled = hostDiskRemediationEnabled;
    }

    public boolean isHostDiskE2e158Enabled() {
        return hostDiskE2e158Enabled;
    }

    public void setHostDiskE2e158Enabled(boolean hostDiskE2e158Enabled) {
        this.hostDiskE2e158Enabled = hostDiskE2e158Enabled;
    }

    public boolean isHostCpuRemediationEnabled() {
        return hostCpuRemediationEnabled;
    }

    public void setHostCpuRemediationEnabled(boolean hostCpuRemediationEnabled) {
        this.hostCpuRemediationEnabled = hostCpuRemediationEnabled;
    }

    public boolean isHostMemoryRemediationEnabled() {
        return hostMemoryRemediationEnabled;
    }

    public void setHostMemoryRemediationEnabled(boolean hostMemoryRemediationEnabled) {
        this.hostMemoryRemediationEnabled = hostMemoryRemediationEnabled;
    }

    public boolean isAutoStopCpuLoadEnabled() {
        return autoStopCpuLoadEnabled;
    }

    public void setAutoStopCpuLoadEnabled(boolean autoStopCpuLoadEnabled) {
        this.autoStopCpuLoadEnabled = autoStopCpuLoadEnabled;
    }

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

    public String getMasterWatchdogUrl() {
        return masterWatchdogUrl;
    }

    public void setMasterWatchdogUrl(String masterWatchdogUrl) {
        this.masterWatchdogUrl = masterWatchdogUrl;
    }

    public String getMasterWatchdogToken() {
        return masterWatchdogToken;
    }

    public void setMasterWatchdogToken(String masterWatchdogToken) {
        this.masterWatchdogToken = masterWatchdogToken;
    }

    public double getRollbackConfidenceThreshold() {
        return rollbackConfidenceThreshold;
    }

    public void setRollbackConfidenceThreshold(double rollbackConfidenceThreshold) {
        this.rollbackConfidenceThreshold = rollbackConfidenceThreshold;
    }

    public boolean isBridgeEnabled() {
        return bridgeEnabled;
    }

    public void setBridgeEnabled(boolean bridgeEnabled) {
        this.bridgeEnabled = bridgeEnabled;
    }

    public String getBridgeUrl() {
        return bridgeUrl;
    }

    public void setBridgeUrl(String bridgeUrl) {
        this.bridgeUrl = bridgeUrl;
    }

    public String getBridgeToken() {
        return bridgeToken;
    }

    public void setBridgeToken(String bridgeToken) {
        this.bridgeToken = bridgeToken;
    }
}
