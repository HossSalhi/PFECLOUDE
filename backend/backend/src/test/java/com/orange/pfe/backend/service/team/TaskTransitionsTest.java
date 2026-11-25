package com.orange.pfe.backend.service.team;

import com.orange.pfe.backend.model.team.TaskStatus;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/** Matrice des transitions de statut d'une tâche : À FAIRE, EN COURS, EN ATTENTE, TERMINÉE, ANNULÉE. */
class TaskTransitionsTest {

    @Test
    void openStatusesCanMoveFreelyAmongThemselvesAndBeClosed() {
        for (TaskStatus from : TaskStatus.OPEN) {
            for (TaskStatus to : TaskStatus.values()) {
                if (from != to) {
                    assertThat(TeamTaskService.transitionAllowed(from, to)).as(from + " -> " + to).isTrue();
                }
            }
        }
    }

    @Test
    void closedStatusesCanOnlyBeReopenedOneWay() {
        assertThat(TeamTaskService.transitionAllowed(TaskStatus.DONE, TaskStatus.IN_PROGRESS)).isTrue();
        assertThat(TeamTaskService.transitionAllowed(TaskStatus.DONE, TaskStatus.TODO)).isFalse();
        assertThat(TeamTaskService.transitionAllowed(TaskStatus.DONE, TaskStatus.CANCELLED)).isFalse();
        assertThat(TeamTaskService.transitionAllowed(TaskStatus.CANCELLED, TaskStatus.TODO)).isTrue();
        assertThat(TeamTaskService.transitionAllowed(TaskStatus.CANCELLED, TaskStatus.DONE)).isFalse();
        assertThat(TeamTaskService.transitionAllowed(TaskStatus.CANCELLED, TaskStatus.IN_PROGRESS)).isFalse();
    }

    @Test
    void openAndClosedPartitionTheStatuses() {
        assertThat(TaskStatus.OPEN).containsExactlyInAnyOrder(TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.ON_HOLD);
        assertThat(TaskStatus.DONE.isClosed()).isTrue();
        assertThat(TaskStatus.CANCELLED.isClosed()).isTrue();
        assertThat(TaskStatus.ON_HOLD.isOpen()).isTrue();
    }
}
