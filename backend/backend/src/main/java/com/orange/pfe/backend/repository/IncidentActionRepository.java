package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.IncidentAction;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface IncidentActionRepository extends JpaRepository<IncidentAction, Long> {

    Page<IncidentAction> findByIncidentId(Long incidentId, Pageable pageable);

    Optional<IncidentAction> findTopByIncidentIdOrderByCreatedAtDesc(Long incidentId);

    Page<IncidentAction> findByStatus(ActionStatus status, Pageable pageable);

    Page<IncidentAction> findByIncidentIdAndStatus(Long incidentId, ActionStatus status, Pageable pageable);

    @Query("""
            SELECT action
            FROM IncidentAction action
            WHERE action.createdAt >= :cutoff
            ORDER BY action.createdAt DESC
            """)
    List<IncidentAction> findRecentActions(@Param("cutoff") LocalDateTime cutoff);

    @Query("""
            SELECT action
            FROM IncidentAction action
            WHERE action.targetNamespace = :namespace
              AND action.targetName = :targetName
              AND action.type = :type
              AND action.createdAt >= :cutoff
            ORDER BY action.createdAt DESC
            """)
    List<IncidentAction> findRecentActionsForTarget(
            @Param("namespace") String namespace,
            @Param("targetName") String targetName,
            @Param("type") ActionType type,
            @Param("cutoff") LocalDateTime cutoff
    );

    @Query("""
            SELECT action
            FROM IncidentAction action
            WHERE action.targetNamespace = :namespace
              AND action.targetName = :targetName
              AND action.createdAt >= :cutoff
            ORDER BY action.createdAt DESC
            """)
    List<IncidentAction> findRecentActionsForTarget(
            @Param("namespace") String namespace,
            @Param("targetName") String targetName,
            @Param("cutoff") LocalDateTime cutoff
    );
}
