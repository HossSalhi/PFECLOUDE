package com.orange.pfe.backend.service.aiassist;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.MaintenanceWindowRequest;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.MaintenanceWindowResponse;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.WindowCandidate;
import com.orange.pfe.backend.model.maintenance.Maintenance;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.MaintenanceRepository;
import com.orange.pfe.backend.service.supervision.SupervisionClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.security.access.AccessDeniedException;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** (d) Fenêtre recommandée : profil de charge Prometheus, conflits évités, périmètre d'équipe, dégradation sans métriques. */
class MaintenanceWindowAdvisorTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    private SupervisionClient prom;
    private MaintenanceRepository repo;
    private AppUser admin, leadA, leadB;
    private Team teamA, teamB;

    @BeforeEach
    void setUp() {
        prom = mock(SupervisionClient.class);
        repo = mock(MaintenanceRepository.class);
        when(repo.findOverlapping(any(), any(), any(), any(), any())).thenReturn(List.of());
        admin = AiTestSupport.user(1, "root", "Ad", "Min", UserRole.ADMIN);
        leadA = AiTestSupport.user(10, "leada", "Lead", "Alpha", UserRole.USER);
        leadB = AiTestSupport.user(20, "leadb", "Lead", "Beta", UserRole.USER);
        teamA = AiTestSupport.team(1, "Équipe VM", leadA, ResourceScope.VM);
        teamB = AiTestSupport.team(2, "Équipe B", leadB, null);
        when(prom.promRange(any(), anyLong(), anyLong(), anyLong())).thenReturn(profile(3, 85));   // 3 % la nuit (heure locale 2 h-5 h), 85 % sinon
    }

    /** Série horaire de 7 jours : charge basse entre 2 h et 5 h (heure locale), forte le reste du temps. */
    private static com.fasterxml.jackson.databind.JsonNode profile(double night, double day) {
        ArrayNode result = JSON.createArrayNode();
        ObjectNode series = result.addObject();
        ArrayNode values = series.putArray("values");
        long end = Instant.now().getEpochSecond();
        for (int h = 7 * 24; h >= 0; h--) {
            long ts = end - h * 3600L;
            int hour = Instant.ofEpochSecond(ts).atZone(ZoneId.systemDefault()).getHour();
            values.addArray().add(ts).add(String.valueOf(hour >= 2 && hour <= 5 ? night : day));
        }
        return result;
    }

    private MaintenanceWindowAdvisor advisor(AiCompletionClient client) {
        return new MaintenanceWindowAdvisor(AiTestSupport.guard(client, teamA, teamB), prom, repo);
    }

    private MaintenanceWindowRequest req(Long teamId, String type) {
        return new MaintenanceWindowRequest(teamId, type, "k8s-worker1", 120, 7);
    }

    @Test
    void recommendsThreeNonOverlappingQuietNightWindows() {
        AiCompletionClient client = AiTestSupport.readyClient("Privilégiez la première fenêtre, la moins chargée.");

        MaintenanceWindowResponse r = advisor(client).recommend(admin, req(null, "VM"));

        assertThat(r.metricsAvailable()).isTrue();
        assertThat(r.windows()).hasSize(3);
        for (WindowCandidate w : r.windows()) {
            assertThat(w.start().getHour()).isBetween(2, 4);                           // créneaux de 2 h dans la zone creuse
            assertThat(w.averageLoad()).isEqualTo(3.0);
            assertThat(w.score()).isEqualTo(97);
            assertThat(w.end()).isEqualTo(w.start().plusMinutes(120));
        }
        assertThat(r.analysis().status()).isEqualTo("OK");
        assertThat(r.facts()).anyMatch(f -> f.label().contains("Source") && f.value().contains("Prometheus"));
    }

    @Test
    void windowsNeverOverlapEachOther() {
        MaintenanceWindowResponse r = advisor(AiTestSupport.readyClient("x")).recommend(admin, req(null, "VM"));

        for (int i = 0; i < r.windows().size(); i++) {
            for (int j = i + 1; j < r.windows().size(); j++) {
                WindowCandidate a = r.windows().get(i), b = r.windows().get(j);
                assertThat(a.start().isBefore(b.end()) && a.end().isAfter(b.start())).isFalse();
            }
        }
    }

    @Test
    void existingMaintenanceOnTheSameResourceExcludesOverlappingSlots() {
        // toute fenêtre démarrant avant demain midi est en conflit
        LocalDateTime limit = LocalDateTime.now().plusDays(1).withHour(12);
        when(repo.findOverlapping(eq(MaintenanceResourceType.VM), eq("k8s-worker1"), any(), any(), any())).thenAnswer(i -> {
            LocalDateTime start = i.getArgument(3);
            return start.isBefore(limit) ? List.of(mock(Maintenance.class)) : List.of();
        });

        MaintenanceWindowResponse r = advisor(AiTestSupport.readyClient("x")).recommend(admin, req(null, "VM"));

        assertThat(r.windows()).isNotEmpty().allMatch(w -> !w.start().isBefore(limit));
        assertThat(r.facts()).anyMatch(f -> f.label().contains("écartés") && !f.value().equals("0"));
    }

    @Test
    void withoutPrometheusItFallsBackToOffPeakHoursAndSaysSo() {
        when(prom.promRange(any(), anyLong(), anyLong(), anyLong())).thenThrow(new SupervisionClient.UnavailableException("Prometheus est injoignable", null));
        AiCompletionClient client = AiTestSupport.readyClient("x");

        MaintenanceWindowResponse r = advisor(client).recommend(admin, req(null, "VM"));

        assertThat(r.metricsAvailable()).isFalse();
        assertThat(r.windows()).isNotEmpty().allMatch(w -> w.start().getHour() >= 1 && w.start().getHour() <= 4);
        assertThat(r.facts()).anyMatch(f -> f.value().contains("Indisponible"));
        ArgumentCaptor<String> prompt = ArgumentCaptor.forClass(String.class);
        verify(client).complete(any(), any(), prompt.capture());
        assertThat(prompt.getValue()).contains("métriques sont indisponibles");
    }

    @Test
    void incompleteProfileCountsAsUnavailable() {
        ArrayNode result = JSON.createArrayNode();
        result.addObject().putArray("values").addArray().add(Instant.now().getEpochSecond()).add("50");

        when(prom.promRange(any(), anyLong(), anyLong(), anyLong())).thenReturn(result);

        assertThat(advisor(AiTestSupport.readyClient("x")).recommend(admin, req(null, "VM")).metricsAvailable()).isFalse();
    }

    @Test
    void aiDownStillReturnsTheComputedWindows() {
        MaintenanceWindowResponse r = advisor(AiTestSupport.clientWith(AiCompletionResult.Reason.UNREACHABLE)).recommend(admin, req(null, "VM"));

        assertThat(r.windows()).hasSize(3);
        assertThat(r.analysis().status()).isEqualTo("UNAVAILABLE");
    }

    // ── périmètre ─────────────────────────────────────────────────────────────────────────────────────────────────

    @Test
    void leaderGetsRecommendationsForHisOwnTeamResourcesOnly() {
        assertThat(advisor(AiTestSupport.readyClient("x")).recommend(leadA, req(1L, "VM")).windows()).hasSize(3);

        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(leadA, req(2L, "VM"))).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(leadB, req(1L, "VM"))).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(leadA, req(null, "VM"))).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void aResourceOutsideTheTeamSpecialtyIsRefused() {
        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(leadA, req(1L, "APPLICATION")))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("périmètre");
        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(admin, req(1L, "SERVICE")))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void invalidInputsAreRejected() {
        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(admin, req(null, "BANANE"))).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(admin,
                new MaintenanceWindowRequest(null, "VM", "  ", 60, 7))).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void disabledFeatureQueriesNothing() {
        AiCompletionClient off = AiTestSupport.clientWith(AiCompletionResult.Reason.DISABLED);

        MaintenanceWindowResponse r = advisor(off).recommend(admin, req(null, "VM"));

        assertThat(r.analysis().status()).isEqualTo("DISABLED");
        assertThat(r.windows()).isEmpty();
        verify(prom, never()).promRange(any(), anyLong(), anyLong(), anyLong());
        verify(off, never()).complete(any(), any(), any());
    }

    @Test
    void durationAndHorizonAreBounded() {
        MaintenanceWindowResponse r = advisor(AiTestSupport.readyClient("x")).recommend(admin,
                new MaintenanceWindowRequest(null, "VM", "k8s-worker1", 5, 500));

        assertThat(r.facts()).anyMatch(f -> f.label().equals("Durée prévue") && f.value().equals("30 min"));
        assertThat(r.facts()).anyMatch(f -> f.label().equals("Horizon") && f.value().equals("30 jours"));
    }
}
