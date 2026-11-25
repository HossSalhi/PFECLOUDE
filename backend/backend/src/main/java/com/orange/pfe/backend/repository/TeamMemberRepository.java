package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.team.TeamMember;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface TeamMemberRepository extends JpaRepository<TeamMember, Long> {

    @Query("select m from TeamMember m join fetch m.user where m.team.id = :teamId order by m.user.username")
    List<TeamMember> findByTeamIdWithUser(@Param("teamId") Long teamId);

    Optional<TeamMember> findByTeamIdAndUserId(Long teamId, Long userId);

    boolean existsByTeamIdAndUserId(Long teamId, Long userId);

    long countByTeamId(Long teamId);

    void deleteByTeamId(Long teamId);

    @Query("select m.team.id, count(m) from TeamMember m where m.team.id in :teamIds group by m.team.id")
    List<Object[]> countByTeamIds(@Param("teamIds") Collection<Long> teamIds);
}
