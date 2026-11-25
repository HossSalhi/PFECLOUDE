package com.orange.pfe.backend.model.team;

import com.orange.pfe.backend.model.user.AppUser;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.time.LocalDateTime;

/** Appartenance d'un utilisateur à une équipe. Un utilisateur ne peut figurer qu'une fois dans une équipe. */
@Entity
@Table(name = "team_member",
        uniqueConstraints = @UniqueConstraint(name = "uq_team_member", columnNames = {"team_id", "user_id"}))
public class TeamMember {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "team_id", nullable = false)
    private Team team;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id", nullable = false)
    private AppUser user;

    @Column(name = "added_by_username", length = 50)
    private String addedByUsername;

    @Column(name = "added_at", nullable = false, updatable = false)
    private LocalDateTime addedAt;

    protected TeamMember() {
        // JPA
    }

    public TeamMember(Team team, AppUser user, String addedByUsername) {
        this.team = team;
        this.user = user;
        this.addedByUsername = addedByUsername;
    }

    @PrePersist
    protected void onCreate() {
        this.addedAt = LocalDateTime.now();
    }

    public Long getId() { return id; }
    public Team getTeam() { return team; }
    public AppUser getUser() { return user; }
    public String getAddedByUsername() { return addedByUsername; }
    public LocalDateTime getAddedAt() { return addedAt; }
}
