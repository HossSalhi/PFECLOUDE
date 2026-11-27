package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Capabilities;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.FeatureState;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.LedTeam;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.TeamRepository;
import com.orange.pfe.backend.service.org.OrgAccessService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/** Dit à l'interface quelles fonctions IA afficher à cet utilisateur (aucun appel IA, aucune donnée métier). */
@Service
public class AiCapabilitiesService {

    private final OrgAccessService access;
    private final TeamRepository teams;
    private final AiCompletionClient client;

    public AiCapabilitiesService(OrgAccessService access, TeamRepository teams, AiCompletionClient client) {
        this.access = access;
        this.teams = teams;
        this.client = client;
    }

    @Transactional(readOnly = true)
    public Capabilities capabilities(AppUser actor) {
        access.requireUser(actor);
        boolean admin = access.isAdminRole(actor);
        List<LedTeam> led = admin ? List.of()
                : teams.findActiveByLeadIds(List.of(actor.getId())).stream().map(t -> new LedTeam(t.getId(), t.getName())).toList();
        boolean allowed = admin || !led.isEmpty();
        return new Capabilities(admin, led, allowed,
                state(allowed && admin, AiFeature.REPORTS_SUMMARY),    // résumé d'un rapport global : administrateurs seulement
                state(allowed, AiFeature.REPORTS_SUMMARY),             // résumé de l'activité d'une équipe : admin ou chef actif
                state(allowed, AiFeature.AUDIT_ANOMALIES),
                state(allowed, AiFeature.TASK_ASSIGNMENT),
                state(allowed, AiFeature.MAINTENANCE_WINDOW),
                state(allowed, AiFeature.TELEMETRY_ANALYSIS));
    }

    private FeatureState state(boolean permitted, AiFeature feature) {
        if (!permitted) {
            return FeatureState.off();
        }
        AiCompletionResult.Reason why = client.notReadyReason(feature);
        if (why == null) {
            return new FeatureState(true, true, null);
        }
        // désactivée : l'interface masque la fonction ; non configurée : elle l'affiche avec l'explication
        return new FeatureState(why != AiCompletionResult.Reason.DISABLED, false, why == AiCompletionResult.Reason.DISABLED ? null : why.message());
    }
}
