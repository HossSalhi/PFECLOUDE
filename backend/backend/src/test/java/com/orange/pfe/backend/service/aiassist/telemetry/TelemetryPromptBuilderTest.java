package com.orange.pfe.backend.service.aiassist.telemetry;

import com.orange.pfe.backend.service.aiassist.AiNames;
import org.junit.jupiter.api.Test;

import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/** Pseudonymisation et masquage appliqués au texte envoyé au fournisseur IA. */
class TelemetryPromptBuilderTest {

    @Test
    void resourcesBecomeStableNeutralAliasesByCategory() {
        AiNames names = new AiNames();

        String out = TelemetryPromptBuilder.sanitize("k8s-worker1 et k8s-master ; pfe/backend ; kube-system/etcd ; infra/loki ; k8s-worker1 encore",
                Set.of("k8s-worker1", "k8s-master", "pfe/backend", "kube-system/etcd", "infra/loki"), names);

        assertThat(out).isEqualTo("Machine A et Machine B ; Application A ; Composant du cluster A ; Service A ; Machine A encore");
        assertThat(names.restore(out)).isEqualTo("k8s-worker1 et k8s-master ; pfe/backend ; kube-system/etcd ; infra/loki ; k8s-worker1 encore");
    }

    @Test
    void theLongestNameIsReplacedFirstSoNoFragmentOfAnInternalNameSurvives() {
        AiNames names = new AiNames();

        String out = TelemetryPromptBuilder.sanitize("hôte master puis k8s-master puis master", Set.of("master", "hôte master", "k8s-master"), names);

        assertThat(out).doesNotContain("master");
    }

    @Test
    void ipAddressesPortsEmailsAndTokensAreMaskedEvenWhenUnknown() {
        String out = TelemetryPromptBuilder.sanitize("a 192.168.148.158:9100 b 10.0.0.1 c ops@orange.tn d Bearer abcdef123456 e "
                + "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJyb290In0.sig12345 f gsk_abcdefghijklmnop g Basic dXNlcjpwYXNz", Set.of(), new AiNames());

        assertThat(out).doesNotContain("192.168").doesNotContain("10.0.0.1").doesNotContain("orange.tn").doesNotContain("abcdef123456")
                .doesNotContain("eyJ").doesNotContain("gsk_").doesNotContain("dXNlcjpwYXNz");
        assertThat(out).contains("[adresse masquée]").contains("[e-mail masqué]").contains("[secret masqué]");
    }

    @Test
    void ordinaryNumbersAndPercentagesAreLeftUntouched() {
        String out = TelemetryPromptBuilder.sanitize("CPU à 91.5 % pendant 40 min, 60 erreurs/h, version 8.9.0, pic 12:30", Set.of(), new AiNames());

        assertThat(out).isEqualTo("CPU à 91.5 % pendant 40 min, 60 erreurs/h, version 8.9.0, pic 12:30");
    }

    @Test
    void categoriesAreNeutralAndNeverRevealTheName() {
        assertThat(TelemetryPromptBuilder.prefixOf("hôte worker1")).isEqualTo("Machine");
        assertThat(TelemetryPromptBuilder.prefixOf("k8s-lb2")).isEqualTo("Machine");
        assertThat(TelemetryPromptBuilder.prefixOf("pfe/backend")).isEqualTo("Application");
        assertThat(TelemetryPromptBuilder.prefixOf("kube-system/coredns")).isEqualTo("Composant du cluster");
        assertThat(TelemetryPromptBuilder.prefixOf("infra/grafana")).isEqualTo("Service");
    }

    @Test
    void theFindingsAreSplitBetweenApplicationAndInfrastructureWithoutRevealingNames() {
        var app = new TelemetryModels.Finding("APPLICATION", "pfe/backend", "WORKLOAD", "POD_RESTARTS", "HIGH", null, null, 7.0, 0.0, "redémarrage(s)",
                "Le conteneur a redémarré 7 fois sur la période", "PromQL : q", "PROBE_RESTARTS");
        var infra = new TelemetryModels.Finding("SERVICE", "service Loki", "MONITORING", "MONITORING_NOT_READY", "HIGH", null, null, 0.0, null, null,
                "Loki n'est pas prêt : HTTP 503 sur 192.168.148.160:3100", "GET /ready", "PROBE_TOOLS_READY");
        var vm = new TelemetryModels.Finding("VM", "k8s-lb2", "TARGET", "TARGET_DOWN", "HIGH", null, null, 0.0, null, null,
                "Cible injoignable", "PromQL : up == 0", "PROBE_TARGETS_DOWN");
        var trace = new TelemetryModels.Trace("r", java.time.LocalDateTime.now().minusHours(1), java.time.LocalDateTime.now(), null, null, null, null, null, null,
                java.util.Map.of(), java.util.List.of(), java.util.List.of());
        var d = new TelemetryModels.Detection("ANOMALIES", "COMPLETE", java.util.List.of(app, infra, vm), java.util.List.of(), trace, trace.windowStart(), trace.windowEnd(), 3);

        String prompt = TelemetryPromptBuilder.build(d, "ALL", 60, new AiNames());

        assertThat(prompt).contains("[Application] Application A").contains("[Infrastructure] Service A").contains("[Infrastructure] Machine A");
        assertThat(prompt).contains("Côté application").contains("Côté infrastructure");
        assertThat(prompt).doesNotContain("pfe/backend").doesNotContain("service Loki").doesNotContain("k8s-lb2").doesNotContain("192.168").doesNotContain(":3100");
    }
}
