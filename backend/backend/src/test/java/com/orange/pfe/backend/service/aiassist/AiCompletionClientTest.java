package com.orange.pfe.backend.service.aiassist;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.config.AiAssistProperties;
import com.orange.pfe.backend.service.aiassist.AiCompletionResult.Reason;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/** Client IA : faux fournisseur HTTP local, aucune connexion externe. */
class AiCompletionClientTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    private HttpServer server;
    private final AtomicInteger calls = new AtomicInteger();
    private final AtomicReference<String> lastAuth = new AtomicReference<>();
    private final AtomicReference<String> lastBody = new AtomicReference<>();
    private volatile int status = 200;
    private volatile String reply = "{\"choices\":[{\"message\":{\"content\":\"  Synthèse claire.  \"}}]}";
    private volatile long delayMs = 0;

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v1/chat/completions", ex -> {
            calls.incrementAndGet();
            lastAuth.set(ex.getRequestHeaders().getFirst("Authorization"));
            lastBody.set(new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            try { Thread.sleep(delayMs); } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
            byte[] out = reply.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(status, out.length);
            ex.getResponseBody().write(out);
            ex.close();
        });
        server.start();
    }

    @AfterEach
    void stop() { server.stop(0); }

    private AiAssistProperties props(boolean enabled, boolean feature, String key) {
        AiAssistProperties p = new AiAssistProperties();
        p.setEnabled(enabled);
        p.getReportsSummary().setEnabled(feature);
        p.setApiKey(key);
        p.setBaseUrl("http://127.0.0.1:" + server.getAddress().getPort() + "/v1");
        p.setModel("test-model");
        p.setTimeoutSeconds(5);
        return p;
    }

    private AiCompletionClient client(AiAssistProperties p) { return new AiCompletionClient(p); }

    private Reason reasonOf(AiCompletionResult r) {
        assertThat(r).isInstanceOf(AiCompletionResult.Unavailable.class);
        return ((AiCompletionResult.Unavailable) r).reason();
    }

    @Test
    void successReturnsTrimmedTextAndSendsBearerKeyAndOpenAiBody() throws Exception {
        AiCompletionResult r = client(props(true, true, "cle-secrete")).complete(AiFeature.REPORTS_SUMMARY, "Tu es concis.", "Chiffres : 3");

        assertThat(r).isInstanceOf(AiCompletionResult.Success.class);
        AiCompletionResult.Success ok = (AiCompletionResult.Success) r;
        assertThat(ok.text()).isEqualTo("Synthèse claire.");
        assertThat(ok.model()).isEqualTo("test-model");
        assertThat(ok.generatedAt()).isNotNull();
        assertThat(lastAuth.get()).isEqualTo("Bearer cle-secrete");
        JsonNode body = JSON.readTree(lastBody.get());
        assertThat(body.path("model").asText()).isEqualTo("test-model");
        assertThat(body.path("messages").get(0).path("role").asText()).isEqualTo("system");
        assertThat(body.path("messages").get(1).path("content").asText()).isEqualTo("Chiffres : 3");
    }

    @Test
    void reasoningModelsGetALowReasoningEffortAndOthersDoNot() throws Exception {
        AiAssistProperties gptOss = props(true, true, "k");
        gptOss.setModel("openai/gpt-oss-20b");
        client(gptOss).complete(AiFeature.REPORTS_SUMMARY, "s", "u");
        assertThat(JSON.readTree(lastBody.get()).path("reasoning_effort").asText()).isEqualTo("low");

        client(props(true, true, "k")).complete(AiFeature.REPORTS_SUMMARY, "s", "u");     // modèle « test-model »
        assertThat(JSON.readTree(lastBody.get()).has("reasoning_effort")).isFalse();
    }

    @Test
    void disabledGloballyOrPerFeatureMakesNoCall() {
        assertThat(reasonOf(client(props(false, true, "k")).complete(AiFeature.REPORTS_SUMMARY, "s", "u"))).isEqualTo(Reason.DISABLED);
        assertThat(reasonOf(client(props(true, false, "k")).complete(AiFeature.REPORTS_SUMMARY, "s", "u"))).isEqualTo(Reason.DISABLED);
        // une autre fonction non activée reste désactivée
        assertThat(reasonOf(client(props(true, true, "k")).complete(AiFeature.AUDIT_ANOMALIES, "s", "u"))).isEqualTo(Reason.DISABLED);
        assertThat(calls.get()).isZero();
    }

    @Test
    void missingKeyIsNotConfiguredAndMakesNoCall() {
        assertThat(reasonOf(client(props(true, true, "")).complete(AiFeature.REPORTS_SUMMARY, "s", "u"))).isEqualTo(Reason.NOT_CONFIGURED);
        assertThat(reasonOf(client(props(true, true, "   ")).complete(AiFeature.REPORTS_SUMMARY, "s", "u"))).isEqualTo(Reason.NOT_CONFIGURED);
        assertThat(calls.get()).isZero();
    }

    @Test
    void quotaExceededIsReportedNotThrown() {
        status = 429;
        reply = "{\"error\":\"rate limit\"}";
        assertThat(reasonOf(client(props(true, true, "k")).complete(AiFeature.REPORTS_SUMMARY, "s", "u"))).isEqualTo(Reason.QUOTA);
    }

    @Test
    void rejectedKeyAndServerErrorsAreMapped() {
        status = 401;
        assertThat(reasonOf(client(props(true, true, "k")).complete(AiFeature.REPORTS_SUMMARY, "s", "u"))).isEqualTo(Reason.NOT_CONFIGURED);
        status = 500;
        assertThat(reasonOf(client(props(true, true, "k")).complete(AiFeature.REPORTS_SUMMARY, "s", "u"))).isEqualTo(Reason.REJECTED);
    }

    @Test
    void emptyOrMalformedResponsesAreInvalidNotErrors() {
        reply = "{\"choices\":[]}";
        assertThat(reasonOf(client(props(true, true, "k")).complete(AiFeature.REPORTS_SUMMARY, "s", "u"))).isEqualTo(Reason.INVALID_RESPONSE);
        reply = "pas du json {{{";
        assertThat(reasonOf(client(props(true, true, "k")).complete(AiFeature.REPORTS_SUMMARY, "s", "u"))).isEqualTo(Reason.INVALID_RESPONSE);
    }

    @Test
    void slowProviderTimesOut() {
        delayMs = 2500;
        AiAssistProperties p = props(true, true, "k");
        p.setTimeoutSeconds(1);

        assertThat(reasonOf(client(p).complete(AiFeature.REPORTS_SUMMARY, "s", "u"))).isEqualTo(Reason.TIMEOUT);
    }

    @Test
    void unreachableProviderIsUnavailable() {
        AiAssistProperties p = props(true, true, "k");
        server.stop(0);
        assertThat(reasonOf(client(p).complete(AiFeature.REPORTS_SUMMARY, "s", "u"))).isEqualTo(Reason.UNREACHABLE);
    }

    @Test
    void invalidBaseUrlNeverThrows() {
        AiAssistProperties p = props(true, true, "k");
        p.setBaseUrl("http://[pas une adresse");

        assertThat(client(p).complete(AiFeature.REPORTS_SUMMARY, "s", "u")).isInstanceOf(AiCompletionResult.Unavailable.class);
    }

    @Test
    void theKeyNeverAppearsInAnyUnavailableMessage() {
        status = 401;
        AiCompletionResult r = client(props(true, true, "cle-ultra-secrete")).complete(AiFeature.REPORTS_SUMMARY, "s", "u");

        assertThat(((AiCompletionResult.Unavailable) r).message()).doesNotContain("cle-ultra-secrete");
        assertThat(r.toString()).doesNotContain("cle-ultra-secrete");
    }

    @Test
    void providerDefaultsAndReadiness() {
        AiAssistProperties byDefault = new AiAssistProperties();          // gratuit et rapide par défaut : Groq, petit modèle
        assertThat(byDefault.effectiveBaseUrl()).isEqualTo("https://api.groq.com/openai/v1");
        assertThat(byDefault.effectiveModel()).isEqualTo("openai/gpt-oss-20b");
        assertThat(byDefault.getMaxTokens()).isEqualTo(400);
        assertThat(byDefault.getTimeoutSeconds()).isEqualTo(10);

        AiAssistProperties mistral = new AiAssistProperties();
        mistral.setProvider("mistral");
        assertThat(mistral.effectiveBaseUrl()).isEqualTo("https://api.mistral.ai/v1");
        assertThat(mistral.effectiveModel()).isEqualTo("mistral-small-latest");

        assertThat(client(byDefault).ready(AiFeature.REPORTS_SUMMARY)).isFalse();       // tout est désactivé par défaut
        assertThat(client(props(true, true, "k")).ready(AiFeature.REPORTS_SUMMARY)).isTrue();
    }
}
