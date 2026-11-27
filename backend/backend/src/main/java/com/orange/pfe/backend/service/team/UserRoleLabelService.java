package com.orange.pfe.backend.service.team;

import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.team.TeamMember;
import com.orange.pfe.backend.repository.TeamMemberRepository;
import com.orange.pfe.backend.repository.TeamRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Badges « Chef / Technicien &lt;spécialité&gt; » d'un utilisateur, calculés à la volée à partir de ses équipes ACTIVES
 * (en 2 requêtes quel que soit le nombre d'utilisateurs). Lecture seule : aucune donnée n'est ajoutée à l'utilisateur.
 * Dans une même équipe, le responsable est « Chef » même s'il en est aussi membre.
 */
@Service
public class UserRoleLabelService {

    private final TeamRepository teams;
    private final TeamMemberRepository members;

    public UserRoleLabelService(TeamRepository teams, TeamMemberRepository members) {
        this.teams = teams;
        this.members = members;
    }

    /** Libellés triés et sans doublon, par identifiant d'utilisateur ; un utilisateur sans équipe active a une liste vide. */
    @Transactional(readOnly = true)
    public Map<Long, List<String>> labelsFor(Collection<Long> userIds) {
        Map<Long, List<String>> result = new HashMap<>();
        if (userIds == null || userIds.isEmpty()) {
            return result;
        }
        Map<Long, Set<Long>> leadOf = new HashMap<>();            // userId -> équipes dont il est responsable
        Map<Long, Map<Long, Team>> teamsOf = new HashMap<>();     // userId -> équipes (id -> équipe)
        for (Team t : teams.findActiveByLeadIds(userIds)) {
            leadOf.computeIfAbsent(t.getLead().getId(), k -> new LinkedHashSet<>()).add(t.getId());
            teamsOf.computeIfAbsent(t.getLead().getId(), k -> new HashMap<>()).put(t.getId(), t);
        }
        for (TeamMember m : members.findActiveByUserIds(userIds)) {
            teamsOf.computeIfAbsent(m.getUser().getId(), k -> new HashMap<>()).put(m.getTeam().getId(), m.getTeam());
        }
        for (Long id : userIds) {
            Set<String> labels = new TreeSet<>();
            Map<Long, Team> mine = teamsOf.getOrDefault(id, Map.of());
            for (Team t : mine.values()) {
                labels.add(RoleLabels.of(t.getSpecialty(), leadOf.getOrDefault(id, Set.of()).contains(t.getId())));
            }
            result.put(id, new ArrayList<>(labels));
        }
        return result;
    }
}
