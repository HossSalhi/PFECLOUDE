package com.orange.pfe.backend.config;

import org.springframework.boot.health.contributor.Health;
import org.springframework.boot.health.contributor.HealthIndicator;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.RestTemplate;

@Component
@ConditionalOnProperty(name = "camunda.enabled", havingValue = "true")
public class CamundaHealthIndicator implements HealthIndicator {

    private final CamundaProperties camundaProperties;
    private final RestTemplate restTemplate;

    public CamundaHealthIndicator(CamundaProperties camundaProperties) {
        this.camundaProperties = camundaProperties;
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(3000);
        factory.setReadTimeout(3000);
        this.restTemplate = new RestTemplate(factory);
    }

    @Override
    public Health health() {
        String url = camundaProperties.getRestAddress() + "/v2/topology";
        try {
            restTemplate.getForEntity(url, String.class);
            return Health.up().withDetail("url", url).build();
        } catch (HttpStatusCodeException ex) {
            return Health.down().withDetail("url", url).withDetail("status", ex.getStatusCode().value()).build();
        } catch (Exception ex) {
            return Health.down().withDetail("url", url).withDetail("error", ex.getMessage()).build();
        }
    }
}
