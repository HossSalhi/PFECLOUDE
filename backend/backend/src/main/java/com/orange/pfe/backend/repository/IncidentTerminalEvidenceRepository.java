package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.core.IncidentTerminalEvidence;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

/** LOT HELIOS-INCIDENT-IDENTITY-CARD-01 : accès aux preuves terminal d'un incident. */
public interface IncidentTerminalEvidenceRepository extends JpaRepository<IncidentTerminalEvidence, Long> {

    List<IncidentTerminalEvidence> findByIncidentIdOrderByCapturedAtAsc(Long incidentId);

    List<IncidentTerminalEvidence> findByIncidentIdAndPhaseOrderByCapturedAtAsc(
            Long incidentId, TerminalEvidencePhase phase);

    /** Anti-doublon : même incident + phase + commande déjà enregistrée. */
    boolean existsByIncidentIdAndPhaseAndCommand(
            Long incidentId, TerminalEvidencePhase phase, String command);
}
