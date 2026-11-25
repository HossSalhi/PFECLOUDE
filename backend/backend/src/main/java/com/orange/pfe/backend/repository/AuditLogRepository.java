package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.audit.AuditLogEntry;
import org.springframework.data.repository.Repository;

import java.util.Optional;

/**
 * Repository du journal d'audit — volontairement RÉDUIT : écriture d'une nouvelle entrée et lecture seulement.
 * Il n'étend pas {@code JpaRepository} : aucune méthode de modification ni de suppression n'est exposée
 * (garanti par {@code AuditLogRepositoryTest}).
 */
public interface AuditLogRepository extends Repository<AuditLogEntry, Long> {

    AuditLogEntry save(AuditLogEntry entry);

    Optional<AuditLogEntry> findById(Long id);

    long count();
}
