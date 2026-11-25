package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface IncidentRepository extends JpaRepository<Incident, Long> {

    List<Incident> findByStatus(IncidentStatus status);

    /**
     * VMDOWN-POLICY-20261105 — verrou de ligne PESSIMISTE (SELECT ... FOR UPDATE) sur l'incident. Sert à
     * sérialiser la création d'une action RESTART_VM : deux créateurs concurrents pour le même incident ne
     * peuvent plus passer le contrôle « aucune action RESTART_VM » en même temps. Sans migration ni schéma.
     */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT i FROM Incident i WHERE i.id = :id")
    Optional<Incident> findByIdForUpdate(@Param("id") Long id);

    long countByStatus(IncidentStatus status);

    List<Incident> findBySeverity(IncidentSeverity severity);

    long countBySeverity(IncidentSeverity severity);

    List<Incident> findBySource(IncidentSource source);

    boolean existsByStatusInAndSource(Collection<IncidentStatus> statuses, IncidentSource source);

    boolean existsByStatusInAndSourceAndNamespaceAndApp(
            Collection<IncidentStatus> statuses, IncidentSource source, String namespace, String app);

    Optional<Incident> findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(
            Collection<IncidentStatus> statuses, IncidentSource source, String namespace, String app);

    List<Incident> findByStatusAndNamespaceAndApp(IncidentStatus status, String namespace, String app);

    boolean existsByStatusAndSourceAndNamespaceAndAppAndCreatedAtAfter(
            IncidentStatus status, IncidentSource source, String namespace, String app, LocalDateTime after);

    boolean existsByStatusAndSourceAndNamespaceAndApp(
            IncidentStatus status, IncidentSource source, String namespace, String app);

    boolean existsByStatusAndSourceAndNamespaceAndAppAndUpdatedAtAfter(
            IncidentStatus status, IncidentSource source, String namespace, String app, LocalDateTime after);

    // Any AI incident for this app created recently, regardless of status — used to suppress
    // churn duplicates (residual metrics re-triggering detection right after a resolution).
    boolean existsBySourceAndNamespaceAndAppAndCreatedAtAfter(
            IncidentSource source, String namespace, String app, LocalDateTime after);

    Optional<Incident> findFirstByNamespaceAndAppAndIncidentTypeAndStatusNotInOrderByCreatedAtDesc(
            String namespace,
            String app,
            String incidentType,
            Collection<IncidentStatus> excludedStatuses);

    Page<Incident> findAll(Pageable pageable);

    Page<Incident> findByStatus(IncidentStatus status, Pageable pageable);

    // ── Namespace-scoped queries for multi-tenancy ────────────────────────────

    Page<Incident> findByNamespaceIn(Collection<String> namespaces, Pageable pageable);

    Page<Incident> findByStatusAndNamespaceIn(IncidentStatus status, Collection<String> namespaces, Pageable pageable);

    long countBySeverityAndNamespaceIn(IncidentSeverity severity, Collection<String> namespaces);
}
