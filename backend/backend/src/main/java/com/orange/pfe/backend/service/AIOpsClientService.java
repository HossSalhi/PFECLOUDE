package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.AILearnRequest;
import com.orange.pfe.backend.dto.AIPredictRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.exception.AIOpsIntegrationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;

import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import java.net.HttpURLConnection;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.time.Instant;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Client for the Python AI service. Embeds a lightweight circuit-breaker:
 * after {@code circuitBreakerThreshold} consecutive failures the circuit opens
 * and all calls are short-circuited for {@code circuitBreakerOpenMs} ms, then
 * half-open again to allow a probe request through.
 */
@Service
public class AIOpsClientService {

    private static final Logger log = LoggerFactory.getLogger(AIOpsClientService.class);

    private final RestClient restClient;

    @Value("${aiops.client.circuit-breaker-threshold:3}")
    private int circuitBreakerThreshold;

    @Value("${aiops.client.circuit-breaker-open-ms:60000}")
    private long circuitBreakerOpenMs;

    private final AtomicInteger consecutiveFailures = new AtomicInteger(0);
    private final AtomicReference<Instant> openedAt = new AtomicReference<>(null);

    public AIOpsClientService(
            RestClient.Builder builder,
            @Value("${aiops.service.base-url}") String baseUrl,
            @Value("${aiops.service.api-key:}") String apiKey,
            @Value("${aiops.service.tls-skip-verify:false}") boolean tlsSkipVerify
    ) {
        RestClient.Builder b = builder
                .baseUrl(baseUrl)
                .requestInterceptor((req, body, execution) -> {
                    String traceId = MDC.get("traceId");
                    if (traceId != null) req.getHeaders().set("X-Request-ID", traceId);
                    if (apiKey != null && !apiKey.isBlank()) req.getHeaders().set("X-API-Key", apiKey);
                    return execution.execute(req, body);
                });

        // For dev/lab use only: skip TLS certificate verification when the AI service
        // is fronted by a self-signed nginx cert and no CA import has been done.
        if (tlsSkipVerify) {
            log.warn("aiops.service.tls-skip-verify=true — TLS certificate validation DISABLED (dev/lab only)");
            b = b.requestFactory(() -> {
                try {
                    SSLContext ctx = SSLContext.getInstance("TLS");
                    ctx.init(null, new TrustManager[]{new X509TrustManager() {
                        public void checkClientTrusted(X509Certificate[] c, String a) {}
                        public void checkServerTrusted(X509Certificate[] c, String a) {}
                        public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
                    }}, new SecureRandom());
                    return new SimpleClientHttpRequestFactory() {
                        @Override protected void prepareConnection(HttpURLConnection conn, String method) throws java.io.IOException {
                            if (conn instanceof javax.net.ssl.HttpsURLConnection h) {
                                h.setSSLSocketFactory(ctx.getSocketFactory());
                                h.setHostnameVerifier((host, session) -> true);
                            }
                            super.prepareConnection(conn, method);
                        }
                    };
                } catch (Exception e) {
                    throw new IllegalStateException("Failed to build skip-verify SSL context", e);
                }
            });
        }
        this.restClient = b.build();
    }

    private boolean isCircuitOpen() {
        Instant opened = openedAt.get();
        if (opened == null) return false;
        if (Instant.now().isAfter(opened.plusMillis(circuitBreakerOpenMs))) {
            // half-open: exactly ONE thread wins the probe slot via CAS.
            // If CAS fails, another thread already claimed the probe — stay "open" for this caller.
            return !openedAt.compareAndSet(opened, null);
        }
        return true;
    }

    private void recordSuccess() {
        consecutiveFailures.set(0);
        openedAt.set(null);
    }

    private void recordFailure() {
        int failures = consecutiveFailures.incrementAndGet();
        if (failures >= circuitBreakerThreshold && openedAt.get() == null) {
            openedAt.set(Instant.now());
            log.warn("AI service circuit breaker OPEN after {} consecutive failures", failures);
        }
    }

    public void learn(AILearnRequest request) {
        if (isCircuitOpen()) {
            log.debug("AI service circuit open — skipping /learn call");
            return;
        }
        try {
            restClient.post()
                    .uri("/learn")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(request)
                    .retrieve()
                    .toBodilessEntity();
            recordSuccess();
        } catch (Exception ex) {
            recordFailure();
            log.warn("AI service /learn call failed (non-critical): {}", ex.getMessage());
        }
    }

    public AIPredictResponse predict(AIPredictRequest request) {
        if (isCircuitOpen()) {
            throw new AIOpsIntegrationException(
                    "AI service circuit breaker is open — service unreachable, retry later");
        }
        try {
            AIPredictResponse response = restClient.post()
                    .uri("/predict")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(request)
                    .retrieve()
                    .body(AIPredictResponse.class);

            if (response == null) {
                throw new AIOpsIntegrationException("AI service returned empty response");
            }
            recordSuccess();
            return response;
        } catch (AIOpsIntegrationException ex) {
            recordFailure();
            throw ex;
        } catch (ResourceAccessException ex) {
            recordFailure();
            throw new AIOpsIntegrationException("AI service unreachable (timeout/connection refused)", ex);
        } catch (Exception ex) {
            recordFailure();
            throw new AIOpsIntegrationException("AI service call failed: " + ex.getMessage(), ex);
        }
    }

    public boolean isAvailable() {
        return !isCircuitOpen();
    }
}
