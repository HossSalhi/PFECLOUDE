package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.AssignmentFeedback;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.AssignmentRecommendation;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Candidate;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Fact;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Narrative;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TaskRecommendationRequest;
import com.orange.pfe.backend.dto.org.UserRef;
import com.orange.pfe.backend.dto.team.TaskFilter;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.team.TeamMember;
import com.orange.pfe.backend.model.team.TeamTask;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.TeamMemberRepository;
import com.orange.pfe.backend.repository.TeamTaskSearchRepository;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * (a) Recommandation d'assignation : score DÉTERMINISTE des membres de l'équipe (charge actuelle, retards, historique de
 * tâches terminées sur des sujets proches). L'IA ne fait que rédiger la justification du classement déjà calculé ; elle ne
 * crée ni n'assigne aucune tâche : l'humain accepte ou ignore. Le choix est tracé dans le journal d'audit.
 */
@Service
public class TaskAssignmentAdvisor {

    static final int MAX_ROWS = 10_000;
    private static final Pattern SPLIT = Pattern.compile("[^\\p{L}\\p{N}]+");
    private static final Set<String> STOP = Set.of("pour", "dans", "avec", "sans", "tache", "tâche", "des", "les", "une", "sur",
            "that", "this", "from", "the", "and", "task");

    private final AiAssistGuard guard;
    private final TeamMemberRepository members;
    private final TeamTaskSearchRepository tasks;
    private final AuditTrailService audit;

    public TaskAssignmentAdvisor(AiAssistGuard guard, TeamMemberRepository members, TeamTaskSearchRepository tasks,
                                 AuditTrailService audit) {
        this.guard = guard;
        this.members = members;
        this.tasks = tasks;
        this.audit = audit;
    }

    @Transactional(readOnly = true)
    public AssignmentRecommendation recommend(AppUser actor, TaskRecommendationRequest req) {
        if (req == null || req.teamId() == null) {
            throw new IllegalArgumentException("L'équipe est obligatoire");
        }
        Team team = guard.authorize(actor, req.teamId());
        if (req.title() == null || req.title().isBlank()) {
            throw new IllegalArgumentException("Le titre de la tâche est obligatoire");
        }
        if (guard.disabled(AiFeature.TASK_ASSIGNMENT)) {
            return new AssignmentRecommendation(team.getId(), team.getName(), List.of(), List.of(), null, guard.disabledNarrative());
        }
        List<AppUser> candidates = new ArrayList<>();
        for (TeamMember m : members.findByTeamIdWithUser(team.getId())) {
            if (m.getUser().isEnabled()) {
                candidates.add(m.getUser());
            }
        }
        List<TeamTask> history = tasks.findAllMatching(TaskFilter.forTeam(team.getId()), MAX_ROWS);
        List<Candidate> ranked = score(candidates, history, req.title() + " " + (req.description() == null ? "" : req.description()),
                LocalDate.now(), LocalDateTime.now());
        if (ranked.isEmpty()) {
            return new AssignmentRecommendation(team.getId(), team.getName(),
                    List.of(Fact.of("Membres éligibles", 0, "warn")), List.of(), null,
                    Narrative.none());
        }
        Candidate top = ranked.get(0);
        List<Fact> facts = List.of(
                Fact.of("Membres comparés", ranked.size()),
                Fact.of("Meilleur score", top.score() + " / 100", top.score() >= 70 ? "good" : "warn"),
                Fact.of("Charge du recommandé", top.openTasks() + " ouverte(s)"),
                Fact.of("Tâches similaires résolues", top.similarDone()));

        AiNames names = new AiNames();
        StringBuilder prompt = new StringBuilder("Nouvelle tâche à assigner dans l'équipe « ").append(team.getName())
                .append(" » : « ").append(req.title().trim()).append(" »")
                .append(req.priority() == null || req.priority().isBlank() ? "" : " (priorité " + req.priority() + ")")
                .append(".\nClassement calculé automatiquement (score sur 100) :\n");
        for (int i = 0; i < ranked.size() && i < 5; i++) {
            Candidate c = ranked.get(i);
            prompt.append(i + 1).append(". ").append(names.alias(c.displayName())).append(" : score ").append(c.score())
                    .append(", ").append(c.openTasks()).append(" tâche(s) ouverte(s), ").append(c.overdueTasks())
                    .append(" en retard, ").append(c.similarDone()).append(" tâche(s) similaire(s) déjà terminée(s)\n");
        }
        prompt.append("\nRédige en 2 à 4 phrases la justification de la première place, en t'appuyant uniquement sur ces chiffres. "
                + "Dis clairement que c'est une suggestion que le responsable peut ignorer.");
        Narrative n = guard.narrate(AiFeature.TASK_ASSIGNMENT, AiAssistGuard.SYSTEM, prompt.toString(), names);
        return new AssignmentRecommendation(team.getId(), team.getName(), facts, ranked, top.userId(), n);
    }

