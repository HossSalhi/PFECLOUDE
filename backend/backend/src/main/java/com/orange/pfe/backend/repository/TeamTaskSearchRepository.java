package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.dto.team.TaskFilter;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.TeamTask;
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

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Recherche filtrée des tâches (API Criteria : seuls les filtres renseignés produisent un prédicat). */
@Repository
public class TeamTaskSearchRepository {

    @PersistenceContext
    private EntityManager em;

    @Transactional(readOnly = true)
    public Page<TeamTask> search(TaskFilter filter, Pageable pageable) {
        CriteriaBuilder cb = em.getCriteriaBuilder();

        CriteriaQuery<TeamTask> cq = cb.createQuery(TeamTask.class);
        Root<TeamTask> root = cq.from(TeamTask.class);
        cq.select(root)
                .where(predicates(cb, root, filter).toArray(new Predicate[0]))
                .orderBy(cb.desc(root.get("updatedAt")), cb.desc(root.get("id")));
        List<TeamTask> content = em.createQuery(cq)
                .setFirstResult((int) pageable.getOffset())
                .setMaxResults(pageable.getPageSize())
                .getResultList();

        CriteriaQuery<Long> count = cb.createQuery(Long.class);
        Root<TeamTask> countRoot = count.from(TeamTask.class);
        count.select(cb.count(countRoot)).where(predicates(cb, countRoot, filter).toArray(new Predicate[0]));
        long total = em.createQuery(count).getSingleResult();

        return new PageImpl<>(content, pageable, total);
    }

    @Transactional(readOnly = true)
    public List<TeamTask> findAllMatching(TaskFilter filter, int maxRows) {
        CriteriaBuilder cb = em.getCriteriaBuilder();
        CriteriaQuery<TeamTask> cq = cb.createQuery(TeamTask.class);
        Root<TeamTask> root = cq.from(TeamTask.class);
        cq.select(root)
                .where(predicates(cb, root, filter).toArray(new Predicate[0]))
                .orderBy(cb.desc(root.get("updatedAt")), cb.desc(root.get("id")));
        return em.createQuery(cq).setMaxResults(maxRows).getResultList();
    }

    private List<Predicate> predicates(CriteriaBuilder cb, Root<TeamTask> root, TaskFilter f) {
        List<Predicate> list = new ArrayList<>();
        if (f.teamId() != null) {
            list.add(cb.equal(root.get("team").get("id"), f.teamId()));
        }
        if (f.assigneeId() != null) {
            list.add(cb.equal(root.get("assignee").get("id"), f.assigneeId()));
        }
        if (f.status() != null) {
            list.add(cb.equal(root.get("status"), f.status()));
        }
        if (f.priority() != null) {
            list.add(cb.equal(root.get("priority"), f.priority()));
        }
        if (f.overdueOnly()) {
            list.add(cb.and(
                    cb.lessThan(root.<LocalDate>get("dueDate"), LocalDate.now()),
                    root.get("status").in(TaskStatus.OPEN)));
        }
        if (f.text() != null && !f.text().isBlank()) {
            String pattern = "%" + escapeLike(f.text().trim().toLowerCase(Locale.ROOT)) + "%";
            list.add(cb.like(cb.lower(root.<String>get("title")), pattern, '\\'));
        }
        if (f.visibleToUserId() != null) {
            List<Predicate> visible = new ArrayList<>();
            visible.add(cb.equal(root.get("assignee").get("id"), f.visibleToUserId()));
            visible.add(cb.equal(root.get("createdBy").get("id"), f.visibleToUserId()));
            if (f.visibleTeamIds() != null && !f.visibleTeamIds().isEmpty()) {
                visible.add(root.get("team").get("id").in(f.visibleTeamIds()));
            }
            list.add(cb.or(visible.toArray(new Predicate[0])));
        }
        return list;
    }

    private static String escapeLike(String s) {
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }
}
