package com.orange.pfe.backend.service;

import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Le stockage PostgreSQL est simulé ici ; l'atomicité entre deux pods est prouvée par l'essai réel (deux backends, une base). */
class SseTicketServiceTest {

    @Test void issue_storesOnlyTheSha256_neverTheTicketItself() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        SseTicketService s = new SseTicketService(jdbc, 30);
        String t = s.issue("alice");
        ArgumentCaptor<Object> a = ArgumentCaptor.forClass(Object.class);
        verify(jdbc).update(anyString(), a.capture(), a.capture(), a.capture());
        assertThat(a.getAllValues().get(0)).isEqualTo(SseTicketService.sha256(t));
        assertThat((String) a.getAllValues().get(0)).hasSize(64).isNotEqualTo(t);
        assertThat(a.getAllValues().get(1)).isEqualTo("alice");
        assertThat(t.length()).isGreaterThanOrEqualTo(43);
    }
    @Test void tickets_areUnique() {
        SseTicketService s = new SseTicketService(mock(JdbcTemplate.class), 30);
        assertThat(s.issue("a")).isNotEqualTo(s.issue("a"));
    }
    @Test @SuppressWarnings("unchecked") void consume_returnsTheUser_whenTheAtomicDeleteMatchesOneRow() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(anyString(), any(RowMapper.class), eq(SseTicketService.sha256("t1")))).thenReturn(List.of("alice"));
        assertThat(new SseTicketService(jdbc, 30).consume("t1")).isEqualTo(Optional.of("alice"));
    }
    @Test @SuppressWarnings("unchecked") void consume_isEmpty_whenNothingDeleted_expiredReusedOrUnknown() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(anyString(), any(RowMapper.class), anyString())).thenReturn(List.of());
        assertThat(new SseTicketService(jdbc, 30).consume("t1")).isEmpty();
    }
    @Test void consume_rejectsNullBlankAndOversizedWithoutTouchingTheDatabase() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        SseTicketService s = new SseTicketService(jdbc, 30);
        for (String bad : new String[]{null, "", "  ", "x".repeat(101)}) assertThat(s.consume(bad)).isEmpty();
        org.mockito.Mockito.verifyNoInteractions(jdbc);
    }
    @Test void ttl_isBounded() {
        assertThat(new SseTicketService(mock(JdbcTemplate.class), 100000).ttlSeconds()).isEqualTo(120);
        assertThat(new SseTicketService(mock(JdbcTemplate.class), 1).ttlSeconds()).isEqualTo(5);
    }
}