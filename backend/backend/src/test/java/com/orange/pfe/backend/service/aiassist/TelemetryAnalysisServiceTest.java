package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TelemetryAnalysisRequest;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TelemetryAnalysisView;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TelemetryRunSummary;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.telemetry.TelemetryAnalysisRun;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.TeamRepository;
import com.orange.pfe.backend.repository.TelemetryAnalysisRunRepository;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryAnalysisGate;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryAnalysisService;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryDetector;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Detection;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.EntityCoverage;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Finding;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.QueryTrace;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.SourceQuality;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Trace;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryRunWriter;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import com.orange.pfe.backend.service.org.OrgAccessService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.Pageable;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.util.EnumSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Analyse IA des logs et métriques : droits, enregistrement traçable, confidentialité du prompt, ordre des appels, dégradation de l'IA. */
class TelemetryAnalysisServiceTest {

    private static final LocalDateTime NOW = LocalDateTime.of(2026, 11, 27, 12, 0);

    private TelemetryDetector detector;
    private TelemetryAnalysisRunRepository runs;
    private TeamRepository teams;
    private AuditTrailService audit;
    private TelemetryAnalysisGate gate;
    private AppUser admin, leadA, leadB, tech;
    private Team teamA, teamB;
    private Detection detection;

    @BeforeEach
    void setUp() {
        detector = mock(TelemetryDetector.class);
        runs = mock(TelemetryAnalysisRunRepository.class);
        teams = mock(TeamRepository.class);
        audit = mock(AuditTrailService.class);
        gate = mock(TelemetryAnalysisGate.class);
        when(gate.acquire(anyLong())).thenReturn("tok");
        admin = AiTestSupport.user(1, "root", "Ad", "Min", UserRole.SUPER_ADMIN);
        leadA = AiTestSupport.user(10, "leada", "Lead", "Alpha", UserRole.USER);
        leadB = AiTestSupport.user(20, "leadb", "Lead", "Beta", UserRole.USER);
        tech = AiTestSupport.user(30, "simple", "Sim", "Ple", UserRole.USER);
        teamA = AiTestSupport.team(1, "Réseau", leadA, ResourceScope.VM);
        teamB = AiTestSupport.team(2, "Apps", leadB, ResourceScope.APPLICATION);
        when(teams.findById(1L)).thenReturn(Optional.of(teamA));
        when(teams.findById(2L)).thenReturn(Optional.of(teamB));
        when(teams.findActiveByLeadIds(any())).thenAnswer(i -> {
            Long id = ((java.util.Collection<Long>) i.getArgument(0)).iterator().next();
            return id == 10L ? List.of(teamA) : id == 20L ? List.of(teamB) : List.of();
        });
        when(runs.save(any())).thenAnswer(i -> i.getArgument(0));
        detection = detection("ANOMALIES", "COMPLETE");
        when(detector.detect(any(), anyInt(), any())).thenReturn(detection);
    }

    private Detection detection(String verdict, String dataQuality) {
        Finding f = new Finding("VM", "k8s-worker1", "CPU", "SUSTAINED_HIGH", "HIGH", NOW.minusMinutes(40), NOW, 96.0, 20.0, "%",
                "CPU au-dessus de 85 % pendant 40 min (pic 96.0 %)", "PromQL : x", "PROM_CPU");
        Trace trace = new Trace("telemetry-rules-2", NOW.minusHours(1), NOW, NOW.minusHours(2), NOW.minusHours(1), 60L, NOW.minusHours(1), NOW.minusHours(2), 3600L,
                Map.of("cpu.high/critical", "85.0 / 95.0 %"),
                List.of(new QueryTrace("PROM_CPU", "Prometheus", "CPU", "100-(avg by(instance)...", "OK", null, 60L, NOW.minusHours(2), NOW, 6, 366),
                        new QueryTrace("LOKI_ERRORS_PODS", "Loki", "LOG_ERRORS", "sum by (filename) ...", "FAILED", "Loki est injoignable", 3600L, NOW.minusHours(2), NOW, null, null)),
                List.of(new EntityCoverage("Prometheus", "k8s-worker1", "CPU", 0.78, "47 mesure(s) sur 60 attendues")));
        return new Detection(verdict, dataQuality, "ANOMALIES".equals(verdict) ? List.of(f) : List.of(),
                List.of(new SourceQuality("Prometheus (CPU, mémoire, disque)", true, 0.78, "Couverture inférieure à 80 % : k8s-worker1 (CPU 78 %).")),
                trace, NOW.minusHours(1), NOW, 3);
    }

