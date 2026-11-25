package com.orange.pfe.backend.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.dto.HostIncidentIngestionRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * LOT HELIOS-INCIDENT-PRODUCER-VERIFICATION-WIRING-01 — PRODUCTEUR réel des incidents host-mesurés.
 *
 * Lit la série {@code helios_incident{type,ns,cible}} publiée par helios-alarme (détecteur déterministe,
 * .160) et exposée par Prometheus (.160:9090), et crée/corrèle une carte via
 * {@link HostIncidentIngestionService}. La corrélation/épisode est gérée par le service d'ingestion
 * (une alerte active répétée est corrélée, pas dupliquée).
 *
 * DÉSACTIVÉ PAR DÉFAUT ({@code helios.host-ingest.enabled=false}) : ce lot n'autorise ni déploiement ni
 * appel réseau réel ; le poller ne s'exécute donc pas tant qu'il n'est pas explicitement activé dans un
 * environnement adéquat. La LOGIQUE de décision ({@link #processSignals}) est testée unitairement sans réseau.
 */
@Service
public class HostIncidentPrometheusPoller {

    private static final Logger log = LoggerFactory.getLogger(HostIncidentPrometheusPoller.class);

    /** Types host-mesurés éligibles (les incidents pod/app ne passent PAS par l'ingestion host). */
    static final Set<String> HOST_TYPES = Set.of(
            "NODE_HIGH_CPU", "NODE_HIGH_MEMORY", "NODE_DISK_FULL", "NODE_DISK_PRESSURE",
            "NODE_INODES_FULL", "NODE_LOAD_SATURATION", "NODE_CPU_STEAL", "NODE_DISK_SLOW",
            "NODE_NETWORK_ERRORS", "NODE_FD_EXHAUSTED", "NODE_PID_PRESSURE", "NODE_TIME_DRIFT",
            "NODE_OOM_KILL");

    private final HostIncidentIngestionService ingestion;
    private final ObjectMapper mapper = new ObjectMapper();

    @Value("${helios.host-ingest.enabled:false}")
    private boolean enabled;

    @Value("${helios.host-ingest.prometheus-url:http://192.168.148.160:9090}")
    private String prometheusUrl;

    public HostIncidentPrometheusPoller(HostIncidentIngestionService ingestion) {
        this.ingestion = ingestion;
    }

    /** Signal host-mesuré issu de la série Prometheus. */
    record HostSignal(String type, String ns, String cible) {}

    @Scheduled(fixedDelayString = "${helios.host-ingest.interval-ms:60000}",
               initialDelayString = "${helios.host-ingest.initial-delay-ms:120000}")
    public void poll() {
        if (!enabled) return; // désactivé par défaut : aucun appel réseau sans activation explicite
        try {
            List<HostSignal> signals = queryHostIncidents();
            int n = processSignals(signals);
            if (n > 0) log.info("Host-ingest poller: {} incident(s) host-mesure(s) ingere(s)/correle(s)", n);
        } catch (Exception e) {
            log.warn("Host-ingest poller: cycle ignore ({})", e.getMessage());
        }
    }

    /**
     * Décision d'ingestion (TESTABLE sans réseau) : pour chaque signal host-mesuré éligible, crée/corrèle
     * une carte. Les types non-host sont ignorés. L'anti-doublon/épisode est délégué à l'ingestion.
     */
    int processSignals(List<HostSignal> signals) {
        if (signals == null) return 0;
        int n = 0;
        for (HostSignal s : signals) {
            if (s == null || s.type() == null || !HOST_TYPES.contains(s.type().trim().toUpperCase())) continue;
            if (s.cible() == null || s.cible().isBlank()) continue; // pas de cible => pas de faux incident
            HostIncidentIngestionRequest req = new HostIncidentIngestionRequest(
                    s.type().trim(), s.ns(), s.cible().trim(), null,
                    null,                 // detectedAt inconnu de la métrique (scrute)
                    LocalDateTime.now(),  // observedAt = instant de scrutation
                    "via Prometheus helios_incident", null);
            ingestion.ingest(req);
            n++;
        }
        return n;
    }

    /** Lecture HTTP de la série helios_incident (chemin réseau ; non exécuté dans ce lot). */
    List<HostSignal> queryHostIncidents() throws Exception {
        String q = URLEncoder.encode("helios_incident", StandardCharsets.UTF_8);
        HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(prometheusUrl + "/api/v1/query?query=" + q))
                .timeout(Duration.ofSeconds(8)).GET().build();
        HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
        return parseVector(resp.body());
    }

    /** Parse la réponse Prometheus (resultType=vector) en signaux. TESTABLE. */
    List<HostSignal> parseVector(String body) throws Exception {
        List<HostSignal> out = new ArrayList<>();
        if (body == null || body.isBlank()) return out;
        JsonNode root = mapper.readTree(body);
        JsonNode result = root.path("data").path("result");
        if (result.isArray()) {
            for (JsonNode r : result) {
                JsonNode m = r.path("metric");
                out.add(new HostSignal(
                        m.path("type").asText(null),
                        m.path("ns").asText(null),
                        m.path("cible").asText(null)));
            }
        }
        return out;
    }
}
