package com.orange.pfe.backend.service.aiassist;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Fact;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.MemberLoad;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Narrative;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.ReportSummaryResponse;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TeamSummaryAiResponse;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportDetail;
import com.orange.pfe.backend.dto.team.TeamResponses.MemberTaskStats;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamSummaryResponse;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.report.ReportService;
import com.orange.pfe.backend.service.team.TeamService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;

/**
 * (b) Résumé IA : d'un rapport déjà généré (administrateurs) ou de l'activité d'UNE équipe (administrateurs ou chef actif de
 * cette équipe). Le prompt ne contient que des agrégats déjà calculés ; les personnes y sont anonymisées.
 */
@Service
public class AiSummaryService {

    private final AiAssistGuard guard;
    private final ReportService reports;
    private final TeamService teamService;
    private final ObjectMapper mapper = new ObjectMapper();

    public AiSummaryService(AiAssistGuard guard, ReportService reports, TeamService teamService) {
        this.guard = guard;
        this.reports = reports;
        this.teamService = teamService;
    }

    /** Rapport global (non filtré par équipe) : réservé aux administrateurs. */
    @Transactional(readOnly = true)
    public ReportSummaryResponse reportSummary(AppUser actor, Long reportId) {
        guard.authorize(actor, null);          // sans équipe : seul un administrateur passe
        if (guard.disabled(AiFeature.REPORTS_SUMMARY)) {
            return new ReportSummaryResponse(reportId, null, null, List.of(), guard.disabledNarrative());
        }
        ReportDetail detail = reports.get(actor, reportId);
        JsonNode content = parse(detail.content());
        String title = text(content, "title", detail.report().title());
        String period = text(content, "periodFrom", "") + " → " + text(content, "periodTo", "");

        List<Fact> facts = new ArrayList<>();
        StringBuilder prompt = new StringBuilder("Rapport « ").append(title).append(" » (période : ").append(period).append(").\nIndicateurs :\n");
        for (JsonNode m : content.path("summary")) {
            String label = m.path("label").asText("");
            String value = number(m.path("value"));
            String unit = m.path("unit").asText("");
            String shown = unit.isBlank() ? value : value + " " + unit;
            String tone = "neutral";
            String trend = "";
            if (m.hasNonNull("previous")) {
                double delta = m.path("delta").asDouble(0);
                trend = " (période précédente : " + number(m.path("previous")) + ", variation : "
                        + (m.hasNonNull("deltaPercent") ? number(m.path("deltaPercent")) + " %" : number(m.path("delta"))) + ")";
                tone = delta == 0 ? "neutral" : "warn";
            }
            facts.add(Fact.of(label, shown, tone));
            prompt.append("- ").append(label).append(" : ").append(shown).append(trend).append('\n');
        }
        for (JsonNode s : content.path("sections")) {
            prompt.append("Tableau « ").append(s.path("title").asText("")).append(" » : ").append(s.path("rows").size()).append(" ligne(s).\n");
        }
        prompt.append("\nRédige un résumé exécutif de 3 à 4 phrases : situation globale, points d'attention chiffrés, évolution "
                + "par rapport à la période précédente si elle est fournie. Aucune action à exécuter.");
        Narrative n = guard.narrate(AiFeature.REPORTS_SUMMARY, AiAssistGuard.SYSTEM, prompt.toString(), null);
        return new ReportSummaryResponse(reportId, title, period, facts, n);
    }

    /** Activité d'une équipe : tâches ouvertes, retards, avancement et charge par membre. */
    @Transactional(readOnly = true)
    public TeamSummaryAiResponse teamSummary(AppUser actor, Long teamId) {
        Team team = guard.authorize(actor, teamId);
        if (team == null) {
            throw new IllegalArgumentException("L'équipe est obligatoire");
        }
        if (guard.disabled(AiFeature.REPORTS_SUMMARY)) {
            return new TeamSummaryAiResponse(team.getId(), team.getName(), List.of(), List.of(), guard.disabledNarrative());
        }
        TeamSummaryResponse s = teamService.summary(actor, teamId);
        List<Fact> facts = new ArrayList<>();
        long open = s.tasks().byStatus().entrySet().stream().filter(e -> isOpen(e.getKey())).mapToLong(e -> e.getValue()).sum();
        facts.add(Fact.of("Tâches", s.tasks().total()));
        facts.add(Fact.of("Ouvertes", open));
        facts.add(Fact.of("En retard", s.tasks().overdue(), s.tasks().overdue() > 0 ? "bad" : "good"));
        facts.add(Fact.of("Avancement", Math.round(s.tasks().completionRate()) + " %",
                s.tasks().completionRate() >= 70 ? "good" : s.tasks().completionRate() >= 40 ? "warn" : "bad"));

        AiNames names = new AiNames();
        List<MemberLoad> members = new ArrayList<>();
        StringBuilder prompt = new StringBuilder("Activité de l'équipe « ").append(team.getName()).append(" » :\n");
        prompt.append("- tâches : ").append(s.tasks().total()).append(", ouvertes : ").append(open)
                .append(", en retard : ").append(s.tasks().overdue())
                .append(", avancement : ").append(Math.round(s.tasks().completionRate())).append(" %\nCharge par membre :\n");
        for (MemberTaskStats m : s.members()) {
            members.add(new MemberLoad(m.userId(), m.displayName(), m.total(), m.open(), m.done(), m.overdue()));
            prompt.append("- ").append(names.alias(m.displayName())).append(" : ").append(m.total()).append(" tâche(s), ")
                    .append(m.open()).append(" ouverte(s), ").append(m.done()).append(" terminée(s), ")
                    .append(m.overdue()).append(" en retard\n");
        }
        prompt.append("\nRédige une synthèse de 3 à 4 phrases : état d'avancement, répartition de la charge, points d'attention. "
                + "Aucune action à exécuter.");
        Narrative n = guard.narrate(AiFeature.REPORTS_SUMMARY, AiAssistGuard.SYSTEM, prompt.toString(), names);
        return new TeamSummaryAiResponse(team.getId(), team.getName(), facts, members, n);
    }

    private static boolean isOpen(String status) {
        return "TODO".equals(status) || "IN_PROGRESS".equals(status) || "ON_HOLD".equals(status);
    }

    private JsonNode parse(String json) {
        try {
            return json == null || json.isBlank() ? mapper.createObjectNode() : mapper.readTree(json);
        } catch (Exception e) {
            return mapper.createObjectNode();
        }
    }

    private static String text(JsonNode n, String field, String fallback) {
        String v = n.path(field).asText("");
        return v.isBlank() ? (fallback == null ? "" : fallback) : v;
    }

    private static String number(JsonNode v) {
        if (v == null || v.isMissingNode() || v.isNull()) {
            return "—";
        }
        double d = v.asDouble();
        return d == Math.rint(d) ? String.valueOf((long) d) : String.valueOf(Math.round(d * 10.0) / 10.0);
    }
}
