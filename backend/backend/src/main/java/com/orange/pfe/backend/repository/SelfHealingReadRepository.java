package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.core.Incident;
import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.EnumSet;
import java.util.List;
import java.util.Set;

/** Lecture seule, bornée : incidents ayant déclenché au moins une VRAIE remédiation dans une période. */
@Repository
public class SelfHealingReadRepository {

    /** Types d'action qui ne sont pas de vraies remédiations. */
    public static final Set<ActionType> NOT_REMEDIATION = EnumSet.of(ActionType.NOTIFICATION, ActionType.INVESTIGATION, ActionType.ESCALATION);

    @PersistenceContext
    private EntityManager em;

    /** Incidents (les plus récents d'abord) dont une remédiation a été créée dans [from, to[, au plus {@code max}. */
    @Transactional(readOnly = true)
    public List<Incident> incidentsWithRemediationBetween(LocalDateTime from, LocalDateTime to, int max) {
        return em.createQuery("select i from Incident i where i.id in (select a.incident.id from IncidentAction a "
                + "where a.createdAt >= :from and a.createdAt < :to and a.type not in :excluded) order by i.id desc", Incident.class)
                .setParameter("from", from).setParameter("to", to).setParameter("excluded", NOT_REMEDIATION)
                .setMaxResults(max).getResultList();
    }
}
