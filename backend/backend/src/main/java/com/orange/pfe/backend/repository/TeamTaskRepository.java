package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.team.TaskSourceEvent;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.TeamTask;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface TeamTaskRepository extends JpaRepository<TeamTask, Long> {

    long countByTeamId(Long teamId);

    /** Tâches automatiques du routage incident → équipe (idempotence : une par incident et par étape). */
    boolean existsBySourceIncidentIdAndSourceEvent(Long sourceIncidentId, TaskSourceEvent sourceEvent);

    Optional<TeamTask> findBySourceIncidentIdAndSourceEvent(Long sourceIncidentId, TaskSourceEvent sourceEvent);

    List<TeamTask> findBySourceIncidentIdOrderByIdAsc(Long sourceIncidentId);

    long countByTeamIdAndAssigneeIdAndStatusIn(Long teamId, Long assigneeId, Collection<TaskStatus> statuses);

    @Query("select t.team.id, count(t) from TeamTask t where t.team.id in :teamIds and t.status in :statuses group by t.team.id")
    List<Object[]> countByTeamIdsAndStatuses(@Param("teamIds") Collection<Long> teamIds,
                                             @Param("statuses") Collection<TaskStatus> statuses);
}
