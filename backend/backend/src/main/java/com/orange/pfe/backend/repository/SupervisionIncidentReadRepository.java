package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.core.Incident;
import org.springframework.data.repository.Repository;

import java.time.LocalDateTime;
import java.util.List;

/** Lecture seule des incidents pour la supervision (rapprochement avec les logs et les métriques). */
public interface SupervisionIncidentReadRepository extends Repository<Incident, Long> {

    List<Incident> findByCreatedAtAfterOrderByCreatedAtAsc(LocalDateTime after);
}
