package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Anomaly;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.AnomalyResponse;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Fact;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Narrative;
import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditLogEntry;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.team.TeamMember;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.AuditLogSearchRepository;
import com.orange.pfe.backend.repository.TeamMemberRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * (c) Anomalies du journal d'audit : détection par RÈGLES DÉTERMINISTES (aucune IA). L'IA n'est sollicitée, à la demande,
 * que pour reformuler en une phrase lisible chaque motif déjà détecté. Un chef d'équipe ne voit que les anomalies
 * concernant les membres (et le responsable) de SON équipe ; un administrateur voit tout.
 */
@Service
public class AuditAnomalyService {

    static final int MAX_ROWS = 5_000;
    static final int MAX_AI_ITEMS = 10;
    private static final Pattern LINE = Pattern.compile("^\\s*(\\d+)\\s*[|:.)\\-]\\s*(.+?)\\s*$");

    /** Une règle : actions concernées, fenêtre glissante, seuil, et seuil de gravité haute. */
    private record Rule(String id, String label, Set<AuditAction> actions, Duration window, int threshold, int high) {
    }

    private static final List<Rule> RULES = List.of(
            new Rule("ACCESS_DENIED_BURST", "Accès refusés répétés", Set.of(AuditAction.ACCESS_DENIED), Duration.ofMinutes(60), 5, 10),
            new Rule("LOGIN_FAILED_BURST", "Échecs de connexion répétés", Set.of(AuditAction.LOGIN_FAILED), Duration.ofMinutes(30), 5, 10),
            new Rule("DELETE_BURST", "Suppressions en rafale", Set.of(AuditAction.DELETE), Duration.ofMinutes(60), 5, 15),
            new Rule("PRIVILEGE_CHANGES", "Changements de rôle ou de responsable répétés",
                    Set.of(AuditAction.ROLE_CHANGE, AuditAction.LEAD_CHANGE), Duration.ofHours(24), 3, 8));

    private final AiAssistGuard guard;
    private final AuditLogSearchRepository audit;
    private final TeamMemberRepository members;

    public AuditAnomalyService(AiAssistGuard guard, AuditLogSearchRepository audit, TeamMemberRepository members) {
        this.guard = guard;
        this.audit = audit;
        this.members = members;
    }

    /** Détection seule (aucun appel IA). */
    @Transactional(readOnly = true)
    public AnomalyResponse detect(AppUser actor, Long teamId, int days) {
        Team team = guard.authorize(actor, teamId);
        int d = Math.max(1, Math.min(30, days));
        if (guard.disabled(AiFeature.AUDIT_ANOMALIES)) {
            return new AnomalyResponse(teamId, d, List.of(), List.of(), guard.disabledNarrative());
        }
        return build(team, d);
    }

    /** Détection puis UNE phrase IA par anomalie (un seul appel, 10 anomalies au plus, personnes anonymisées). */
    @Transactional(readOnly = true)
    public AnomalyResponse explain(AppUser actor, Long teamId, int days) {
        Team team = guard.authorize(actor, teamId);
        int d = Math.max(1, Math.min(30, days));
        if (guard.disabled(AiFeature.AUDIT_ANOMALIES)) {
            return new AnomalyResponse(teamId, d, List.of(), List.of(), guard.disabledNarrative());
        }
        AnomalyResponse base = build(team, d);
        if (base.anomalies().isEmpty()) {
            return base;
        }
        AiNames names = new AiNames();
        List<Anomaly> top = base.anomalies().subList(0, Math.min(MAX_AI_ITEMS, base.anomalies().size()));
        StringBuilder prompt = new StringBuilder("Anomalies détectées par des règles automatiques dans le journal d'audit (")
                .append(d).append(" derniers jours) :\n");
        for (int i = 0; i < top.size(); i++) {
            Anomaly a = top.get(i);
            prompt.append(i + 1).append(". règle=").append(a.ruleLabel()).append(", gravité=").append(a.severity())
                    .append(", utilisateur=").append(names.alias(a.actor())).append(", occurrences=").append(a.count())
                    .append(", de ").append(a.firstAt()).append(" à ").append(a.lastAt()).append('\n');
        }
        prompt.append("\nPour CHAQUE ligne numérotée, écris UNE seule phrase claire en français qui explique le constat, sans "
                + "inventer de cause ni proposer d'action. Réponds uniquement sous la forme « N|phrase », une ligne par anomalie.");
        Narrative n = guard.narrate(AiFeature.AUDIT_ANOMALIES, AiAssistGuard.SYSTEM, prompt.toString(), names);
        if (!"OK".equals(n.status())) {
            return new AnomalyResponse(base.teamId(), d, base.facts(), base.anomalies(), n);
        }
        Map<Integer, String> sentences = new LinkedHashMap<>();
        for (String line : n.text().split("\\R")) {
            Matcher m = LINE.matcher(line);
            if (m.matches()) {
                sentences.put(Integer.parseInt(m.group(1)), m.group(2));
            }
        }
        List<Anomaly> out = new ArrayList<>();
        for (int i = 0; i < base.anomalies().size(); i++) {
            Anomaly a = base.anomalies().get(i);
            String s = i < top.size() ? sentences.get(i + 1) : null;
            out.add(s == null ? a : new Anomaly(a.key(), a.rule(), a.ruleLabel(), a.severity(), a.actor(), a.count(),
                    a.firstAt(), a.lastAt(), s, true));
        }
        return new AnomalyResponse(base.teamId(), d, base.facts(), out, n);
    }