    /** Trace le choix du responsable (suggestion acceptée ou ignorée) : base du taux d'acceptation, sans nouvelle table. */
    @Transactional
    public void feedback(AppUser actor, AssignmentFeedback fb) {
        if (fb == null || fb.teamId() == null) {
            throw new IllegalArgumentException("L'équipe est obligatoire");
        }
        Team team = guard.authorize(actor, fb.teamId());
        audit.record(AuditEvent.of(AuditModule.TASKS, AuditAction.ASSIGN).actor(actor)
                .object("AiAssignmentSuggestion", team.getId())
                .summary("Suggestion IA d'assignation " + (fb.accepted() ? "ACCEPTÉE" : "IGNORÉE")
                        + " (équipe « " + team.getName() + " »)"));
    }

    /** Score déterministe ; visible pour les tests. */
    List<Candidate> score(List<AppUser> people, List<TeamTask> history, String subject, LocalDate today, LocalDateTime now) {
        Set<String> wanted = keywords(subject);
        List<Candidate> out = new ArrayList<>();
        for (AppUser u : people) {
            long open = 0, overdue = 0, similar = 0;
            for (TeamTask t : history) {
                if (t.getAssignee() == null || !u.getId().equals(t.getAssignee().getId())) {
                    continue;
                }
                if (t.getStatus().isOpen()) {
                    open++;
                    if (t.getDueDate() != null && t.getDueDate().isBefore(today)) {
                        overdue++;
                    }
                } else if (t.getStatus() == com.orange.pfe.backend.model.team.TaskStatus.DONE && !wanted.isEmpty()
                        && (t.getCompletedAt() == null || t.getCompletedAt().isAfter(now.minusDays(180)))) {
                    Set<String> k = keywords(t.getTitle());
                    k.retainAll(wanted);
                    if (!k.isEmpty()) {
                        similar++;
                    }
                }
            }
            int score = (int) Math.max(0, Math.min(100, 100 - 12 * open - 18 * overdue + 8 * Math.min(similar, 5)));
            List<String> reasons = new ArrayList<>();
            reasons.add(open == 0 ? "Aucune tâche ouverte" : open + " tâche(s) ouverte(s)");
            if (overdue > 0) {
                reasons.add(overdue + " tâche(s) en retard");
            }
            if (similar > 0) {
                reasons.add(similar + " tâche(s) similaire(s) déjà terminée(s)");
            }
            out.add(new Candidate(u.getId(), UserRef.displayNameOf(u), score, open, overdue, similar, reasons));
        }
        out.sort(Comparator.comparingInt(Candidate::score).reversed()
                .thenComparing(Comparator.comparingLong(Candidate::similarDone).reversed())
                .thenComparingLong(Candidate::openTasks).thenComparing(Candidate::displayName));
        return out;
    }

    private static Set<String> keywords(String text) {
        Set<String> out = new HashSet<>();
        if (text == null) {
            return out;
        }
        for (String w : SPLIT.split(text.toLowerCase(Locale.ROOT))) {
            if (w.length() >= 4 && !STOP.contains(w)) {
                out.add(w);
            }
        }
        return out;
    }
}