    private TelemetryAnalysisService service(AiCompletionClient client) {
        return new TelemetryAnalysisService(AiTestSupport.guard(client, teamA, teamB), new OrgAccessService(mock(AuditTrailService.class)), detector, runs, teams,
                gate, new TelemetryRunWriter(runs, audit));
    }

    private TelemetryAnalysisRequest req(Long teamId, String scope, Integer minutes) {
        return new TelemetryAnalysisRequest(teamId, scope, minutes);
    }

    private String promptSent(AiCompletionClient client) {
        ArgumentCaptor<String> prompt = ArgumentCaptor.forClass(String.class);
        verify(client).complete(eq(AiFeature.TELEMETRY_ANALYSIS), any(), prompt.capture());
        return prompt.getValue();
    }

    // ── enregistrement, trace et audit ───────────────────────────────────────────────────────────────────────────

    @Test
    void anAnalysisIsRecordedWithEveryThingNeededToTraceItAndAudited() {
        AiCompletionClient client = AiTestSupport.readyClient("Une anomalie CPU a été détectée.");

        TelemetryAnalysisView v = service(client).analyse(admin, req(null, "ALL", 60));

        ArgumentCaptor<TelemetryAnalysisRun> saved = ArgumentCaptor.forClass(TelemetryAnalysisRun.class);
        verify(runs).save(saved.capture());
        TelemetryAnalysisRun r = saved.getValue();
        assertThat(r.getRequestedByUsername()).isEqualTo("root");
        assertThat(r.getRequestedByUserId()).isEqualTo(1L);
        assertThat(r.getScope()).isEqualTo("ALL");
        assertThat(r.getWindowMinutes()).isEqualTo(60);
        assertThat(r.getVerdict()).isEqualTo("ANOMALIES");
        assertThat(r.getDataQuality()).isEqualTo("COMPLETE");
        assertThat(r.getAnomalyCount()).isEqualTo(1);
        assertThat(r.getHighCount()).isEqualTo(1);
        assertThat(r.getFindingsJson()).contains("k8s-worker1").contains("PromQL : x").contains("\"queryRef\":\"PROM_CPU\"").contains("\"observed\":96.0").contains("\"baseline\":20.0");
        assertThat(r.getTraceJson()).contains("telemetry-rules-2").contains("\"metricsStepSeconds\":60").contains("baselineStart").contains("PROM_CPU")
                .contains("100-(avg by(instance)").contains("\"status\":\"FAILED\"").contains("Loki est injoignable").contains("cpu.high/critical");
        assertThat(r.getQualityJson()).contains("Prometheus");
        assertThat(r.getNarrativeStatus()).isEqualTo("OK");
        assertThat(r.getModel()).isEqualTo("test-model");

        assertThat(v.dataQuality()).isEqualTo("COMPLETE");
        assertThat(v.trace().queries()).extracting(QueryTrace::id).containsExactly("PROM_CPU", "LOKI_ERRORS_PODS");
        assertThat(v.trace().queries().get(1).reason()).isEqualTo("Loki est injoignable");
        assertThat(v.findings()).singleElement().satisfies(f -> assertThat(f.queryRef()).isEqualTo("PROM_CPU"));
        assertThat(v.facts()).extracting(f -> f.label()).contains("Verdict", "Qualité des données", "Anomalies", "Gravité haute");

        ArgumentCaptor<AuditEvent> ev = ArgumentCaptor.forClass(AuditEvent.class);
        verify(audit).record(ev.capture());
        assertThat(ev.getValue().module()).isEqualTo(AuditModule.AUDIT);
        assertThat(ev.getValue().action()).isEqualTo(AuditAction.GENERATE);
        assertThat(ev.getValue().summary()).contains("Analyse IA des logs et métriques").contains("Anomalies détectées");
    }

