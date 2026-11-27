package com.orange.pfe.backend.service.aiassist.telemetry;

import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.telemetry.TelemetryAnalysisRun;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.TelemetryAnalysisRunRepository;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Enregistrement TRANSACTIONNEL d'une analyse : la ligne d'historique et la trace d'audit sont écrites ensemble, en quelques
 * millisecondes, APRÈS les appels externes (Prometheus, Loki, Groq). Séparé du service d'analyse pour qu'aucune transaction ni
 * connexion de base ne reste ouverte pendant ces appels (un bean distinct : l'annotation ne s'appliquerait pas à un appel interne).
 */
@Service
public class TelemetryRunWriter {

    private final TelemetryAnalysisRunRepository runs;
    private final AuditTrailService audit;

    public TelemetryRunWriter(TelemetryAnalysisRunRepository runs, AuditTrailService audit) {
        this.runs = runs;
        this.audit = audit;
    }

    @Transactional
    public TelemetryAnalysisRun persist(AppUser actor, TelemetryAnalysisRun run, String verdictLabel) {
        TelemetryAnalysisRun saved = runs.save(run);
        audit.record(AuditEvent.of(AuditModule.AUDIT, AuditAction.GENERATE).actor(actor)
                .object("TelemetryAnalysis", saved.getId())
                .summary("Analyse IA des logs et métriques #" + saved.getId() + " (" + saved.getScope() + ", " + saved.getWindowMinutes()
                        + " min) : " + verdictLabel + ", " + saved.getAnomalyCount() + " constat(s)"));
        return saved;
    }
}
