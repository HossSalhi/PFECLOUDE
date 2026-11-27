package com.orange.pfe.backend.service.aiassist.telemetry;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Fact;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.FindingView;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Narrative;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.QualityView;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TelemetryAnalysisRequest;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TelemetryAnalysisView;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TelemetryRunSummary;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.telemetry.TelemetryAnalysisRun;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.TeamRepository;
import com.orange.pfe.backend.repository.TelemetryAnalysisRunRepository;
import com.orange.pfe.backend.service.aiassist.AiAssistGuard;
import com.orange.pfe.backend.service.aiassist.AiFeature;
import com.orange.pfe.backend.service.aiassist.AiNames;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Detection;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.EntityCoverage;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Finding;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Trace;
import com.orange.pfe.backend.service.org.OrgAccessService;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDateTime;
import java.util.EnumSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * (e) Analyse IA des logs et métriques, TRAÇABLE : la détection d'anomalies est faite par des règles déterministes
 * ({@link TelemetryDetector}) ; l'IA ne rédige, à la demande, que la synthèse des constats déjà chiffrés. Chaque analyse est
 * enregistrée en base (ajout seul) et inscrite à l'audit-trail.
 *
 * <p>Transactions : {@link #analyse} n'est PAS transactionnelle. Les appels externes (Prometheus, Loki, Groq) se font sans
 * transaction ni connexion de base retenue ; seul l'enregistrement final ({@link TelemetryRunWriter}) ouvre une transaction,
 * de quelques millisecondes. Le nombre d'analyses simultanées est borné en base ({@link TelemetryAnalysisGate}), donc pour
 * l'ensemble des réplicas du backend.</p>
 *
 * <p>Droits : un administrateur analyse tout ou un périmètre ; le chef ACTIF d'une équipe n'analyse que le périmètre de la
 * spécialité de SON équipe (une équipe sans spécialité couvre tous les types) et ne relit que l'historique de ses équipes.</p>
 */
@Service
public class TelemetryAnalysisService {

    static final int MIN_MINUTES = 15;
    static final int MAX_MINUTES = 7 * 24 * 60;
    static final int MAX_PAGE = 50;

    private final AiAssistGuard guard;
    private final OrgAccessService access;
    private final TelemetryDetector detector;
    private final TelemetryAnalysisRunRepository runs;
    private final TeamRepository teams;
    private final TelemetryAnalysisGate gate;
    private final TelemetryRunWriter writer;
    private final ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);

    public TelemetryAnalysisService(AiAssistGuard guard, OrgAccessService access, TelemetryDetector detector,
                                    TelemetryAnalysisRunRepository runs, TeamRepository teams, TelemetryAnalysisGate gate,
                                    TelemetryRunWriter writer) {
        this.guard = guard;
        this.access = access;
        this.detector = detector;
        this.runs = runs;
        this.teams = teams;
        this.gate = gate;
        this.writer = writer;
    }

    /** Lance une analyse, l'enregistre et la renvoie. Déclenchée par un bouton : jamais automatique. Aucune transaction ouverte. */
    public TelemetryAnalysisView analyse(AppUser actor, TelemetryAnalysisRequest req) {
        TelemetryAnalysisRequest r = req == null ? new TelemetryAnalysisRequest(null, null, null) : req;
        Team team = guard.authorize(actor, r.teamId());                    // lectures courtes, chacune dans sa propre transaction
        if (guard.disabled(AiFeature.TELEMETRY_ANALYSIS)) {
            throw new IllegalStateException("L'analyse IA des logs et métriques est désactivée");
        }
        int minutes = Math.max(MIN_MINUTES, Math.min(MAX_MINUTES, r.minutes() == null ? 60 : r.minutes()));
        String scopeName = scopeName(team, r.scope());
        Set<ResourceScope> scopes = scopesOf(scopeName);

        String token = gate.acquire(actor.getId());                        // refus 429 : une analyse par utilisateur, plafond global
        try {
            long startedAt = System.nanoTime();
            Detection detection = detector.detect(Instant.now(), minutes, scopes);          // appels externes : aucune transaction
            AiNames names = new AiNames();
            String prompt = TelemetryPromptBuilder.build(detection, scopeName, minutes, names);
            Narrative narrative = guard.narrate(AiFeature.TELEMETRY_ANALYSIS, AiAssistGuard.SYSTEM, prompt, names);      // Groq : idem
            long durationMs = (System.nanoTime() - startedAt) / 1_000_000L;

            TelemetryAnalysisRun saved = writer.persist(actor, new TelemetryAnalysisRun(LocalDateTime.now(), actor.getId(), actor.getUsername(),
                    team == null ? null : team.getId(), team == null ? null : team.getName(), scopeName, minutes,
                    detection.windowStart(), detection.windowEnd(), detection.verdict(), detection.dataQuality(),
                    detection.findings().size(), (int) detection.count("HIGH"),
                    json(detection.findings().stream().map(TelemetryAnalysisService::view).toList()),
                    json(detection.quality().stream().map(q -> new QualityView(q.source(), q.available(), q.coverage(), q.note())).toList()),
                    json(detection.trace()), narrative.status(), narrative.text(), truncate(narrative.message(), 300), narrative.model(), durationMs),
                    verdictLabel(detection.verdict()));
            return toView(saved);
        } finally {
            gate.release(actor.getId(), token);
        }
    }

    /** Historique, plus récent d'abord : tout pour un administrateur, seulement SES équipes pour un chef. */
    @Transactional(readOnly = true)
    public Page<TelemetryRunSummary> history(AppUser actor, Long teamId, int page, int size) {
        access.requireUser(actor);
        PageRequest pr = PageRequest.of(Math.max(0, page), Math.max(1, Math.min(MAX_PAGE, size)));
        Page<TelemetryAnalysisRun> result;
        if (access.isAdminRole(actor)) {
            result = teamId == null ? runs.findAllByOrderByRequestedAtDesc(pr) : runs.findByTeamIdOrderByRequestedAtDesc(teamId, pr);
        } else {
            List<Long> led = teams.findActiveByLeadIds(List.of(actor.getId())).stream().map(Team::getId).toList();
            if (led.isEmpty() || (teamId != null && !led.contains(teamId))) {
                throw new AccessDeniedException("Historique réservé aux administrateurs et aux chefs de l'équipe concernée");
            }
            result = teamId == null ? runs.findByTeamIdInOrderByRequestedAtDesc(led, pr) : runs.findByTeamIdOrderByRequestedAtDesc(teamId, pr);
        }
        return result.map(TelemetryAnalysisService::summary);
    }

    /** Relit une analyse enregistrée, à l'identique. Un chef ne relit que celles de ses équipes. */
    @Transactional(readOnly = true)
    public TelemetryAnalysisView get(AppUser actor, Long id) {
        access.requireUser(actor);
        TelemetryAnalysisRun run = runs.findById(id).orElseThrow(() -> new ResourceNotFoundException("Analyse introuvable : " + id));
        if (!access.isAdminRole(actor)) {
            Team team = run.getTeamId() == null ? null : teams.findById(run.getTeamId()).orElse(null);
            access.requireAiAssistFor(actor, team);
        }
        return toView(run);
    }

    // ── Périmètre ────────────────────────────────────────────────────────────────────────────────────────────────

    /** Périmètre effectif : spécialité de l'équipe pour un chef, ou le choix explicite (défaut ALL) pour un administrateur. */
    static String scopeName(Team team, String requested) {
        if (team != null && team.getSpecialty() != null) {
            return team.getSpecialty().name();
        }
        if (team != null) {
            return "ALL";                                           // équipe sans spécialité : tous les types
        }
        if (requested == null || requested.isBlank() || "ALL".equalsIgnoreCase(requested.trim())) {
            return "ALL";
        }
        try {
            return ResourceScope.valueOf(requested.trim().toUpperCase(Locale.ROOT)).name();
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("Périmètre invalide : " + requested);
        }
    }

    static Set<ResourceScope> scopesOf(String scopeName) {
        return "ALL".equals(scopeName) ? null : EnumSet.of(ResourceScope.valueOf(scopeName));
    }

    // ── Conversion ───────────────────────────────────────────────────────────────────────────────────────────────

    /** Indicateurs affichés : fonction des seules données enregistrées, donc identiques à la création et à la relecture. */
    static List<Fact> facts(String verdict, String dataQuality, List<FindingView> findings, Trace trace) {
        long high = findings.stream().filter(f -> "HIGH".equals(f.severity())).count();
        long medium = findings.stream().filter(f -> "MEDIUM".equals(f.severity())).count();
        long entities = trace == null ? 0 : trace.coverage().stream().map(EntityCoverage::entity).distinct().count();
        String verdictTone = switch (verdict) {
            case TelemetryModels.NO_ANOMALY -> "good";
            case TelemetryModels.ANOMALIES -> high > 0 ? "bad" : "warn";
            default -> "warn";
        };
        return List.of(
                Fact.of("Verdict", verdictLabel(verdict), verdictTone),
                Fact.of("Qualité des données", qualityLabel(dataQuality),
                        TelemetryModels.DATA_COMPLETE.equals(dataQuality) ? "good" : TelemetryModels.DATA_PARTIAL.equals(dataQuality) ? "warn" : "bad"),
                Fact.of("Anomalies", findings.size(), findings.isEmpty() ? "good" : "warn"),
                Fact.of("Gravité haute", high, high > 0 ? "bad" : "good"),
                Fact.of("Gravité moyenne", medium, medium > 0 ? "warn" : "good"),
                Fact.of("Entités suivies", entities));
    }

    private static FindingView view(Finding f) {
        return new FindingView(f.scope(), f.entity(), f.signal(), f.rule(), f.severity(), f.start(), f.end(), f.observed(),
                f.baseline(), f.unit(), f.detail(), f.evidence(), f.queryRef());
    }

    private TelemetryAnalysisView toView(TelemetryAnalysisRun r) {
        List<FindingView> findings = read(r.getFindingsJson(), new TypeReference<>() { });
        List<QualityView> quality = read(r.getQualityJson(), new TypeReference<>() { });
        Trace trace = read(r.getTraceJson(), new TypeReference<>() { });
        Narrative narrative = new Narrative(r.getNarrativeStatus(), r.getNarrativeMessage(), r.getNarrativeText(), r.getModel(), null);
        return new TelemetryAnalysisView(r.getId(), r.getRequestedAt(), r.getRequestedByUsername(), r.getTeamId(), r.getTeamName(),
                r.getScope(), r.getWindowMinutes(), r.getWindowStart(), r.getWindowEnd(), r.getVerdict(), r.getDataQuality(),
                r.getAnomalyCount(), r.getHighCount(), r.getDurationMs(), facts(r.getVerdict(), r.getDataQuality(), findings, trace),
                findings, quality, trace, narrative);
    }

    private static TelemetryRunSummary summary(TelemetryAnalysisRun r) {
        return new TelemetryRunSummary(r.getId(), r.getRequestedAt(), r.getRequestedByUsername(), r.getTeamId(), r.getTeamName(),
                r.getScope(), r.getWindowMinutes(), r.getVerdict(), r.getDataQuality(), r.getAnomalyCount(), r.getHighCount(),
                r.getNarrativeStatus());
    }

    private String json(Object o) {
        try {
            return mapper.writeValueAsString(o);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Sérialisation de l'analyse impossible", e);
        }
    }

    private <T> T read(String json, TypeReference<T> type) {
        try {
            return mapper.readValue(json, type);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Analyse enregistrée illisible", e);
        }
    }

    static String verdictLabel(String verdict) {
        return switch (verdict) {
            case TelemetryModels.NO_ANOMALY -> "Aucune anomalie";
            case TelemetryModels.ANOMALIES -> "Anomalies détectées";
            case TelemetryModels.PARTIAL_COVERAGE -> "Couverture partielle";
            default -> "Données insuffisantes";
        };
    }

    static String qualityLabel(String q) {
        return TelemetryModels.DATA_COMPLETE.equals(q) ? "Complète" : TelemetryModels.DATA_PARTIAL.equals(q) ? "Partielle" : "Insuffisante";
    }

    private static String truncate(String s, int max) {
        return s == null ? null : s.length() <= max ? s : s.substring(0, max);
    }
}
