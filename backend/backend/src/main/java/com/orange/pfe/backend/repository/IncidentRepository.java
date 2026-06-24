package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDateTime;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface IncidentRepository extends JpaRepository<Incident, Long> {

    List<Incident> findByStatus(IncidentStatus status);

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

    Page<Incident> findAll(Pageable pageable);

    Page<Incident> findByStatus(IncidentStatus status, Pageable pageable);

    // ── Namespace-scoped queries for multi-tenancy ────────────────────────────

    Page<Incident> findByNamespaceIn(Collection<String> namespaces, Pageable pageable);

    Page<Incident> findByStatusAndNamespaceIn(IncidentStatus status, Collection<String> namespaces, Pageable pageable);

    long countBySeverityAndNamespaceIn(IncidentSeverity severity, Collection<String> namespaces);
}