    private AnomalyResponse build(Team team, int days) {
        LocalDateTime to = LocalDateTime.now();
        LocalDateTime from = to.minusDays(days);
        List<AuditLogEntry> entries = audit.searchForExport(
                new AuditSearchCriteria(null, null, null, null, null, null, null, from, to, null), MAX_ROWS);
        Set<Long> ids = null;
        Set<String> names = null;
        if (team != null) {
            ids = new HashSet<>();
            names = new HashSet<>();
            for (TeamMember m : members.findByTeamIdWithUser(team.getId())) {
                ids.add(m.getUser().getId());
                names.add(m.getUser().getUsername());
            }
            if (team.getLead() != null) {
                ids.add(team.getLead().getId());
                names.add(team.getLead().getUsername());
            }
        }
        List<AuditLogEntry> scoped = new ArrayList<>();
        for (AuditLogEntry e : entries) {
            if (ids == null || (e.getActorUserId() != null && ids.contains(e.getActorUserId()))
                    || (e.getActorUsername() != null && names.contains(e.getActorUsername()))) {
                scoped.add(e);
            }
        }
        List<Anomaly> anomalies = detectIn(scoped);
        long high = anomalies.stream().filter(a -> "HIGH".equals(a.severity())).count();
        long medium = anomalies.stream().filter(a -> "MEDIUM".equals(a.severity())).count();
        List<Fact> facts = List.of(
                Fact.of("Événements analysés", scoped.size()),
                Fact.of("Anomalies", anomalies.size(), anomalies.isEmpty() ? "good" : "warn"),
                Fact.of("Gravité haute", high, high > 0 ? "bad" : "good"),
                Fact.of("Gravité moyenne", medium, medium > 0 ? "warn" : "good"));
        return new AnomalyResponse(team == null ? null : team.getId(), days, facts, anomalies, Narrative.none());
    }

    /** Applique les règles à des entrées déjà filtrées ; visible pour les tests. */
    List<Anomaly> detectIn(List<AuditLogEntry> entries) {
        List<Anomaly> out = new ArrayList<>();
        for (Rule rule : RULES) {
            Map<String, List<LocalDateTime>> byActor = new LinkedHashMap<>();
            for (AuditLogEntry e : entries) {
                if (rule.actions().contains(e.getAction())) {
                    byActor.computeIfAbsent(actorOf(e), k -> new ArrayList<>()).add(e.getOccurredAt());
                }
            }
            byActor.forEach((actor, times) -> {
                Burst b = burst(times, rule.window());
                if (b.count >= rule.threshold()) {
                    String sev = b.count >= rule.high() ? "HIGH" : "MEDIUM";
                    out.add(new Anomaly(rule.id() + "|" + actor, rule.id(), rule.label(), sev, actor, b.count, b.first, b.last,
                            b.count + " événement(s) « " + rule.label().toLowerCase() + " » pour " + actor + " en moins de "
                                    + minutes(rule.window()) + ".", false));
                }
            });
        }
        // connexions réussies hors des horaires habituels (22 h - 6 h)
        Map<String, List<LocalDateTime>> night = new LinkedHashMap<>();
        for (AuditLogEntry e : entries) {
            if ((e.getAction() == AuditAction.LOGIN || e.getAction() == AuditAction.LOGIN_2FA) && e.getOccurredAt() != null
                    && (e.getOccurredAt().getHour() >= 22 || e.getOccurredAt().getHour() < 6)) {
                night.computeIfAbsent(actorOf(e), k -> new ArrayList<>()).add(e.getOccurredAt());
            }
        }
        night.forEach((actor, times) -> {
            times.sort(Comparator.naturalOrder());
            out.add(new Anomaly("OFF_HOURS_LOGIN|" + actor, "OFF_HOURS_LOGIN", "Connexions hors horaires habituels",
                    times.size() >= 3 ? "MEDIUM" : "LOW", actor, times.size(), times.get(0), times.get(times.size() - 1),
                    times.size() + " connexion(s) entre 22 h et 6 h pour " + actor + ".", false));
        });
        out.sort(Comparator.comparingInt((Anomaly a) -> severityRank(a.severity())).reversed()
                .thenComparing(Comparator.comparingLong(Anomaly::count).reversed()));
        return out.size() > 30 ? new ArrayList<>(out.subList(0, 30)) : out;
    }

    private record Burst(long count, LocalDateTime first, LocalDateTime last) {
    }

    /** Plus grand nombre d'événements dans une fenêtre glissante de la durée donnée. */
    private static Burst burst(List<LocalDateTime> times, Duration window) {
        List<LocalDateTime> t = new ArrayList<>(times);
        t.removeIf(x -> x == null);
        t.sort(Comparator.naturalOrder());
        long best = 0;
        LocalDateTime bf = null, bl = null;
        int lo = 0;
        for (int hi = 0; hi < t.size(); hi++) {
            while (Duration.between(t.get(lo), t.get(hi)).compareTo(window) > 0) {
                lo++;
            }
            long c = hi - lo + 1;
            if (c > best) {
                best = c;
                bf = t.get(lo);
                bl = t.get(hi);
            }
        }
        return new Burst(best, bf, bl);
    }

    private static String actorOf(AuditLogEntry e) {
        if (e.getActorUsername() != null && !e.getActorUsername().isBlank()) {
            return e.getActorUsername();
        }
        return e.getActorUserId() == null ? "inconnu" : "#" + e.getActorUserId();
    }

    private static int severityRank(String s) {
        return "HIGH".equals(s) ? 3 : "MEDIUM".equals(s) ? 2 : 1;
    }

    private static String minutes(Duration d) {
        return d.toHours() >= 1 && d.toMinutes() % 60 == 0 ? d.toHours() + " h" : d.toMinutes() + " min";
    }
}
