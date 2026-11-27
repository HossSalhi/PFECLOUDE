package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.telemetry.TelemetryAnalysisRun;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.repository.Repository;

import java.util.Collection;
import java.util.Optional;

/**
 * Dépôt VOLONTAIREMENT réduit (comme {@code AuditLogRepository}) : écriture d'une nouvelle analyse et lecture seulement.
 * Il n'étend pas {@code JpaRepository} : aucune méthode de modification ni de suppression n'est exposée.
 */
public interface TelemetryAnalysisRunRepository extends Repository<TelemetryAnalysisRun, Long> {

    TelemetryAnalysisRun save(TelemetryAnalysisRun run);

    Optional<TelemetryAnalysisRun> findById(Long id);

    /** Historique complet (administrateurs), plus récent d'abord. */
    Page<TelemetryAnalysisRun> findAllByOrderByRequestedAtDesc(Pageable pageable);

    /** Historique d'une équipe précise, plus récent d'abord. */
    Page<TelemetryAnalysisRun> findByTeamIdOrderByRequestedAtDesc(Long teamId, Pageable pageable);

    /** Historique des équipes dirigées par un chef : jamais les analyses des autres équipes. */
    Page<TelemetryAnalysisRun> findByTeamIdInOrderByRequestedAtDesc(Collection<Long> teamIds, Pageable pageable);
}
