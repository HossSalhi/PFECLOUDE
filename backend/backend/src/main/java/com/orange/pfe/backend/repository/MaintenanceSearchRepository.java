package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.dto.maintenance.MaintenanceFilter;
import com.orange.pfe.backend.model.maintenance.Maintenance;
import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import jakarta.persistence.criteria.CriteriaBuilder;
import jakarta.persistence.criteria.CriteriaQuery;
import jakarta.persistence.criteria.Predicate;
import jakarta.persistence.criteria.Root;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Recherche filtrée des maintenances (liste et calendrier). Lecture seule. */
@Repository
public class MaintenanceSearchRepository {

    @PersistenceContext
    private EntityManager em;

    @Transactional(readOnly = true)
    public Page<Maintenance> search(MaintenanceFilter filter, Pageable pageable) {
        CriteriaBuilder cb = em.getCriteriaBuilder();

        CriteriaQuery<Maintenance> cq = cb.createQuery(Maintenance.class);
        Root<Maintenance> root = cq.from(Maintenance.class);
        cq.select(root)
                .where(predicates(cb, root, filter).toArray(new Predicate[0]))
                .orderBy(cb.desc(root.get("startAt")), cb.desc(root.get("id")));
        List<Maintenance> content = em.createQuery(cq)
                .setFirstResult((int) pageable.getOffset())
                .setMaxResults(pageable.getPageSize())
                .getResultList();

        CriteriaQuery<Long> count = cb.createQuery(Long.class);
        Root<Maintenance> countRoot = count.from(Maintenance.class);
        count.select(cb.count(countRoot)).where(predicates(cb, countRoot, filter).toArray(new Predicate[0]));
        long total = em.createQuery(count).getSingleResult();

        return new PageImpl<>(content, pageable, total);
    }

    /** Maintenances dont la période chevauche l'intervalle (calendrier), de la plus proche à la plus lointaine. */
    @Transactional(readOnly = true)
    public List<Maintenance> findInRange(MaintenanceFilter filter, int maxRows) {
        CriteriaBuilder cb = em.getCriteriaBuilder();
        CriteriaQuery<Maintenance> cq = cb.createQuery(Maintenance.class);
        Root<Maintenance> root = cq.from(Maintenance.class);
        cq.select(root)
                .where(predicates(cb, root, filter).toArray(new Predicate[0]))
                .orderBy(cb.asc(root.get("startAt")), cb.asc(root.get("id")));
        return em.createQuery(cq).setMaxResults(maxRows).getResultList();
    }

    private List<Predicate> predicates(CriteriaBuilder cb, Root<Maintenance> root, MaintenanceFilter f) {
        List<Predicate> list = new ArrayList<>();
        if (f.status() != null) {
            list.add(cb.equal(root.get("status"), f.status()));
        }
        if (f.resourceType() != null) {
            list.add(cb.equal(root.get("resourceType"), f.resourceType()));
        }
        if (f.resourceName() != null && !f.resourceName().isBlank()) {
            String pattern = "%" + escapeLike(f.resourceName().trim().toLowerCase(Locale.ROOT)) + "%";
            list.add(cb.like(cb.lower(root.<String>get("resourceName")), pattern, '\\'));
        }
        if (f.responsibleId() != null) {
            list.add(cb.equal(root.get("responsible").get("id"), f.responsibleId()));
        }
        if (f.from() != null) {
            list.add(cb.greaterThan(root.<LocalDateTime>get("endAt"), f.from()));
        }
        if (f.to() != null) {
            list.add(cb.lessThan(root.<LocalDateTime>get("startAt"), f.to()));
        }
        if (f.text() != null && !f.text().isBlank()) {
            String pattern = "%" + escapeLike(f.text().trim().toLowerCase(Locale.ROOT)) + "%";
            list.add(cb.or(
                    cb.like(cb.lower(root.<String>get("title")), pattern, '\\'),
                    cb.like(cb.lower(root.<String>get("resourceName")), pattern, '\\')));
        }
        return list;
    }

    private static String escapeLike(String s) {
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }
}
