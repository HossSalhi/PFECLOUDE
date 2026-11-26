package com.orange.pfe.backend.service;

import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

class SseTicketServiceTest {

    static class MutableClock extends Clock {
        final AtomicReference<Instant> now = new AtomicReference<>(Instant.parse("2026-11-26T10:00:00Z"));
        @Override public java.time.ZoneId getZone() { return ZoneOffset.UTC; }
        @Override public Clock withZone(java.time.ZoneId z) { return this; }
        @Override public Instant instant() { return now.get(); }
    }

    @Test void validTicket_isAcceptedOnce_thenRefused() {
        SseTicketService s = new SseTicketService("secret-factice", 30, new MutableClock());
        String t = s.issue();
        assertThat(s.consume(t)).isTrue();
        assertThat(s.consume(t)).isFalse();
    }
    @Test void expiredTicket_isRefused() {
        MutableClock c = new MutableClock(); SseTicketService s = new SseTicketService("secret-factice", 30, c);
        String t = s.issue(); c.now.set(c.now.get().plusSeconds(31));
        assertThat(s.consume(t)).isFalse();
    }
    @Test void ticketJustBeforeExpiry_isAccepted() {
        MutableClock c = new MutableClock(); SseTicketService s = new SseTicketService("secret-factice", 30, c);
        String t = s.issue(); c.now.set(c.now.get().plusSeconds(29));
        assertThat(s.consume(t)).isTrue();
    }
    @Test void tamperedOrMalformedTickets_areRefused() {
        SseTicketService s = new SseTicketService("secret-factice", 30, new MutableClock());
        String t = s.issue(); String[] p = t.split("\\.");
        assertThat(s.consume(p[0] + "." + p[1] + ".AAAA")).isFalse();
        assertThat(s.consume((Long.parseLong(p[0]) + 3600) + "." + p[1] + "." + p[2])).isFalse();   // expiration prolongée
        for (String bad : new String[]{null, "", "a.b", "a.b.c.d", "x".repeat(300), "1.2.%%%"}) assertThat(s.consume(bad)).as(String.valueOf(bad)).isFalse();
    }
    @Test void ticketSignedWithAnotherSecret_isRefused() {
        MutableClock c = new MutableClock();
        String t = new SseTicketService("autre-secret", 30, c).issue();
        assertThat(new SseTicketService("secret-factice", 30, c).consume(t)).isFalse();
    }
    @Test void ticketsAreUnique_andTtlIsBounded() {
        SseTicketService s = new SseTicketService("secret-factice", 100000, new MutableClock());
        assertThat(s.issue()).isNotEqualTo(s.issue());
        assertThat(s.ttlSeconds()).isLessThanOrEqualTo(120);
    }
}