    @Test
    void aPartialCoverageVerdictIsStoredAndShownAsPartialNeverAsAllClear() {
        when(detector.detect(any(), anyInt(), any())).thenReturn(detection("PARTIAL_COVERAGE", "PARTIAL"));

        TelemetryAnalysisView v = service(AiTestSupport.readyClient("x")).analyse(admin, req(null, null, 60));

        ArgumentCaptor<TelemetryAnalysisRun> saved = ArgumentCaptor.forClass(TelemetryAnalysisRun.class);
        verify(runs).save(saved.capture());
        assertThat(saved.getValue().getVerdict()).isEqualTo("PARTIAL_COVERAGE");
        assertThat(saved.getValue().getDataQuality()).isEqualTo("PARTIAL");
        assertThat(v.facts()).anySatisfy(f -> {
            assertThat(f.label()).isEqualTo("Verdict");
            assertThat(f.value()).isEqualTo("Couverture partielle");
            assertThat(f.tone()).isEqualTo("warn");
        });
        assertThat(v.facts()).anySatisfy(f -> {
            assertThat(f.label()).isEqualTo("Qualité des données");
            assertThat(f.value()).isEqualTo("Partielle");
        });
        verify(audit).record(argThat(e -> e.summary().contains("Couverture partielle")));
    }

    private static AuditEvent argThat(java.util.function.Predicate<AuditEvent> p) {
        return org.mockito.ArgumentMatchers.argThat(p::test);
    }

    // ── confidentialité : ce que Groq reçoit ─────────────────────────────────────────────────────────────────────

    private Detection sensitiveDetection() {
        Finding disk = new Finding("VM", "k8s-worker1", "DISK", "FILLING_UP", "HIGH", NOW.minusHours(2), NOW, 91.0, null, "%",
                "Disque à 91.0 % et en hausse de 0.80 %/h : plein dans environ 11 h", "PromQL : q", "PROM_DISK");
        Finding app = new Finding("APPLICATION", "pfe/backend", "LOG_ERRORS", "ERROR_SPIKE", "MEDIUM", NOW.minusHours(1), NOW, 60.0, 3.0, "erreurs/h",
                "Pic de 60 erreurs/h (seuil 20, habituel 3/h)", "LogQL : q", "LOKI_ERRORS_PODS");
        Finding ip = new Finding("VM", "10.0.0.9", "CPU", "SUSTAINED_HIGH", "MEDIUM", NOW.minusHours(1), NOW, 90.0, null, "%", "CPU au-dessus de 85 %", "PromQL : q", "PROM_CPU");
        Finding host = new Finding("VM", "hôte master", "LOG_ERRORS", "NEW_ERRORS", "MEDIUM", NOW.minusHours(1), NOW, 40.0, 0.0, "erreurs", "40 lignes d'erreur", "LogQL : q", "LOKI_ERRORS_HOSTS");
        Trace trace = new Trace("telemetry-rules-2", NOW.minusHours(1), NOW, null, null, 60L, NOW.minusHours(1), NOW.minusHours(2), 3600L, Map.of(), List.of(),
                List.of(new EntityCoverage("Prometheus", "k8s-master", "CPU", 1.0, "ok"), new EntityCoverage("Loki", "hôte master", "LOG_LINES", 1.0, "ok")));
        return new Detection("ANOMALIES", "PARTIAL", List.of(disk, app, ip, host), List.of(
                new SourceQuality("Prometheus (CPU, mémoire, disque)", true, 0.7, "Couverture inférieure à 80 % : k8s-worker1 (CPU 78 %), k8s-master (DISK 60 %). Instance 192.168.148.158:9100 ignorée. Contact ops@orange.tn"),
                new SourceQuality("Loki (erreurs des logs)", true, 1.0, "Source(s) silencieuse(s) : hôte master, pfe/backend. Authorization: Bearer abcdef1234567890")),
                trace, NOW.minusHours(1), NOW, 5);
    }

