package com.orange.pfe.backend.service.supervision;

import com.orange.pfe.backend.config.InfraHealthProperties;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.GrafanaConfig;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;

class GrafanaConfigServiceTest {

    private GrafanaConfig config(boolean embed, String dashboards, String grafanaUrl) {
        InfraHealthProperties infra = new InfraHealthProperties();
        infra.setGrafanaUrl(grafanaUrl);
        GrafanaConfigService s = new GrafanaConfigService(infra);
        ReflectionTestUtils.setField(s, "embedEnabled", embed);
        ReflectionTestUtils.setField(s, "dashboards", dashboards);
        return s.config();
    }

    @Test
    void embeddingIsOffByDefaultAndNothingIsListedWithoutConfiguration() {
        GrafanaConfig c = config(false, "", "http://graf.test:3000");

        assertThat(c.embedEnabled()).isFalse();
        assertThat(c.dashboards()).isEmpty();
    }

    @Test
    void dashboardsBecomeEmbedAddressesBuiltFromTheConfiguredGrafana() {
        GrafanaConfig c = config(true, "Cluster|k8s-cluster; Etcd|etcd_1", "http://graf.test:3000/");

        assertThat(c.embedEnabled()).isTrue();
        assertThat(c.dashboards()).extracting(d -> d.title() + "=" + d.embedUrl())
                .containsExactly("Cluster=http://graf.test:3000/d/k8s-cluster?orgId=1&kiosk", "Etcd=http://graf.test:3000/d/etcd_1?orgId=1&kiosk");
    }

    @Test
    void anUnsafeUidOrAMalformedEntryIsIgnored() {
        GrafanaConfig c = config(true, "Mauvais|a/../b; Script|x\"onload=1; SansUid; Bon|ok-1", "http://graf.test:3000");

        assertThat(c.dashboards()).extracting(d -> d.uid()).containsExactly("ok-1");
    }

    @Test
    void aNonHttpGrafanaAddressDisablesEmbedding() {
        GrafanaConfig c = config(true, "A|a", "javascript:alert(1)");

        assertThat(c.embedEnabled()).isFalse();
        assertThat(c.dashboards()).singleElement().satisfies(d -> assertThat(d.embedUrl()).isNull());
    }
}
