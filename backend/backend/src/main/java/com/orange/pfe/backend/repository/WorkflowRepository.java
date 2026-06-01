package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface WorkflowRepository extends JpaRepository<WorkflowInstance, Long> {

    long countByStatus(WorkflowStatus status);

    long countByOrchestrationState(WorkflowOrchestrationState orchestrationState);

    List<WorkflowInstance> findByStatusAndOrchestrationState(
            WorkflowStatus status,
            WorkflowOrchestrationState orchestrationState
    );

    @Query("SELECT wf FROM WorkflowInstance wf LEFT JOIN FETCH wf.logs WHERE wf.id = :id")
    Optional<WorkflowInstance> findByIdWithLogs(@Param("id") Long id);

    @Query("SELECT DISTINCT wf FROM WorkflowInstance wf LEFT JOIN FETCH wf.logs")
    List<WorkflowInstance> findAllWithLogs();
}
