package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import org.springframework.data.repository.Repository;

import java.time.LocalDateTime;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

/**
 * Lectures d'incidents propres au routage incident → équipe (dépôt séparé : {@link IncidentRepository}, utilisé par la
 * détection et la remédiation, n'est pas modifié). Lecture seule.
 */
public interface IncidentRoutingQueryRepository extends Repository<Incident, Long> {

    Optional<Incident> findById(Long id);

    /** Incidents encore actifs créés après {@code after}, du plus ancien au plus récent. */
    List<Incident> findByStatusInAndCreatedAtAfterOrderByCreatedAtAsc(Collection<IncidentStatus> statuses, LocalDateTime after);
}
