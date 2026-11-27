package com.orange.pfe.backend.service.supervision;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.config.InfraHealthProperties;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Client HTTP en LECTURE SEULE de Prometheus et de Loki (requêtes GET sur l'API de consultation uniquement). Les adresses
 * viennent de la configuration existante {@code infra.health.*-url}. Aucune requête n'est fournie par le navigateur :
 * seul {@link SupervisionService} construit les requêtes, à partir de modèles fixes.
 */
@Component
public class SupervisionClient {

    /** Un outil de supervision ne répond pas, répond en erreur ou renvoie un contenu inattendu. */
    public static class UnavailableException extends RuntimeException {
        public UnavailableException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    private static final Duration CONNECT = Duration.ofSeconds(3);
    private static final Duration READ = Duration.ofSeconds(20);

    private final ObjectMapper mapper = new ObjectMapper();
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(CONNECT).build();
    private final InfraHealthProperties infra;

    public SupervisionClient(InfraHealthProperties infra) {
        this.infra = infra;
    }

    /** {@code /api/v1/query} (valeur instantanée) : renvoie {@code data.result}. */
    public JsonNode promInstant(String promql) {
        return get(infra.getPrometheusUrl(), "/api/v1/query", params("query", promql), "Prometheus");
    }

    /** {@code /api/v1/query_range} : renvoie {@code data.result} (matrice). */
    public JsonNode promRange(String promql, long startSec, long endSec, long stepSec) {
        return get(infra.getPrometheusUrl(), "/api/v1/query_range",
                params("query", promql, "start", String.valueOf(startSec), "end", String.valueOf(endSec), "step", String.valueOf(stepSec)),
                "Prometheus");
    }

    /** {@code /loki/api/v1/query} (requête de métrique instantanée) : renvoie {@code data.result}. */
    public JsonNode lokiInstant(String logql, long timeSec) {
        return get(infra.getLokiUrl(), "/loki/api/v1/query",
                params("query", logql, "time", timeSec + "000000000"), "Loki");
    }

    /** {@code /loki/api/v1/query_range} : métrique (matrice) ou lignes (flux) selon la requête. */
    public JsonNode lokiRange(String logql, long startSec, long endSec, long stepSec, int limit) {
        Map<String, String> p = params("query", logql, "start", startSec + "000000000", "end", endSec + "000000000",
                "direction", "backward");
        if (stepSec > 0) {
            p.put("step", String.valueOf(stepSec));
        }
        if (limit > 0) {
            p.put("limit", String.valueOf(limit));
        }
        return get(infra.getLokiUrl(), "/loki/api/v1/query_range", p, "Loki");
    }

    private Map<String, String> params(String... kv) {
        Map<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            m.put(kv[i], kv[i + 1]);
        }
        return m;
    }

    private JsonNode get(String base, String path, Map<String, String> params, String tool) {
        if (base == null || base.isBlank()) {
            throw new UnavailableException(tool + " n'est pas configuré", null);
        }
        StringBuilder q = new StringBuilder();
        params.forEach((k, v) -> q.append(q.length() == 0 ? '?' : '&').append(k).append('=')
                .append(URLEncoder.encode(v, StandardCharsets.UTF_8)));
        String root = base.replaceAll("/+$", "");
        try {
            HttpResponse<String> r = http.send(HttpRequest.newBuilder().uri(URI.create(root + path + q))
                    .timeout(READ).GET().build(), HttpResponse.BodyHandlers.ofString());
            if (r.statusCode() != 200) {
                throw new UnavailableException(tool + " a répondu HTTP " + r.statusCode(), null);
            }
            JsonNode body = mapper.readTree(r.body());
            if (!"success".equals(body.path("status").asText())) {
                throw new UnavailableException(tool + " a refusé la requête", null);
            }
            return body.path("data").path("result");
        } catch (UnavailableException e) {
            throw e;
        } catch (IOException e) {
            throw new UnavailableException(tool + " est injoignable", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new UnavailableException(tool + " : requête interrompue", e);
        } catch (RuntimeException e) {
            throw new UnavailableException(tool + " : réponse inexploitable", e);
        }
    }
}