    @Test
    void thePromptNeverCarriesIpAddressesResourceNamesUsersTeamsSecretsOrEmails() {
        when(detector.detect(any(), anyInt(), any())).thenReturn(sensitiveDetection());
        AiCompletionClient client = AiTestSupport.readyClient("ok");

        service(client).analyse(leadA, req(1L, null, 360));            // chef de l'équipe « Réseau » (spécialité VM)

        String prompt = promptSent(client);
        // aucune adresse IP, aucun port
        assertThat(prompt).doesNotContain("192.168").doesNotContain("10.0.0.9").doesNotContain(":9100").doesNotContainPattern("\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}");
        // aucun identifiant interne de ressource
        assertThat(prompt).doesNotContain("k8s-worker1").doesNotContain("k8s-master").doesNotContain("hôte master").doesNotContain("pfe/backend")
                .doesNotContain("worker").doesNotContain("backend");
        // aucun nom d'utilisateur, d'équipe, ni identité du demandeur
        assertThat(prompt).doesNotContain("leada").doesNotContain("Lead").doesNotContain("Alpha").doesNotContain("Réseau").doesNotContain("Apps").doesNotContain("root");
        // aucun secret ni e-mail
        assertThat(prompt).doesNotContain("Bearer").doesNotContain("abcdef1234567890").doesNotContain("ops@orange.tn").doesNotContain("orange.tn");
        // mais bien des constats chiffrés et des alias neutres
        assertThat(prompt).contains("Machine A").contains("Application A").contains("91.0 %").contains("60 erreurs/h").contains("[adresse masquée]")
                .contains("[secret masqué]").contains("[e-mail masqué]").contains("qualité des données : PARTIELLE");
        // jamais de contenu de log brut : seuls des comptes
        assertThat(prompt).doesNotContain("level=").doesNotContain("Exception").doesNotContain("/var/log");
    }

    @Test
    void theAliasesAreStableAndTheAnswerShownToTheUserGetsTheRealNamesBack() {
        when(detector.detect(any(), anyInt(), any())).thenReturn(sensitiveDetection());
        AiCompletionClient client = AiTestSupport.readyClient("Machine A voit son disque se remplir ; Application A signale un pic d'erreurs.");

        TelemetryAnalysisView v = service(client).analyse(admin, req(null, "ALL", 360));

        String prompt = promptSent(client);
        assertThat(prompt).contains("Machine A").contains("Application A");
        assertThat(v.analysis().text()).isEqualTo("k8s-worker1 voit son disque se remplir ; pfe/backend signale un pic d'erreurs.");
        // le texte enregistré est celui affiché (noms réels, pour les seuls utilisateurs autorisés)
        ArgumentCaptor<TelemetryAnalysisRun> saved = ArgumentCaptor.forClass(TelemetryAnalysisRun.class);
        verify(runs).save(saved.capture());
        assertThat(saved.getValue().getNarrativeText()).contains("k8s-worker1");
    }

    @Test
    void theSystemPromptAndTheInstructionsForbidInventingCausesAndRequireCompleteDataForAllClear() {
        AiCompletionClient client = AiTestSupport.readyClient("ok");

        service(client).analyse(admin, req(null, null, 60));

        String prompt = promptSent(client);
        assertThat(prompt).contains("Ne déduis aucune cause").contains("l'absence d'anomalie n'est établie que si la qualité des données est complète")
                .contains("couvertures partielles").contains("Qualité des sources");
    }

    // ── ordre des appels : aucune transaction pendant les appels externes ────────────────────────────────────────

    @Test
    void theCallsAreMadeInTheSafeOrderAndThePlaceIsAlwaysReleased() {
        AiCompletionClient client = AiTestSupport.readyClient("ok");

        service(client).analyse(admin, req(null, null, 60));

        InOrder order = inOrder(gate, detector, client, runs, audit);
        order.verify(gate).acquire(1L);
        order.verify(detector).detect(any(), anyInt(), any());
        order.verify(client).complete(any(), any(), any());
        order.verify(runs).save(any());
        order.verify(audit).record(any(AuditEvent.class));
        order.verify(gate).release(1L, "tok");
    }

