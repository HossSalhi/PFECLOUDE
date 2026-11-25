package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

@Component
public class RestTemplateBridgeClient implements BridgeClient {

    private static final Logger logger = LoggerFactory.getLogger(RestTemplateBridgeClient.class);

    private static final String TOKEN_HEADER = "X-Helios-Token";

    // DISK-E2E-158-01 : valeurs canoniques du seul scenario disque autorise (miroir des constantes du Bridge).
    private static final String DISK_RECLAIM_ACTION = "HOST_RECLAIM_DISK";
    private static final String DISK_INCIDENT_TYPE = "NODE_DISK_FULL";
    private static final String DISK_NAMESPACE = "k8s-nodes";
    private static final String DISK_TARGET = "k8s-worker1";
    private static final String DISK_SCENARIO = "DISK_E2E_158";

    private final RemediationProperties properties;
    private final RestTemplate restTemplate;

    public RestTemplateBridgeClient(
            RemediationProperties properties,
            @Qualifier("heliosBridgeRestTemplate") RestTemplate restTemplate) {
        this.properties = properties;
        this.restTemplate = restTemplate;
    }

    @Override
    public BridgeResponse sendAction(
            String action,
            String incidentType,
            String namespace,
            String target,
            String node) {

        if (!properties.isBridgeEnabled()) {
            return failure("Bridge desactive.");
        }
        if (isBlank(properties.getBridgeUrl()) || isBlank(properties.getBridgeToken())) {
            return failure("Configuration du Bridge incomplete.");
        }
        if ("HOST_SERVICE_CPU_QUOTA".equals(action)) {
            return failure("Action CPU quota exige service, quota_pct et duree_s explicites.");
        }
        if (DISK_RECLAIM_ACTION.equals(action)) {
            return failure("Action DISK exige le scenario et l'incident explicites.");
        }
        return sendPayload(basePayload(action, incidentType, namespace, target, node));
    }

    /**
     * DISK-E2E-158-01 : seule voie vers HOST_RECLAIM_DISK. Le client refuse tout ce qui n'est pas exactement
     * (HOST_RECLAIM_DISK, NODE_DISK_FULL, k8s-nodes, k8s-worker1, DISK_E2E_158) avec un incident identifie ; il n'envoie
     * que des valeurs canoniques (jamais celles de l'appelant). Le Bridge revalide tout et decide seul des fichiers.
     */
    @Override
    public BridgeResponse sendDiskReclaimAction(
            String action,
            String incidentType,
            String namespace,
            String target,
            String node,
            String scenario,
            Long incidentId) {

        // DISK-E2E-158-02 : seul le drapeau DEDIE du scenario ouvre cette voie ; l'ancien drapeau global ne suffit pas.
        if (!properties.isHostDiskE2e158Enabled()) {
            return failure("Scenario DISK_E2E_158 desactivee (HOST_DISK_E2E_158_ENABLED).");
        }
        if (!properties.isBridgeEnabled()) {
            return failure("Bridge desactive.");
        }
        if (isBlank(properties.getBridgeUrl()) || isBlank(properties.getBridgeToken())) {
            return failure("Configuration du Bridge incomplete.");
        }
        if (!DISK_RECLAIM_ACTION.equals(action)) {
            return failure("Action disque non autorisee.");
        }
        if (!DISK_INCIDENT_TYPE.equalsIgnoreCase(incidentType)) {
            return failure("Type incident disque non autorise.");
        }
        if (!DISK_NAMESPACE.equalsIgnoreCase(namespace)) {
            return failure("Namespace cible disque non autorise.");
        }
        if (!DISK_TARGET.equalsIgnoreCase(target) || !DISK_TARGET.equalsIgnoreCase(node)) {
            return failure("Le nettoyage disque est limite au worker laboratoire k8s-worker1.");
        }
        if (!DISK_SCENARIO.equals(scenario)) {
            return failure("Scenario disque non autorise.");
        }
        if (incidentId == null || incidentId <= 0L) {
            return failure("Identifiant d'incident disque requis.");
        }

        Map<String, Object> payload = basePayload(
                DISK_RECLAIM_ACTION, DISK_INCIDENT_TYPE, DISK_NAMESPACE, DISK_TARGET, DISK_TARGET);
        payload.put("scenario", DISK_SCENARIO);
        payload.put("incident_id", String.valueOf(incidentId));
        return sendPayload(payload);
    }

    @Override
    public BridgeResponse sendAction(
            String action,
            String incidentType,
            String namespace,
            String target,
            String node,
            String service,
            int quotaPct,
            int durationSeconds) {

        if (!properties.isBridgeEnabled()) {
            return failure("Bridge desactive.");
        }
        if (isBlank(properties.getBridgeUrl()) || isBlank(properties.getBridgeToken())) {
            return failure("Configuration du Bridge incomplete.");
        }
        if (!"HOST_SERVICE_CPU_QUOTA".equals(action)) {
            return failure("Les parametres quota ne sont autorises que pour HOST_SERVICE_CPU_QUOTA.");
        }
        if (!"helios-cpuload.service".equals(service)) {
            return failure("Service CPU non autorise par le client backend.");
        }
        if (quotaPct < 20 || quotaPct > 400) {
            return failure("Quota CPU hors limites du Bridge (20 a 400 pour cent).");
        }
        if (durationSeconds < 1 || durationSeconds > 3600) {
            return failure("Duree du quota hors limites du Bridge (1 a 3600 secondes).");
        }

        Map<String, Object> payload = basePayload(
                action, incidentType, namespace, target, node);
        payload.put("service", service);
        payload.put("quota_pct", quotaPct);
        payload.put("duree_s", durationSeconds);
        return sendPayload(payload);
    }

