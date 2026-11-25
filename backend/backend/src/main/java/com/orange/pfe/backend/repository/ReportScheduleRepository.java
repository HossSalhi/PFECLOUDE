package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.report.ReportFrequency;
import com.orange.pfe.backend.model.report.ReportSchedule;
import com.orange.pfe.backend.model.report.ReportType;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ReportScheduleRepository extends JpaRepository<ReportSchedule, Long> {

    List<ReportSchedule> findByEnabledTrueOrderByIdAsc();

    List<ReportSchedule> findAllByOrderByReportTypeAscFrequencyAsc();

    boolean existsByReportTypeAndFrequency(ReportType type, ReportFrequency frequency);
}
