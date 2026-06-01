package com.orange.pfe.backend.model.common;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;

@Embeddable
public class AuditActor {

    @Column(name = "actor_name")
    private String actorName;

    @Column(name = "actor_type")
    private String actorType;

    public String getActorName() {
        return actorName;
    }

    public void setActorName(String actorName) {
        this.actorName = actorName;
    }

    public String getActorType() {
        return actorType;
    }

    public void setActorType(String actorType) {
        this.actorType = actorType;
    }
}
