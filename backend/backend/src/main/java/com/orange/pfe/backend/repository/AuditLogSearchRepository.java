package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.model.audit.AuditLogEntry;
import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import jakarta.persistence.TypedQuery;
import jakarta.persistence.criteria.CriteriaBuilder;
import jakarta.persistence.criteria.CriteriaQuery;
import jakarta.persistence.criteria.Predicate;
import jakarta.persistence.criteria.Root;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Recherche LECTURE SEULE dans le journal d'audit (API Criteria : seuls les filtres renseignés produisent un
 * prédicat, ce qui évite les paramètres nuls non typés côté PostgreSQL). Ne fait aucune écriture.
 */
@Repository
public class AuditLogSearchRepository {

    @PersistenceContext
    private EntityManager em;

    @Transactional(readOnly = true)
    public Page<AuditLogEntry> search(AuditSearchCriteria criteria, Pageable pageable) {
        CriteriaBuilder cb = em.getCriteriaBuilder();

        CriteriaQuery<AuditLogEntry> cq = cb.createQuery(AuditLogEntry.class);
        Root<AuditLogEntry> root = cq.from(AuditLogEntry.class);
        cq.select(root)
                .where(predicates(cb, root, criteria).toArray(new Predicate[0]))
                .orderBy(cb.desc(root.get("occurredAt")), cb.desc(root.get("id")));
        List<AuditLogEntry> content = em.createQuery(cq)
                .setFirstResult((int) pageable.getOffset())
                .setMaxResults(pageable.getPageSize())
                .getResultList();

        CriteriaQuery<Long> count = cb.createQuery(Long.class);
        Root<AuditLogEntry> countRoot = count.from(AuditLogEntry.class);
        count.select(cb.count(countRoot)).where(predicates(cb, countRoot, criteria).toArray(new Predicate[0]));
        long total = em.createQuery(count).getSingleResult();

        return new PageImpl<>(content, pageable, total);
    }

    /** Résultats les plus récents d'abord, bornés à {@code maxRows} (export). */
    @Transactional(readOnly = true)
    public List<AuditLogEntry> searchForExport(AuditSearchCriteria criteria, int maxRows) {
        CriteriaBuilder cb = em.getCriteriaBuilder();
        CriteriaQuery<AuditLogEntry> cq = cb.createQuery(AuditLogEntry.class);
        Root<AuditLogEntry> root = cq.from(AuditLogEntry.class);
        cq.select(root)
                .where(predicates(cb, root, criteria).toArray(new Predicate[0]))
                .orderBy(cb.desc(root.get("occurredAt")), cb.desc(root.get("id")));
        TypedQuery<AuditLogEntry> query = em.createQuery(cq).setMaxResults(maxRows);
        return query.getResultList();
    }

    private List<Predicate> predicates(CriteriaBuilder cb, Root<AuditLogEntry> root, AuditSearchCriteria c) {
        List<Predicate> list = new ArrayList<>();
        if (c.actorUserId() != null) {
            list.add(cb.equal(root.get("actorUserId"), c.actorUserId()));
        }
        if (hasText(c.actorUsername())) {
            list.add(cb.equal(cb.lower(root.get("actorUsername")), c.actorUsername().trim().toLowerCase(Locale.ROOT)));
        }
        if (c.module() != null) {
            list.add(cb.equal(root.get("module"), c.module()));
        }
        if (c.action() != null) {
            list.add(cb.equal(root.get("action"), c.action()));
        }
        if (hasText(c.objectType())) {
            list.add(cb.equal(root.get("objectType"), c.objectType().trim()));
        }
        if (hasText(c.objectId())) {
            list.add(cb.equal(root.get("objectId"), c.objectId().trim()));
        }
        if (c.result() != null) {
            list.add(cb.equal(root.get("result"), c.result()));
        }
        if (c.from() != null) {
            list.add(cb.greaterThanOrEqualTo(root.get("occurredAt"), c.from()));
        }
        if (c.to() != null) {
            list.add(cb.lessThan(root.get("occurredAt"), c.to()));
        }
        if (hasText(c.q())) {
            String pattern = "%" + escapeLike(c.q().trim().toLowerCase(Locale.ROOT)) + "%";
            list.add(cb.or(
                    cb.like(cb.lower(root.get("summary")), pattern, '\\'),
                    cb.like(cb.lower(root.get("actorUsername")), pattern, '\\'),
                    cb.like(cb.lower(root.get("objectId")), pattern, '\\'),
                    cb.like(cb.lower(root.get("objectType")), pattern, '\\')));
        }
        return list;
    }

    private static boolean hasText(String s) {
        return s != null && !s.isBlank();
    }

    private static String escapeLike(String s) {
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }
}
