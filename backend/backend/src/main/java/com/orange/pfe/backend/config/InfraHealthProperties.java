package com.orange.pfe.backend.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "infra.health")
public class InfraHealthProperties {

    private boolean enabled = true;
    private int intervalSeconds = 300;
    private int timeoutSeconds = 10;

    // VM Monitoring
    private String prometheusUrl = "http://192.168.148.160:9090";
    private String lokiUrl       = "http://192.168.148.160:3100";
    private String grafanaUrl    = "http://192.168.148.160:3000";

    // VM IaCamunda (retrait de l'IA, 11 nov. 2026 : il ne reste que Camunda)
    private String camundaUrl    = "http://192.168.148.161:8088";

    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }

    public int getIntervalSeconds() { return intervalSeconds; }
    public void setIntervalSeconds(int intervalSeconds) { this.intervalSeconds = intervalSeconds; }

    public int getTimeoutSeconds() { return timeoutSeconds; }
    public void setTimeoutSeconds(int timeoutSeconds) { this.timeoutSeconds = timeoutSeconds; }

    public String getPrometheusUrl() { return prometheusUrl; }
    public void setPrometheusUrl(String prometheusUrl) { this.prometheusUrl = prometheusUrl; }

    public String getLokiUrl() { return lokiUrl; }
    public void setLokiUrl(String lokiUrl) { this.lokiUrl = lokiUrl; }

    public String getGrafanaUrl() { return grafanaUrl; }
    public void setGrafanaUrl(String grafanaUrl) { this.grafanaUrl = grafanaUrl; }

    public String getCamundaUrl() { return camundaUrl; }
    public void setCamundaUrl(String camundaUrl) { this.camundaUrl = camundaUrl; }
}