    @Test
    void anUnexpectedFailureStillReleasesThePlaceAndRecordsNothing() {
        when(detector.detect(any(), anyInt(), any())).thenThrow(new IllegalStateException("boum"));

        assertThatThrownBy(() -> service(AiTestSupport.readyClient("x")).analyse(admin, req(null, null, 60))).isInstanceOf(IllegalStateException.class);

        verify(gate).release(1L, "tok");
        verify(runs, never()).save(any());
        verify(audit, never()).record(any(AuditEvent.class));
    }

    @Test
    void aRefusedPlaceMeansNothingIsQueriedRecordedOrSentToTheProvider() {
        when(gate.acquire(anyLong())).thenThrow(new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS, "Une analyse est déjà en cours"));
        AiCompletionClient client = AiTestSupport.readyClient("x");

        assertThatThrownBy(() -> service(client).analyse(admin, req(null, null, 60))).isInstanceOf(ResponseStatusException.class)
                .satisfies(e -> assertThat(((ResponseStatusException) e).getStatusCode().value()).isEqualTo(429));

        verify(detector, never()).detect(any(), anyInt(), any());
        verify(client, never()).complete(any(), any(), any());
        verify(runs, never()).save(any());
        verify(gate, never()).release(anyLong(), any());
    }

    @Test
    void theAnalysisMethodOpensNoTransactionWhileOnlyTheFinalWriteAndTheReadsDo() throws Exception {
        assertThat(TelemetryAnalysisService.class.isAnnotationPresent(Transactional.class)).isFalse();
        assertThat(TelemetryAnalysisService.class.getMethod("analyse", AppUser.class, TelemetryAnalysisRequest.class).isAnnotationPresent(Transactional.class)).isFalse();
        assertThat(TelemetryRunWriter.class.getMethod("persist", AppUser.class, TelemetryAnalysisRun.class, String.class).isAnnotationPresent(Transactional.class)).isTrue();
        assertThat(TelemetryAnalysisService.class.getMethod("history", AppUser.class, Long.class, int.class, int.class).getAnnotation(Transactional.class).readOnly()).isTrue();
        assertThat(TelemetryAnalysisService.class.getMethod("get", AppUser.class, Long.class).getAnnotation(Transactional.class).readOnly()).isTrue();
    }

    // ── dégradation de l'IA ──────────────────────────────────────────────────────────────────────────────────────

    @Test
    void aMissingAiKeyStillRecordsTheFindingsAndSaysSo() {
        TelemetryAnalysisView v = service(AiTestSupport.clientWith(AiCompletionResult.Reason.NOT_CONFIGURED)).analyse(admin, req(null, null, 60));

        assertThat(v.analysis().status()).isEqualTo("NOT_CONFIGURED");
        assertThat(v.findings()).hasSize(1);
        verify(runs).save(any());
    }

    @Test
    void aQuotaOrTimeoutIsRecordedAsUnavailableNotAsAnError() {
        TelemetryAnalysisView v = service(AiTestSupport.clientWith(AiCompletionResult.Reason.QUOTA)).analyse(admin, req(null, null, 60));

        assertThat(v.analysis().status()).isEqualTo("UNAVAILABLE");
        assertThat(v.analysis().message()).contains("quota");
        assertThat(v.verdict()).isEqualTo("ANOMALIES");
    }

    @Test
    void whenTheFeatureIsDisabledNothingIsQueriedRecordedAuditedOrReserved() {
        AiCompletionClient off = AiTestSupport.clientWith(AiCompletionResult.Reason.DISABLED);

        assertThatThrownBy(() -> service(off).analyse(admin, req(null, null, 60))).isInstanceOf(IllegalStateException.class);

        verify(gate, never()).acquire(anyLong());
        verify(detector, never()).detect(any(), anyInt(), any());
        verify(runs, never()).save(any());
        verify(audit, never()).record(any(AuditEvent.class));
        verify(off, never()).complete(any(), any(), any());
    }

    // ── périmètre, durée, droits ─────────────────────────────────────────────────────────────────────────────────

    @Test
    void anAdminChoosesTheScopeAndALeaderIsLockedToHisTeamSpecialty() {
        AiCompletionClient c = AiTestSupport.readyClient("x");

        service(c).analyse(admin, req(null, "application", 60));
        service(c).analyse(leadA, req(1L, "APPLICATION", 60));       // le choix du chef est ignoré : sa spécialité (VM) s'applique
        service(c).analyse(leadB, req(2L, null, 60));

        ArgumentCaptor<Set<ResourceScope>> scopes = ArgumentCaptor.forClass(Set.class);
        verify(detector, org.mockito.Mockito.times(3)).detect(any(), eq(60), scopes.capture());
        assertThat(scopes.getAllValues().get(0)).isEqualTo(EnumSet.of(ResourceScope.APPLICATION));
        assertThat(scopes.getAllValues().get(1)).isEqualTo(EnumSet.of(ResourceScope.VM));
        assertThat(scopes.getAllValues().get(2)).isEqualTo(EnumSet.of(ResourceScope.APPLICATION));
    }

    @Test
    void anAdminWithoutScopeAnalysesEverythingAndAnInvalidScopeIsRejected() {
        AiCompletionClient c = AiTestSupport.readyClient("x");

        service(c).analyse(admin, req(null, null, 60));
        ArgumentCaptor<Set<ResourceScope>> scopes = ArgumentCaptor.forClass(Set.class);
        verify(detector).detect(any(), eq(60), scopes.capture());
        assertThat(scopes.getValue()).isNull();

        assertThatThrownBy(() -> service(c).analyse(admin, req(null, "BANANE", 60))).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void theDurationIsBoundedToFifteenMinutesAndSevenDays() {
        AiCompletionClient c = AiTestSupport.readyClient("x");

        service(c).analyse(admin, req(null, null, 1));
        service(c).analyse(admin, req(null, null, 999_999));
        service(c).analyse(admin, req(null, null, null));

        ArgumentCaptor<Integer> minutes = ArgumentCaptor.forClass(Integer.class);
        verify(detector, org.mockito.Mockito.times(3)).detect(any(), minutes.capture(), any());
        assertThat(minutes.getAllValues()).containsExactly(15, 7 * 24 * 60, 60);
    }

    @Test
    void aLeaderCanNeverAnalyseAnotherTeamOrTheGlobalViewAndATechnicianNothing() {
        AiCompletionClient c = AiTestSupport.readyClient("x");

        assertThatThrownBy(() -> service(c).analyse(leadA, req(2L, null, 60))).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service(c).analyse(leadB, req(1L, null, 60))).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service(c).analyse(leadA, req(null, null, 60))).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service(c).analyse(tech, req(1L, null, 60))).isInstanceOf(AccessDeniedException.class);

        verify(gate, never()).acquire(anyLong());
        verify(detector, never()).detect(any(), anyInt(), any());
        verify(runs, never()).save(any());
    }

    // ── historique ───────────────────────────────────────────────────────────────────────────────────────────────

    private TelemetryAnalysisRun run(long id, Long teamId) {
        TelemetryAnalysisRun r = new TelemetryAnalysisRun(NOW, 10L, "leada", teamId, teamId == null ? null : "Réseau", "VM", 60, NOW.minusHours(1), NOW,
                "NO_ANOMALY", "COMPLETE", 0, 0, "[]", "[{\"source\":\"Prometheus\",\"available\":true,\"coverage\":1.0,\"note\":\"ok\"}]",
                "{\"rulesVersion\":\"telemetry-rules-2\",\"windowStart\":\"2026-11-27T11:00:00\",\"windowEnd\":\"2026-11-27T12:00:00\",\"metricsStepSeconds\":60,"
                        + "\"queries\":[{\"id\":\"PROM_CPU\",\"source\":\"Prometheus\",\"signal\":\"CPU\",\"query\":\"q\",\"status\":\"OK\",\"stepSeconds\":60}],"
                        + "\"coverage\":[{\"source\":\"Prometheus\",\"entity\":\"k8s-worker1\",\"signal\":\"CPU\",\"coverage\":1.0,\"note\":\"ok\"}],\"thresholds\":{}}",
                "OK", "Tout va bien.", null, "m", 12);
        ReflectionTestUtils.setField(r, "id", id);
        return r;
    }

    @Test
    void historyShowsEverythingToAdminsAndOnlyOwnTeamsToLeaders() {
        when(runs.findAllByOrderByRequestedAtDesc(any(Pageable.class))).thenReturn(new PageImpl<>(List.of(run(1, 1L), run(2, 2L))));
        when(runs.findByTeamIdInOrderByRequestedAtDesc(any(), any(Pageable.class))).thenReturn(new PageImpl<>(List.of(run(1, 1L))));
        AiCompletionClient c = AiTestSupport.readyClient("x");

        assertThat(service(c).history(admin, null, 0, 10).getContent()).extracting(TelemetryRunSummary::id).containsExactly(1L, 2L);
        assertThat(service(c).history(leadA, null, 0, 10).getContent()).extracting(TelemetryRunSummary::id).containsExactly(1L);
        assertThat(service(c).history(admin, null, 0, 10).getContent().get(0).dataQuality()).isEqualTo("COMPLETE");
        verify(runs).findByTeamIdInOrderByRequestedAtDesc(eq(List.of(1L)), any(Pageable.class));
    }

    @Test
    void aLeaderCannotListAnotherTeamsHistoryAndATechnicianCannotListAny() {
        AiCompletionClient c = AiTestSupport.readyClient("x");

        assertThatThrownBy(() -> service(c).history(leadA, 2L, 0, 10)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service(c).history(tech, null, 0, 10)).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void aStoredAnalysisIsReopenedIdenticallyWithItsTraceAndAccessIsEnforcedPerTeam() {
        when(runs.findById(5L)).thenReturn(Optional.of(run(5, 1L)));
        when(runs.findById(6L)).thenReturn(Optional.of(run(6, 2L)));
        when(runs.findById(7L)).thenReturn(Optional.of(run(7, null)));
        AiCompletionClient c = AiTestSupport.readyClient("x");

        TelemetryAnalysisView v = service(c).get(leadA, 5L);
        assertThat(v.id()).isEqualTo(5L);
        assertThat(v.verdict()).isEqualTo("NO_ANOMALY");
        assertThat(v.dataQuality()).isEqualTo("COMPLETE");
        assertThat(v.analysis().text()).isEqualTo("Tout va bien.");
        assertThat(v.quality()).singleElement().satisfies(q -> assertThat(q.source()).isEqualTo("Prometheus"));
        assertThat(v.trace().rulesVersion()).isEqualTo("telemetry-rules-2");
        assertThat(v.trace().metricsStepSeconds()).isEqualTo(60L);
        assertThat(v.trace().queries()).singleElement().satisfies(q -> assertThat(q.query()).isEqualTo("q"));
        assertThat(v.trace().coverage()).singleElement().satisfies(c2 -> assertThat(c2.entity()).isEqualTo("k8s-worker1"));
        assertThat(service(c).get(admin, 6L).teamName()).isEqualTo("Réseau");

        assertThatThrownBy(() -> service(c).get(leadA, 6L)).isInstanceOf(AccessDeniedException.class);        // équipe d'un autre chef
        assertThatThrownBy(() -> service(c).get(leadA, 7L)).isInstanceOf(AccessDeniedException.class);        // analyse globale d'un admin
        assertThatThrownBy(() -> service(c).get(tech, 5L)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service(c).get(admin, 99L)).isInstanceOf(ResourceNotFoundException.class);
    }

    @Test
    void theRepositoryOffersNoWayToModifyOrDeleteAnAnalysis() {
        List<String> names = java.util.Arrays.stream(TelemetryAnalysisRunRepository.class.getMethods()).map(java.lang.reflect.Method::getName).toList();

        assertThat(names).noneMatch(n -> n.startsWith("delete") || n.startsWith("remove") || n.startsWith("update"));
        assertThat(org.springframework.data.jpa.repository.JpaRepository.class.isAssignableFrom(TelemetryAnalysisRunRepository.class)).isFalse();
        assertThat(TelemetryAnalysisRun.class.isAnnotationPresent(org.hibernate.annotations.Immutable.class)).isTrue();
        assertThat(java.util.Arrays.stream(TelemetryAnalysisRun.class.getMethods()).map(java.lang.reflect.Method::getName)).noneMatch(n -> n.startsWith("set"));
    }

}
