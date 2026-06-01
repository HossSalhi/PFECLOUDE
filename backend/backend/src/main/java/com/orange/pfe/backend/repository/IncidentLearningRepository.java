package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.learning.IncidentLearning;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface IncidentLearningRepository extends JpaRepository<IncidentLearning, Long> {

    boolean existsByIncidentId(Long incidentId);

    List<IncidentLearning> findTop20ByNamespaceAndAppOrderByCreatedAtDesc(String namespace, String app);

    @Query("""
            SELECT l FROM IncidentLearning l
            WHERE l.incidentType = :incidentType
            ORDER BY l.createdAt DESC
            LIMIT 10
            """)
    List<IncidentLearning> findRecentByType(@Param("incidentType") String incidentType);

    @Query("""
            SELECT l FROM IncidentLearning l
            ORDER BY l.createdAt DESC
            LIMIT 30
            """)
    List<IncidentLearning> findLatest();
}
