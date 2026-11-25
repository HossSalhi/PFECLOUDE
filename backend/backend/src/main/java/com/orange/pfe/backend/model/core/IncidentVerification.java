package com.orange.pfe.backend.model.core;

import jakarta.persistence.*;

import java.time.LocalDateTime;

/**
 * Résultat de la vérification finale d'un incident (critères, mesures après, verdict, motif de clôture /
 * d'échec / d'escalade). LOT HELIOS-INCIDENT-IDENTITY-CARD-01. Entité autonome, reliée par {@code incidentId}.
 * Renseignée par le composant qui réalise la vérification (ex. worker verify-remediation) ; tant qu'elle ne
 * l'est pas, {@code available} = false et l'UI affiche « NON COLLECTÉ ».
 */
@Entity
@Table(name = "incident_verification")
public class IncidentVerification {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "incident_id", nullable = false)
    private Long incidentId;

    /** Critères de vérification appliqués (ex. « nœud Ready » / « host_cpu < 90 % »). */
    @Column(name = "criteria", length = 1000)
    private String criteria;

    /** Verdict : PASS | FAIL | ESCALATED | PENDING. */
    @Column(name = "result", length = 32)
    private String result;

    /** Mesures après remédiation (JSON libre) ; null si non mesurées. */
    @Column(name = "measures_json", columnDefinition = "TEXT")
    private String measuresJson;

    /** Motif de clôture / d'échec / d'escalade, lisible par un humain. */
    @Column(name = "reason", columnDefinition = "TEXT")
    private String reason;

    @Column(name = "verified_at")
    private LocalDateTime verifiedAt;

    @Column(name = "available", nullable = false)
    private boolean available = false;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    void onCreate() {
        this.createdAt = LocalDateTime.now();
        if (this.verifiedAt == null) {
            this.verifiedAt = this.createdAt;
        }
    }

    public Long getId() { return id; }
    public Long getIncidentId() { return incidentId; }
    public void setIncidentId(Long incidentId) { this.incidentId = incidentId; }
    public String getCriteria() { return criteria; }
    public void setCriteria(String criteria) { this.criteria = criteria; }
    public String getResult() { return result; }
    public void setResult(String result) { this.result = result; }
    public String getMeasuresJson() { return measuresJson; }
    public void setMeasuresJson(String measuresJson) { this.measuresJson = measuresJson; }
    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }
    public LocalDateTime getVerifiedAt() { return verifiedAt; }
    public void setVerifiedAt(LocalDateTime verifiedAt) { this.verifiedAt = verifiedAt; }
    public boolean isAvailable() { return available; }
    public void setAvailable(boolean available) { this.available = available; }
    public LocalDateTime getCreatedAt() { return createdAt; }
}
