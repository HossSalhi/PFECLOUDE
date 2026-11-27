package com.orange.pfe.backend.service.aiassist;

import com.fasterxml.jackson.databind.JsonNode;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Fact;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.MaintenanceWindowRequest;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.MaintenanceWindowResponse;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Narrative;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.WindowCandidate;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.MaintenanceRepository;
import com.orange.pfe.backend.service.supervision.SupervisionClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * (d) Fenêtre de maintenance recommandée : profil de charge CPU par heure (Prometheus, lecture seule, derniers jours),
 * croisé avec les maintenances déjà planifiées sur la même ressource. Le calcul est DÉTERMINISTE ; l'IA ne formule que la
 * recommandation finale. Aucune maintenance n'est créée : l'humain choisit une fenêtre et la planifie lui-même.
 */
@Service
public class MaintenanceWindowAdvisor {

    static final String LOAD_QL = "avg(100-(avg by(instance)(rate(node_cpu_seconds_total{mode=\"idle\"}[5m]))*100))";
    static final int HISTORY_DAYS = 7;

    private final AiAssistGuard guard;
    private final SupervisionClient prometheus;
    private final MaintenanceRepository maintenances;

    public MaintenanceWindowAdvisor(AiAssistGuard guard, SupervisionClient prometheus, MaintenanceRepository maintenances) {
        this.guard = guard;
        this.prometheus = prometheus;
        this.maintenances = maintenances;
    }

    @Transactional(readOnly = true)
    public MaintenanceWindowResponse recommend(AppUser actor, MaintenanceWindowRequest req) {
        if (req == null) {
            throw new IllegalArgumentException("Requête vide");
        }
        Team team = guard.authorize(actor, req.teamId());
        MaintenanceResourceType type = parseType(req.resourceType());
        String resource = req.resourceName() == null ? "" : req.resourceName().trim();
        if (resource.isEmpty()) {
            throw new IllegalArgumentException("La ressource est obligatoire");
        }
        if (team != null && team.getSpecialty() != null && team.getSpecialty() != ResourceScope.fromMaintenance(type)) {
            throw new IllegalArgumentException("Cette ressource n'est pas dans le périmètre de l'équipe « " + team.getName() + " »");
        }
        Long teamId = team == null ? null : team.getId();
        if (guard.disabled(AiFeature.MAINTENANCE_WINDOW)) {
            return new MaintenanceWindowResponse(teamId, false, List.of(), List.of(), guard.disabledNarrative());
        }
        int duration = req.durationMinutes() == null ? 120 : Math.max(30, Math.min(720, req.durationMinutes()));
        int horizon = req.horizonDays() == null ? 14 : Math.max(1, Math.min(30, req.horizonDays()));
        int hours = (int) Math.ceil(duration / 60.0);

        double[] profile = loadProfile();
        boolean metrics = profile != null;
        if (profile == null) {
            profile = offPeakDefault();
        }
        List<WindowCandidate> windows = new ArrayList<>();
        int conflicts = 0;
        LocalDateTime first = LocalDateTime.now().truncatedTo(ChronoUnit.HOURS).plusHours(1);
        List<WindowCandidate> all = new ArrayList<>();
        for (int h = 0; h < horizon * 24; h++) {
            LocalDateTime start = first.plusHours(h);
            LocalDateTime end = start.plusMinutes(duration);
            if (!maintenances.findOverlapping(type, resource, MaintenanceStatus.ACTIVE, start, end).isEmpty()) {
                conflicts++;
                continue;
            }
            all.add(candidate(profile, start, end, hours));
        }
        all.sort(Comparator.comparingDouble(WindowCandidate::averageLoad).thenComparing(WindowCandidate::start));
        for (WindowCandidate c : all) {
            if (windows.size() == 3) {
                break;
            }
            if (windows.stream().noneMatch(w -> c.start().isBefore(w.end()) && c.end().isAfter(w.start()))) {
                windows.add(c);
            }
        }
        List<Fact> facts = new ArrayList<>();
        facts.add(Fact.of("Source de charge", metrics ? "Prometheus (" + HISTORY_DAYS + " j)" : "Indisponible : horaires creux par défaut",
                metrics ? "good" : "warn"));
        facts.add(Fact.of("Durée prévue", duration + " min"));
        facts.add(Fact.of("Horizon", horizon + " jours"));
        facts.add(Fact.of("Créneaux écartés (conflit)", conflicts, conflicts > 0 ? "warn" : "neutral"));
        if (metrics) {
            facts.add(Fact.of("Heure la plus calme", quietestHour(profile) + " h"));
        }
        if (windows.isEmpty()) {
            return new MaintenanceWindowResponse(teamId, metrics, facts, windows, Narrative.none());
        }
        StringBuilder prompt = new StringBuilder("Planification d'une maintenance de ").append(duration).append(" min sur la ressource « ")
                .append(resource).append(" » (").append(type).append(").\n")
                .append(metrics ? "Fenêtres les moins chargées (charge CPU moyenne mesurée sur " + HISTORY_DAYS + " jours) :\n"
                        : "Les métriques sont indisponibles ; fenêtres choisies sur les horaires creux par défaut :\n");
        for (int i = 0; i < windows.size(); i++) {
            WindowCandidate w = windows.get(i);
            prompt.append(i + 1).append(". du ").append(w.start()).append(" au ").append(w.end())
                    .append(", charge moyenne ").append(Math.round(w.averageLoad())).append(" %, pic ")
                    .append(Math.round(w.peakLoad())).append(" %\n");
        }
        prompt.append("Aucune maintenance existante ne chevauche ces fenêtres sur cette ressource.\n\nRédige en 2 à 3 phrases la "
                + "recommandation : quelle fenêtre privilégier et pourquoi, en citant uniquement ces chiffres. C'est une suggestion "
                + "que le responsable valide ; ne planifie rien.");
        Narrative n = guard.narrate(AiFeature.MAINTENANCE_WINDOW, AiAssistGuard.SYSTEM, prompt.toString(), null);
        return new MaintenanceWindowResponse(teamId, metrics, facts, windows, n);
    }

