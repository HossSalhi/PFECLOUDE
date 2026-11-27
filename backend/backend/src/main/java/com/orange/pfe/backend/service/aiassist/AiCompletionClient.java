package com.orange.pfe.backend.service.aiassist;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.orange.pfe.backend.config.AiAssistProperties;
import com.orange.pfe.backend.service.aiassist.AiCompletionResult.Reason;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.time.Instant;

/**
 * Client HTTP générique au format OpenAI {@code chat/completions} (Mistral AI, Groq, …). Il ne fait que transformer un
 * texte en texte : l'IA ne décide rien et ne reçoit que des agrégats déjà calculés par le Backend. Aucune exception ne
 * remonte : une panne, un quota dépassé ou une clé absente donnent un {@link AiCompletionResult.Unavailable} (même
 * principe que {@code SupervisionClient}). La clé API n'est jamais journalisée ni renvoyée.
 */
@Component
public class AiCompletionClient {

    private static final Logger log = LoggerFactory.getLogger(AiCompletionClient.class);

    private final ObjectMapper mapper = new ObjectMapper();
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final AiAssistProperties props;

    public AiCompletionClient(AiAssistProperties props) {
        this.props = props;
    }

    /** Vrai si la fonction est activée ET configurée : sert à l'interface pour proposer ou non le bouton. */
    public boolean ready(AiFeature feature) {
        return notReadyReason(feature) == null;
    }

    /** Raison pour laquelle la fonction ne peut pas être utilisée, ou {@code null} si elle est prête. */
    public Reason notReadyReason(AiFeature feature) {
        if (!props.isFeatureEnabled(feature)) {
            return Reason.DISABLED;
        }
        return props.isConfigured() ? null : Reason.NOT_CONFIGURED;
    }

    /** Appel déclenché à la demande (jamais en continu) : un message système et un message utilisateur. */
    public AiCompletionResult complete(AiFeature feature, String system, String user) {
        Reason notReady = notReadyReason(feature);
        if (notReady != null) {
            return unavailable(notReady);
        }
        try {
            HttpResponse<String> r = http.send(HttpRequest.newBuilder()
                            .uri(URI.create(props.effectiveBaseUrl() + "/chat/completions"))
                            .timeout(Duration.ofSeconds(props.getTimeoutSeconds()))
                            .header("Content-Type", "application/json")
                            .header("Authorization", "Bearer " + props.getApiKey().trim())
                            .POST(HttpRequest.BodyPublishers.ofString(body(system, user)))
                            .build(),
                    HttpResponse.BodyHandlers.ofString());
            int code = r.statusCode();
            if (code == 429) {
                return unavailable(Reason.QUOTA);
            }
            if (code != 200) {
                log.warn("Assistance IA : le fournisseur a répondu HTTP {} (fonction {})", code, feature);
                return unavailable(code == 401 || code == 403 ? Reason.NOT_CONFIGURED : Reason.REJECTED);
            }
            String text = mapper.readTree(r.body()).path("choices").path(0).path("message").path("content").asText("").trim();
            if (text.isEmpty()) {
                return unavailable(Reason.INVALID_RESPONSE);
            }
            return new AiCompletionResult.Success(text, props.effectiveModel(), Instant.now());
        } catch (HttpTimeoutException e) {
            return unavailable(Reason.TIMEOUT);
        } catch (JsonProcessingException e) {
            return unavailable(Reason.INVALID_RESPONSE);
        } catch (IOException e) {
            return unavailable(Reason.UNREACHABLE);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return unavailable(Reason.UNREACHABLE);
        } catch (RuntimeException e) {
            // adresse mal formée, JSON illisible… : jamais d'erreur 500 pour une aide facultative
            log.warn("Assistance IA : réponse ou configuration inexploitable ({})", e.getClass().getSimpleName());
            return unavailable(Reason.INVALID_RESPONSE);
        }
    }

    private String body(String system, String user) throws IOException {
        ObjectNode root = mapper.createObjectNode();
        root.put("model", props.effectiveModel());
        root.put("temperature", props.getTemperature());
        root.put("max_tokens", props.getMaxTokens());
        if (props.effectiveModel().startsWith("openai/gpt-oss")) {
            // modèle à raisonnement : on limite la réflexion pour que le budget de tokens serve à la réponse (et reste économique)
            root.put("reasoning_effort", "low");
        }
        ArrayNode messages = root.putArray("messages");
        if (system != null && !system.isBlank()) {
            messages.addObject().put("role", "system").put("content", system);
        }
        messages.addObject().put("role", "user").put("content", user == null ? "" : user);
        return mapper.writeValueAsString(root);
    }

    private AiCompletionResult.Unavailable unavailable(Reason reason) {
        return new AiCompletionResult.Unavailable(reason, Instant.now());
    }
}
