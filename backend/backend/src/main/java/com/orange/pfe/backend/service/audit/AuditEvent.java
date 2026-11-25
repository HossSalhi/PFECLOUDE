package com.orange.pfe.backend.service.audit;

import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.audit.AuditResult;
import com.orange.pfe.backend.model.user.AppUser;

import java.util.Map;

/**
 * Description d'une activité à tracer. Construction fluide :
 * <pre>
 * AuditEvent.of(AuditModule.TEAMS, AuditAction.CREATE).actor(actor).object("Team", team.getId())
 *           .summary("Création de l'équipe X").newValues(Map.of("name", "X"))
 * </pre>
 * L'acteur est déduit du contexte de sécurité s'il n'est pas fourni. Les valeurs sont assainies (secrets masqués)
 * par {@link AuditSanitizer} avant toute écriture.
 */
public final class AuditEvent {

    private final AuditModule module;
    private final AuditAction action;
    private AppUser actor;
    private String actorUsername;
    private String objectType;
    private String objectId;
    private String summary;
    private Map<String, ?> oldValues;
    private Map<String, ?> newValues;
    private AuditResult result = AuditResult.SUCCESS;

    private AuditEvent(AuditModule module, AuditAction action) {
        this.module = module;
        this.action = action;
    }

    public static AuditEvent of(AuditModule module, AuditAction action) {
        return new AuditEvent(module, action);
    }

    public AuditEvent actor(AppUser actor) {
        this.actor = actor;
        return this;
    }

    /** Nom d'acteur explicite (ex. tentative de connexion échouée : l'utilisateur n'est pas authentifié). */
    public AuditEvent actorUsername(String actorUsername) {
        this.actorUsername = actorUsername;
        return this;
    }

    public AuditEvent object(String type, Object id) {
        this.objectType = type;
        this.objectId = id == null ? null : String.valueOf(id);
        return this;
    }

    public AuditEvent summary(String summary) {
        this.summary = summary;
        return this;
    }

    public AuditEvent oldValues(Map<String, ?> oldValues) {
        this.oldValues = oldValues;
        return this;
    }

    public AuditEvent newValues(Map<String, ?> newValues) {
        this.newValues = newValues;
        return this;
    }

    public AuditEvent result(AuditResult result) {
        this.result = result == null ? AuditResult.SUCCESS : result;
        return this;
    }

    public AuditModule module() { return module; }
    public AuditAction action() { return action; }
    public AppUser actor() { return actor; }
    public String actorUsername() { return actorUsername; }
    public String objectType() { return objectType; }
    public String objectId() { return objectId; }
    public String summary() { return summary; }
    public Map<String, ?> oldValues() { return oldValues; }
    public Map<String, ?> newValues() { return newValues; }
    public AuditResult result() { return result; }
}
