package com.orange.pfe.backend.model.core;

import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import jakarta.persistence.*;

import java.time.LocalDateTime;

/**
 * Preuve terminal AVANT/APRÈS d'un incident : sortie réelle d'une commande hôte/VM, exécutée par un
 * mécanisme d'exécution ÉTABLI (Bridge / watchdog) et simplement ENREGISTRÉE ici.
 *
 * LOT HELIOS-INCIDENT-IDENTITY-CARD-01. Entité autonome (pas de BaseEntity) : référence l'incident par
 * {@code incidentId} (aucune modification de {@link Incident}). Ce lot ne PRODUIT aucune de ces commandes ;
 * il stocke et affiche ce que les lots de remédiation fourniront. Quand une preuve manque, {@code available}
 * = false et l'UI affiche « NON COLLECTÉ » (jamais de valeur fictive).
 */
@Entity
@Table(name = "incident_terminal_evidence")
public class IncidentTerminalEvidence {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "incident_id", nullable = false)
    private Long incidentId;

    /** VM cible du relevé (ex. k8s-worker1). */
    @Column(name = "vm")
    private String vm;

    @Enumerated(EnumType.STRING)
    @Column(name = "phase", nullable = false, length = 16)
    private TerminalEvidencePhase phase;

    @Column(name = "captured_at")
    private LocalDateTime capturedAt;

    @Column(name = "command", length = 1000)
    private String command;

    @Column(name = "exit_code")
    private Integer exitCode;

    @Column(name = "stdout", columnDefinition = "TEXT")
    private String stdout;

    @Column(name = "stderr", columnDefinition = "TEXT")
    private String stderr;

    /** Mesures extraites (JSON libre : cpu, disque, inodes, état nœud…) ; null si non extraites. */
    @Column(name = "measures_json", columnDefinition = "TEXT")
    private String measuresJson;

    /** false => relevé NON COLLECTÉ / NON DISPONIBLE (ne jamais inventer de valeur). */
    @Column(name = "available", nullable = false)
    private boolean available = false;

    /** Origine du relevé : BRIDGE, WATCHDOG, FIXTURE… (FIXTURE = donnée de test explicite). */
    @Column(name = "source", length = 32)
    private String source;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    void onCreate() {
        this.createdAt = LocalDateTime.now();
        if (this.capturedAt == null) {
            this.capturedAt = this.createdAt;
        }
    }

    public Long getId() { return id; }
    public Long getIncidentId() { return incidentId; }
    public void setIncidentId(Long incidentId) { this.incidentId = incidentId; }
    public String getVm() { return vm; }
    public void setVm(String vm) { this.vm = vm; }
    public TerminalEvidencePhase getPhase() { return phase; }
    public void setPhase(TerminalEvidencePhase phase) { this.phase = phase; }
    public LocalDateTime getCapturedAt() { return capturedAt; }
    public void setCapturedAt(LocalDateTime capturedAt) { this.capturedAt = capturedAt; }
    public String getCommand() { return command; }
    public void setCommand(String command) { this.command = command; }
    public Integer getExitCode() { return exitCode; }
    public void setExitCode(Integer exitCode) { this.exitCode = exitCode; }
    public String getStdout() { return stdout; }
    public void setStdout(String stdout) { this.stdout = stdout; }
    public String getStderr() { return stderr; }
    public void setStderr(String stderr) { this.stderr = stderr; }
    public String getMeasuresJson() { return measuresJson; }
    public void setMeasuresJson(String measuresJson) { this.measuresJson = measuresJson; }
    public boolean isAvailable() { return available; }
    public void setAvailable(boolean available) { this.available = available; }
    public String getSource() { return source; }
    public void setSource(String source) { this.source = source; }
    public LocalDateTime getCreatedAt() { return createdAt; }
}