    @Override
    public BridgeResponse sendMemoryLimitAction(
            String action,
            String incidentType,
            String namespace,
            String target,
            String node,
            String service,
            int memoryLimitMb,
            int durationSeconds) {

        if (!properties.isHostMemoryRemediationEnabled()) {
            return failure("Remediation RAM desactivee.");
        }
        if (!properties.isBridgeEnabled()) {
            return failure("Bridge desactive.");
        }
        if (isBlank(properties.getBridgeUrl()) || isBlank(properties.getBridgeToken())) {
            return failure("Configuration du Bridge incomplete.");
        }
        if (!"HOST_SERVICE_MEMORY_LIMIT".equals(action)) {
            return failure("Action memoire non autorisee.");
        }
        if (!"NODE_HIGH_MEMORY".equalsIgnoreCase(incidentType)
                && !"NODE_MEMORY_PRESSURE".equalsIgnoreCase(incidentType)) {
            return failure("Type incident memoire non autorise.");
        }
        if (!"k8s-nodes".equalsIgnoreCase(namespace)) {
            return failure("Namespace cible RAM non autorise.");
        }
        if (!"k8s-worker1".equalsIgnoreCase(target)
                || !"k8s-worker1".equalsIgnoreCase(node)) {
            return failure("La RAM est limitee au worker laboratoire k8s-worker1.");
        }
        if (!"helios-memload.service".equals(service)) {
            return failure("Service memoire non autorise.");
        }
        if (memoryLimitMb < 128 || memoryLimitMb > 512) {
            return failure("Limite memoire hors bornes backend (128 a 512 Mo).");
        }
        if (durationSeconds < 60 || durationSeconds > 600) {
            return failure("Duree memoire hors bornes backend (60 a 600 secondes).");
        }

        Map<String, Object> payload = basePayload(
                action, incidentType, namespace, target, node);
        payload.put("service", service);
        payload.put("memory_limit_mb", memoryLimitMb);
        payload.put("duree_s", durationSeconds);
        return sendPayload(payload);
    }

    private static Map<String, Object> basePayload(
            String action, String incidentType, String namespace, String target, String node) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("action", action);
        payload.put("type", incidentType);
        payload.put("ns", namespace);
        payload.put("cible", target);
        payload.put("noeud", node);
        return payload;
    }

    private BridgeResponse sendPayload(Map<String, Object> payload) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setAccept(List.of(MediaType.APPLICATION_JSON));
        headers.set(TOKEN_HEADER, properties.getBridgeToken());

        try {
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    properties.getBridgeUrl(),
                    HttpMethod.POST,
                    new HttpEntity<>(payload, headers),
                    new ParameterizedTypeReference<Map<String, Object>>() {
                    });

            if (!response.getStatusCode().is2xxSuccessful()) {
                return failure("Le Bridge a retourne un statut HTTP inattendu.");
            }

            Map<String, Object> body = response.getBody();
            if (body == null) {
                return failure("Reponse du Bridge vide.");
            }

            Object okValue = body.get("ok");
            Object executeValue = body.get("execute");
            Object exitCodeValue = body.get("exitCode");

            if (!(okValue instanceof Boolean)
                    || !(executeValue instanceof Boolean)
                    || !(exitCodeValue instanceof Number)) {
                return failure("Format de reponse Bridge invalide.");
            }

            int exitCode = ((Number) exitCodeValue).intValue();
            boolean ok = (Boolean) okValue && exitCode == 0;
            boolean execute = (Boolean) executeValue;
            // CPU-BRIDGE-ERRPROP-01 : les reponses d'echec du Bridge (ex. quota refuse a
            // l'etape "systemd_control", "verification_cible", "application"...) ne portent PAS
            // de champ "resume" mais "stderr" et/ou "detail". On remonte donc la premiere cause
            // non vide pour ne plus masquer l'erreur reelle derriere un message generique.
            String message = firstNonBlank(
                    asText(body.get("resume")),
                    asText(body.get("stderr")),
                    asText(body.get("detail")),
                    asText(body.get("stdout")));

            return new BridgeResponse(ok, execute, exitCode, message);

        } catch (HttpStatusCodeException ex) {
            logger.warn("Bridge HTTP failure: status={}", ex.getStatusCode().value());
            return failure("Erreur de communication avec le Bridge.");
        } catch (ResourceAccessException ex) {
            Throwable cause = ex.getMostSpecificCause();
            String causeType = cause != null
                    ? cause.getClass().getSimpleName()
                    : ex.getClass().getSimpleName();
            logger.warn("Bridge transport failure: causeType={}", causeType);
            return failure("Erreur de communication avec le Bridge.");
        } catch (RestClientException ex) {
            logger.warn("Bridge client failure: exceptionType={}",
                    ex.getClass().getSimpleName());
            return failure("Erreur de communication avec le Bridge.");
        } catch (IllegalArgumentException ex) {
            logger.warn("Bridge request configuration failure: exceptionType={}",
                    ex.getClass().getSimpleName());
            return failure("Erreur de communication avec le Bridge.");
        }
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    /** CPU-BRIDGE-ERRPROP-01 : rend une chaine (String telle quelle, sinon valueOf) ou null. */
    private static String asText(Object value) {
        if (value == null) {
            return null;
        }
        return value instanceof String ? (String) value : String.valueOf(value);
    }

    /** CPU-BRIDGE-ERRPROP-01 : premiere valeur non vide, "" sinon. */
    private static String firstNonBlank(String... values) {
        if (values != null) {
            for (String v : values) {
                if (!isBlank(v)) {
                    return v;
                }
            }
        }
        return "";
    }

    private static BridgeResponse failure(String message) {
        return new BridgeResponse(false, false, -1, message);
    }
}