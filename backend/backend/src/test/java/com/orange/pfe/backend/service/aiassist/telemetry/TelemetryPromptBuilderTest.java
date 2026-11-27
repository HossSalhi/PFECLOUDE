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
}
