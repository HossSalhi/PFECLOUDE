package com.orange.pfe.backend.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.Set;

/**
 * VMDOWN-POLICY-20261105 — réglages de la politique de mise sous tension des VM.
 *
 * <p>Les DEUX drapeaux sont fail-safe : par défaut {@code enabled=false} (aucune détection de puissance,
 * aucun démarrage) et {@code dryRun=true} (mode OMBRE : tout est évalué et journalisé, mais aucun appel
 * {@code POST /restart} n'est émis). Variables d'environnement : {@code HELIOS_VMPOWER_ENABLED},
 * {@code HELIOS_VMPOWER_DRYRUN}. L'URL et le jeton du watchdog restent ceux déjà déployés
 * ({@code remediation.master-watchdog-url} / {@code remediation.master-watchdog-token}).
 */
@ConfigurationProperties(prefix = "helios.vmpower")
public class VmPowerProperties {

    /** Active la surveillance de puissance (VmPowerWatch). */
    private boolean enabled = false;
    /** Mode ombre : évaluer et journaliser sans jamais demander de démarrage. */
    private boolean dryRun = true;
    /** Cadence de VmPowerWatch (secondes). */
    private int cycleSeconds = 15;
    /** Délai d'une sonde réseau (ms). */
    private int probeTimeoutMs = 2000;
    /** Parallélisme des sondes. */
    private int probeThreads = 8;
    /** Client watchdog : connexion (ms). */
    private int watchdogConnectTimeoutMs = 3000;
    /** Client watchdog : lecture des GET /vm/... (ms). */
    private int watchdogReadTimeoutMs = 20000;
    /** Client watchdog : lecture du POST /restart (ms) ; doit dépasser l'attente de réponse du watchdog (25 s). */
    private int watchdogRestartTimeoutMs = 35000;
    /** Rappel d'historique d'un épisode RESOLUTION_FAILED encore ouvert (minutes). */
    private int episodeReminderMinutes = 15;
    /**
     * Route DIRECTE du backend (.160, sans Camunda) : budget (minutes) pendant lequel les preuves de récupération sont
     * attendues après une demande de démarrage acceptée ; au-delà, l'incident passe en RESOLUTION_FAILED (explicite).
     */
    private int directVerifyBudgetMinutes = 6;
    /** Preuves de récupération d'un worker : cluster PostgreSQL CNPG (namespace, nom, instances attendues). */
    private String cnpgNamespace = "pfe";
    private String cnpgCluster = "postgres";
    private int cnpgInstances = 2;
    /** Preuves de récupération d'un worker : Deployment du backend (namespace, nom). */
    private String backendNamespace = "pfe";
    private String backendDeployment = "backend";
    /**
     * Activation RÉELLE par VM (noms exacts, ex. {@code HELIOS_VMPOWER_ACTIVE_VMS=k8s-worker1}). Un démarrage n'est
     * demandé que si {@code enabled && !dryRun && activeVms ∋ vm} : trois verrous, tous fermés par défaut.
     */
    private Set<String> activeVms = Set.of();

    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }

    public boolean isDryRun() { return dryRun; }
    public void setDryRun(boolean dryRun) { this.dryRun = dryRun; }

    public int getCycleSeconds() { return cycleSeconds; }
    public void setCycleSeconds(int cycleSeconds) { this.cycleSeconds = cycleSeconds; }

    public int getProbeTimeoutMs() { return probeTimeoutMs; }
    public void setProbeTimeoutMs(int probeTimeoutMs) { this.probeTimeoutMs = probeTimeoutMs; }

    public int getProbeThreads() { return probeThreads; }
    public void setProbeThreads(int probeThreads) { this.probeThreads = probeThreads; }

    public int getWatchdogConnectTimeoutMs() { return watchdogConnectTimeoutMs; }
    public void setWatchdogConnectTimeoutMs(int v) { this.watchdogConnectTimeoutMs = v; }

    public int getWatchdogReadTimeoutMs() { return watchdogReadTimeoutMs; }
    public void setWatchdogReadTimeoutMs(int v) { this.watchdogReadTimeoutMs = v; }

    public int getWatchdogRestartTimeoutMs() { return watchdogRestartTimeoutMs; }
    public void setWatchdogRestartTimeoutMs(int v) { this.watchdogRestartTimeoutMs = v; }

    public int getEpisodeReminderMinutes() { return episodeReminderMinutes; }
    public void setEpisodeReminderMinutes(int v) { this.episodeReminderMinutes = v; }

    public int getDirectVerifyBudgetMinutes() { return directVerifyBudgetMinutes; }
    public void setDirectVerifyBudgetMinutes(int v) { this.directVerifyBudgetMinutes = v; }

    public String getCnpgNamespace() { return cnpgNamespace; }
    public void setCnpgNamespace(String v) { this.cnpgNamespace = v; }

    public String getCnpgCluster() { return cnpgCluster; }
    public void setCnpgCluster(String v) { this.cnpgCluster = v; }

    public int getCnpgInstances() { return cnpgInstances; }
    public void setCnpgInstances(int v) { this.cnpgInstances = v; }

    public String getBackendNamespace() { return backendNamespace; }
    public void setBackendNamespace(String v) { this.backendNamespace = v; }

    public String getBackendDeployment() { return backendDeployment; }
    public void setBackendDeployment(String v) { this.backendDeployment = v; }

    public Set<String> getActiveVms() { return activeVms; }
    public void setActiveVms(Set<String> activeVms) { this.activeVms = activeVms == null ? Set.of() : Set.copyOf(activeVms); }
}
