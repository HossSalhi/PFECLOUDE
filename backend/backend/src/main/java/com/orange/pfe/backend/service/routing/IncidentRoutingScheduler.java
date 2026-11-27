package com.orange.pfe.backend.service.routing;

import com.orange.pfe.backend.service.LeaderElectionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Déclenche le routage incident → équipe. DÉSACTIVÉ par défaut ({@code helios.routing.enabled=false}) : il envoie des
 * e-mails et crée des tâches d'équipe automatiquement, il doit donc être activé volontairement.
 *
 * <p>Ne s'exécute que sur le leader (haute disponibilité, deux répliques du backend). Il LIT les incidents : aucun
 * service de détection ni de remédiation n'est modifié ni appelé. Chaque incident est traité dans sa propre
 * transaction ; une erreur sur l'un n'empêche pas les autres.</p>
 */
@Component
@ConditionalOnProperty(name = "helios.routing.enabled", havingValue = "true")
public class IncidentRoutingScheduler {

    private static final Logger log = LoggerFactory.getLogger(IncidentRoutingScheduler.class);

    private final IncidentTeamRoutingService routing;
    private final LeaderElectionService leaderElection;

    public IncidentRoutingScheduler(IncidentTeamRoutingService routing, LeaderElectionService leaderElection) {
        this.routing = routing;
        this.leaderElection = leaderElection;
    }

    @Scheduled(fixedDelayString = "${helios.routing.scheduler-delay-ms:30000}",
            initialDelayString = "${helios.routing.initial-delay-ms:60000}")
    public void run() {
        if (!leaderElection.isLeader()) {
            return;
        }
        for (Long incidentId : routing.newIncidentIds()) {
            try {
                IncidentTeamRoutingService.Outcome outcome = routing.routeIncident(incidentId);
                if (outcome == IncidentTeamRoutingService.Outcome.ROUTED) {
                    log.info("Incident #{} routé vers l'équipe spécialisée (maintenance en cours sur sa ressource)", incidentId);
                }
            } catch (RuntimeException e) {
                log.warn("Routage de l'incident #{} impossible : {}", incidentId, e.toString());
            }
        }
        for (Long incidentId : routing.resolvedIncidentIdsAwaitingResume()) {
            try {
                if (routing.notifyResolution(incidentId)) {
                    log.info("Incident #{} résolu : reprise de la maintenance proposée à l'équipe", incidentId);
                }
            } catch (RuntimeException e) {
                log.warn("Notification de reprise pour l'incident #{} impossible : {}", incidentId, e.toString());
            }
        }
    }
}
