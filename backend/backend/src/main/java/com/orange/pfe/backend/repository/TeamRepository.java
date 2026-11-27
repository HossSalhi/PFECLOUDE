package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.team.Team;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface TeamRepository extends JpaRepository<Team, Long> {

    boolean existsByNameKey(String nameKey);

    boolean existsByNameKeyAndIdNot(String nameKey, Long id);

    List<Team> findAllByOrderByNameAsc();

    /** Équipes dont l'utilisateur est responsable ou membre. */
    @Query("select t from Team t where t.lead.id = :userId "
            + "or t.id in (select m.team.id from TeamMember m where m.user.id = :userId) order by t.name")
    List<Team> findVisibleToUser(@Param("userId") Long userId);

    /** Équipes ACTIVES dont l'un des utilisateurs est responsable (badges de rôle calculés). */
    @Query("select t from Team t join fetch t.lead where t.active = true and t.lead.id in :leadIds")
    List<Team> findActiveByLeadIds(@Param("leadIds") java.util.Collection<Long> leadIds);

    @Query("select t.id from Team t where t.lead.id = :userId "
            + "or t.id in (select m.team.id from TeamMember m where m.user.id = :userId)")
    List<Long> findVisibleTeamIds(@Param("userId") Long userId);
}
