package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.maintenance.Maintenance;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.Collection;
import java.util.List;

public interface MaintenanceRepository extends JpaRepository<Maintenance, Long> {

    /**
     * Maintenances actives (planifiées ou en cours) de la même ressource dont la période chevauche [start, end[.
     * L'exclusion de la maintenance en cours de modification est faite par l'appelant.
     */
    @Query("select m from Maintenance m where m.resourceType = :type and m.resourceName = :name "
            + "and m.status in :statuses and m.startAt < :end and m.endAt > :start order by m.startAt")
    List<Maintenance> findOverlapping(@Param("type") MaintenanceResourceType type,
                                      @Param("name") String name,
                                      @Param("statuses") Collection<MaintenanceStatus> statuses,
                                      @Param("start") LocalDateTime start,
                                      @Param("end") LocalDateTime end);

    List<Maintenance> findByStatusOrderByStartAtAsc(MaintenanceStatus status);
}
