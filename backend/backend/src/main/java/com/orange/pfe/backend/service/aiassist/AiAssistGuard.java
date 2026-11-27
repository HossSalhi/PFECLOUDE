package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Narrative;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.TeamRepository;
import com.orange.pfe.backend.service.org.OrgAccessService;
import org.springframework.stereotype.Component;

/**
 * Point unique d'entrée des 4 fonctions : applique la règle d'accès (admin = tout ; chef actif = SON équipe ; sinon refus,
 * tracé) puis prépare le texte IA. L'IA n'est appelée qu'ici, à la demande, après vérification du droit.
 */
@Component
public class AiAssistGuard {

    private final OrgAccessService access;
    private final TeamRepository teams;
    private final AiCompletionClient client;

    public AiAssistGuard(OrgAccessService access, TeamRepository teams, AiCompletionClient client) {
        this.access = access;
        this.teams = teams;
        this.client = client;
    }

    /**
     * Vérifie le droit pour l'équipe demandée et la renvoie ({@code null} si un administrateur n'a pas ciblé d'équipe).
     * Un chef d'équipe doit toujours cibler SON équipe : sans équipe, ou avec celle d'un autre, c'est refusé.
     */
    public Team authorize(AppUser actor, Long teamId) {
        access.requireUser(actor);
        Team team = null;
        if (teamId != null) {
            team = teams.findById(teamId).orElseThrow(() -> new ResourceNotFoundException("Équipe introuvable : " + teamId));
        }
        access.requireAiAssistFor(actor, team);
        return team;
    }

    public boolean isAdmin(AppUser actor) {
        return access.isAdminRole(actor);
    }

    /** Vrai si la fonction est désactivée : l'appelant renvoie alors un état « désactivé » sans rien calculer. */
    public boolean disabled(AiFeature feature) {
        return client.notReadyReason(feature) == AiCompletionResult.Reason.DISABLED;
    }

    public Narrative disabledNarrative() {
        return Narrative.from(new AiCompletionResult.Unavailable(AiCompletionResult.Reason.DISABLED, java.time.Instant.now()));
    }

    /** Appel IA à la demande ; les alias d'{@code names} sont remplacés par les vrais noms dans le texte reçu. */
    public Narrative narrate(AiFeature feature, String system, String user, AiNames names) {
        AiCompletionResult r = client.complete(feature, system, user);
        if (r instanceof AiCompletionResult.Success ok && names != null) {
            return new Narrative("OK", null, names.restore(ok.text()), ok.model(), ok.generatedAt());
        }
        return Narrative.from(r);
    }

    /** Consigne commune : l'IA n'invente rien, ne décide rien, et n'utilise que les données fournies. */
    public static final String SYSTEM = "Tu es un assistant de rédaction pour la plateforme HELIOS (supervision d'infrastructure). "
            + "Tu réponds en français, de façon professionnelle et concise. Tu n'utilises QUE les chiffres et faits fournis : "
            + "tu n'inventes aucune donnée, aucune cause, aucun nom. Quand tu cites une personne ou une ressource, recopie son nom exact "
            + "tel que fourni (par exemple « Machine A » ou « Technicien B », avec une espace ordinaire), sans l'abréger ni regrouper "
            + "plusieurs noms (jamais « Machines A, B »). Tu ne prends aucune décision et ne déclenches aucune "
            + "action : tu formules seulement une synthèse ou une suggestion que l'humain confirmera.";
}
