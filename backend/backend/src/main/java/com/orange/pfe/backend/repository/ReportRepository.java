package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.report.Report;
import com.orange.pfe.backend.model.report.ReportType;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.Optional;

public interface ReportRepository extends JpaRepository<Report, Long> {

    boolean existsByScheduleIdAndPeriodStartAndPeriodEnd(Long scheduleId, LocalDateTime start, LocalDateTime end);

    Optional<Report> findTopByScheduleIdOrderByGeneratedAtDesc(Long scheduleId);

    Page<Report> findAllByOrderByGeneratedAtDescIdDesc(Pageable pageable);

    Page<Report> findByReportTypeOrderByGeneratedAtDescIdDesc(ReportType type, Pageable pageable);

    /**
     * Détache les rapports d'un planning avant sa suppression (indépendant de ON DELETE SET NULL).
     * {@code flushAutomatically} / {@code clearAutomatically} : la mise à jour de masse contourne le contexte de
     * persistance ; sans cela, des rapports déjà chargés dans la même transaction référenceraient encore le planning supprimé.
     */
    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("update Report r set r.schedule = null where r.schedule.id = :scheduleId")
    int detachSchedule(@Param("scheduleId") Long scheduleId);
}