    /** Charge moyenne par heure du jour (0-23), ou {@code null} si Prometheus ne répond pas ou n'a pas assez de points. */
    private double[] loadProfile() {
        try {
            long end = Instant.now().getEpochSecond();
            long start = end - HISTORY_DAYS * 86_400L;
            JsonNode result = prometheus.promRange(LOAD_QL, start, end, 3600);
            double[] sum = new double[24];
            int[] n = new int[24];
            ZoneId zone = ZoneId.systemDefault();
            for (JsonNode series : result) {
                for (JsonNode p : series.path("values")) {
                    double v = p.path(1).asDouble(Double.NaN);
                    if (Double.isNaN(v)) {
                        continue;
                    }
                    int hour = Instant.ofEpochSecond(p.path(0).asLong()).atZone(zone).getHour();
                    sum[hour] += v;
                    n[hour]++;
                }
            }
            double[] avg = new double[24];
            for (int h = 0; h < 24; h++) {
                if (n[h] == 0) {
                    return null;
                }
                avg[h] = sum[h] / n[h];
            }
            return avg;
        } catch (RuntimeException e) {
            return null;
        }
    }

    /** Sans métriques : on suppose la nuit (1 h-5 h) plus calme que la soirée, elle-même plus calme que la journée. */
    static double[] offPeakDefault() {
        double[] p = new double[24];
        for (int h = 0; h < 24; h++) {
            p[h] = (h >= 1 && h <= 5) ? 20 : (h >= 22 || h == 0) ? 35 : 70;
        }
        return p;
    }

    static WindowCandidate candidate(double[] profile, LocalDateTime start, LocalDateTime end, int hours) {
        double sum = 0, peak = 0;
        for (int i = 0; i < hours; i++) {
            double v = profile[start.plusHours(i).getHour()];
            sum += v;
            peak = Math.max(peak, v);
        }
        double avg = sum / hours;
        List<String> notes = new ArrayList<>();
        int hour = start.getHour();
        if (hour >= 1 && hour <= 5) {
            notes.add("Heures creuses de nuit");
        }
        int dow = start.getDayOfWeek().getValue();
        if (dow >= 6) {
            notes.add("Week-end");
        }
        return new WindowCandidate(start, end, Math.round(avg * 10) / 10.0, Math.round(peak * 10) / 10.0,
                (int) Math.max(0, Math.min(100, Math.round(100 - avg))), notes);
    }

    private static int quietestHour(double[] profile) {
        int best = 0;
        for (int h = 1; h < 24; h++) {
            if (profile[h] < profile[best]) {
                best = h;
            }
        }
        return best;
    }

    private static MaintenanceResourceType parseType(String s) {
        try {
            return MaintenanceResourceType.valueOf(s == null ? "" : s.trim().toUpperCase());
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("Type de ressource invalide : " + s);
        }
    }
}
