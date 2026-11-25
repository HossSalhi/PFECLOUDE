package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.core.IncidentVerification;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

/** LOT HELIOS-INCIDENT-IDENTITY-CARD-01 : accès au résultat de vérification finale d'un incident. */
public interface IncidentVerificationRepository extends JpaRepository<IncidentVerification, Long> {

    List<IncidentVerification> findByIncidentIdOrderByVerifiedAtDesc(Long incidentId);

    Optional<IncidentVerification> findTopByIncidentIdOrderByVerifiedAtDesc(Long incidentId);
}
