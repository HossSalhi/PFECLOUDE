package com.orange.pfe.backend.service.report;

import com.orange.pfe.backend.model.report.ReportSchedule;
import com.orange.pfe.backend.repository.ReportScheduleRepository;
import com.orange.pfe.backend.service.LeaderElectionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

/**
 * Génération des rapports périodiques. Fiabilité, sans doublon :
 * <ul>
 *   <li>seul le LEADER du Backend exécute la tâche (même mécanisme que les autres planificateurs automatiques) ;</li>
 *   <li>pour chaque planning, seule la dernière période COMPLÈTE est produite, et uniquement si elle ne l'est pas
 *       déjà (contrôle d'existence + contrainte d'unicité en base en cas de course entre deux réplicas) ;</li>
 *   <li>un échec ne bloque pas les autres plannings et sera retenté au passage suivant (rien n'est enregistré en cas
 *       d'échec).</li>
 * </ul>
 * Cadence réglable : {@code helios.reports.scheduler-delay-ms} (défaut 10 min).
 */
@Component
public class ReportScheduler {

    private static final Logger log = LoggerFactory.getLogger(ReportScheduler.class);

    private final ReportService reportService;
    private final ReportScheduleRepository scheduleRepository;
    private final LeaderElectionService leaderElectionService;

    public ReportScheduler(ReportService reportService,
                           ReportScheduleRepository scheduleRepository,
                           LeaderElectionService leaderElectionService) {
        this.reportService = reportService;
        this.scheduleRepository = scheduleRepository;
        this.leaderElectionService = leaderElectionService;
    }

    @Scheduled(fixedDelayString = "${helios.reports.scheduler-delay-ms:600000}",
            initialDelayString = "${helios.reports.scheduler-initial-delay-ms:120000}")
    public void runDueSchedules() {
        if (!leaderElectionService.isLeader()) {
            return;
        }
        runOnce(LocalDateTime.now());
    }

    /** Un passage (exposé pour les tests) : retourne le nombre de rapports générés. */
    int runOnce(LocalDateTime now) {
        int generated = 0;
        for (ReportSchedule schedule : scheduleRepository.findByEnabledTrueOrderByIdAsc()) {
            ReportPeriod period = ReportPeriod.lastCompleted(schedule.getFrequency(), now);
            try {
                if (reportService.generateScheduled(schedule.getId(), period)) {
                    generated++;
                }
            } catch (DataIntegrityViolationException duplicate) {
                log.debug("Rapport périodique déjà produit par une autre instance (planning {}, {} → {})",
                        schedule.getId(), period.from(), period.to());
            } catch (RuntimeException e) {
                log.error("Rapport périodique en échec (planning {}): {}", schedule.getId(), e.toString());
            }
        }
        return generated;
    }
}
