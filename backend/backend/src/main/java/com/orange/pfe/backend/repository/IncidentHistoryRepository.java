package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.core.IncidentHistory;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface IncidentHistoryRepository extends JpaRepository<IncidentHistory, Long> {

    /**
     * Récupère tout l'historique pour un incident donné
     */
    List<IncidentHistory> findByIncidentId(Long incidentId);

    /**
     * Récupère l'historique paginé pour un incident
     */
    Page<IncidentHistory> findAllByIncidentId(Long incidentId, Pageable pageable);

    /**
     * Récupère l'historique paginé pour un incident filtré par status
     */
    Page<IncidentHistory> findAllByIncidentIdAndStatus(Long incidentId, IncidentStatus status, Pageable pageable);

    /**
     * Audit log global — tous les incidents, filtrable par status
     */
    Page<IncidentHistory> findAllByStatus(IncidentStatus status, Pageable pageable);

    Page<IncidentHistory> findAllByOrderByCreatedAtDesc(Pageable pageable);
}