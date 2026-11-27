package com.orange.pfe.backend.model.team;

import com.orange.pfe.backend.model.user.AppUser;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.time.LocalDateTime;
import java.util.Locale;

/** Équipe d'utilisateurs HELIOS. Le responsable et le créateur référencent l'entité utilisateur existante. */
@Entity
@Table(name = "team", uniqueConstraints = @UniqueConstraint(name = "uq_team_name_key", columnNames = "name_key"))
public class Team {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, length = 100)
    private String name;

    /** Nom normalisé (minuscules, espaces retirés) : garantit l'unicité insensible à la casse. */
    @Column(name = "name_key", nullable = false, length = 100)
    private String nameKey;

    @Column(length = 1000)
    private String description;

    /** Spécialité de l'équipe (une seule valeur) ; {@code null} = non spécialisée, jamais routée automatiquement. */
    @Enumerated(EnumType.STRING)
    @Column(length = 16)
    private ResourceScope specialty;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "lead_id")
    private AppUser lead;

    @Column(nullable = false)
    private boolean active = true;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "created_by_id")
    private AppUser createdBy;

    @Column(name = "created_by_username", length = 50)
    private String createdByUsername;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    @PrePersist
    protected void onCreate() {
        LocalDateTime now = LocalDateTime.now();
        this.createdAt = now;
        this.updatedAt = now;
    }

    @PreUpdate
    protected void onUpdate() {
        this.updatedAt = LocalDateTime.now();
    }

    public static String keyOf(String name) {
        return name == null ? null : name.trim().toLowerCase(Locale.ROOT);
    }

    public Long getId() { return id; }

    public String getName() { return name; }
    public void setName(String name) {
        this.name = name == null ? null : name.trim();
        this.nameKey = keyOf(name);
    }

    public String getNameKey() { return nameKey; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public ResourceScope getSpecialty() { return specialty; }
    public void setSpecialty(ResourceScope specialty) { this.specialty = specialty; }
    public AppUser getLead() { return lead; }
    public void setLead(AppUser lead) { this.lead = lead; }

    public boolean isActive() { return active; }
    public void setActive(boolean active) { this.active = active; }

    public AppUser getCreatedBy() { return createdBy; }
    public void setCreatedBy(AppUser createdBy) { this.createdBy = createdBy; }

    public String getCreatedByUsername() { return createdByUsername; }
    public void setCreatedByUsername(String createdByUsername) { this.createdByUsername = createdByUsername; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
}
