package com.orange.pfe.backend.service.team;

import com.orange.pfe.backend.dto.org.UserRef;
import com.orange.pfe.backend.dto.team.TeamResponses.MemberTaskStats;
import com.orange.pfe.backend.dto.team.TeamResponses.TaskSummaryResponse;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.TeamTask;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Calculs d'avancement des tâches, à partir des tâches réellement présentes en base (aucune valeur simulée). */
public final class TaskStats {

    private TaskStats() {
    }

    public static TaskSummaryResponse summarize(Collection<TeamTask> tasks, LocalDate today) {
        Map<String, Long> byStatus = new LinkedHashMap<>();
        for (TaskStatus s : TaskStatus.values()) {
            byStatus.put(s.name(), 0L);
        }
        long overdue = 0;
        for (TeamTask t : tasks) {
            byStatus.merge(t.getStatus().name(), 1L, Long::sum);
            if (t.isOverdue(today)) {
                overdue++;
            }
        }
        long total = tasks.size();
        long done = byStatus.get(TaskStatus.DONE.name());
        long cancelled = byStatus.get(TaskStatus.CANCELLED.name());
        long considered = total - cancelled;
        double rate = considered <= 0 ? 0.0 : Math.round(done * 1000.0 / considered) / 10.0;
        return new TaskSummaryResponse(total, byStatus, overdue, rate);
    }

    /** Une ligne par responsable (les tâches non affectées sont regroupées sous {@code userId = null}). */
    public static List<MemberTaskStats> byMember(Collection<TeamTask> tasks, LocalDate today) {
        Map<Long, long[]> counters = new LinkedHashMap<>();   // [total, open, done, overdue]
        Map<Long, String> names = new LinkedHashMap<>();
        for (TeamTask t : tasks) {
            Long uid = t.getAssignee() == null ? null : t.getAssignee().getId();
            counters.computeIfAbsent(uid, k -> new long[4]);
            names.putIfAbsent(uid, t.getAssignee() == null ? "Non affectées" : UserRef.displayNameOf(t.getAssignee()));
            long[] c = counters.get(uid);
            c[0]++;
            if (t.getStatus().isOpen()) {
                c[1]++;
            }
            if (t.getStatus() == TaskStatus.DONE) {
                c[2]++;
            }
            if (t.isOverdue(today)) {
                c[3]++;
            }
        }
        List<MemberTaskStats> out = new ArrayList<>();
        counters.forEach((uid, c) -> out.add(new MemberTaskStats(uid, names.get(uid), c[0], c[1], c[2], c[3])));
        out.sort(Comparator.comparing(MemberTaskStats::displayName, String.CASE_INSENSITIVE_ORDER));
        return out;
    }
}
