package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.human.HumanTask;
import com.orange.pfe.backend.model.human.TaskDecision;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface HumanTaskRepository extends JpaRepository<HumanTask, Long> {

    List<HumanTask> findByDecision(TaskDecision decision);

    long countByDecision(TaskDecision decision);

    boolean existsByWorkflowInstanceIdAndDecision(Long workflowInstanceId, TaskDecision decision);

    List<HumanTask> findByWorkflowInstanceIdAndDecision(Long workflowInstanceId, TaskDecision decision);
}
