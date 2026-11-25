package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.maintenance.MaintenanceHistory;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface MaintenanceHistoryRepository extends JpaRepository<MaintenanceHistory, Long> {

    List<MaintenanceHistory> findByMaintenanceIdOrderByChangedAtAscIdAsc(Long maintenanceId);

    void deleteByMaintenanceId(Long maintenanceId);
}
