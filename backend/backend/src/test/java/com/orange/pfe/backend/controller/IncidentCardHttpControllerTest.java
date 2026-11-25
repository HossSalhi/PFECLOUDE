package com.orange.pfe.backend.controller;

import com.jayway.jsonpath.JsonPath;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.JwtService;
import jakarta.servlet.Filter;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.context.WebApplicationContext;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.setup.MockMvcBuilders.webAppContextSetup;

/**
 * HELIOS — Validation locale INTÉGRÉE par le vrai contrôleur HTTP + H2 (carte d'incident).
 *
 * Trajet RÉEL : HTTP -> chaîne de filtres de sécurité RÉELLE (JwtAuthFilter) -> contrôleur -> service -> dépôts
 * JPA -> base H2 ISOLÉE/ÉPHÉMÈRE (application.properties de test ; create-drop ; Flyway off ; camunda.enabled=false ;
 * pollers désactivés par défaut -> aucun appel réseau). Chaque incident est créé par le VRAI point d'ingestion HTTP.
 *
 * `spring-security-test` étant ABSENT hors-ligne, la sécurité n'est PAS contournée : la VRAIE
 * `springSecurityFilterChain` est câblée dans MockMvc, et on s'authentifie avec un VRAI JWT (ADMIN synthétique
 * persisté). Données SYNTHÉTIQUES, aucune VM, aucune remédiation.
 */
@SpringBootTest(properties = {
        "jwt.secret=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "helios.bridge.api-key=bridge-test-secret"
})
@Transactional
class IncidentCardHttpControllerTest {

    @Autowired private WebApplicationContext wac;
    @Autowired @Qualifier("springSecurityFilterChain") private Filter springSecurityFilterChain;
    @Autowired private JwtService jwtService;
    @Autowired private AppUserRepository userRepo;

    private MockMvc mvc;
    private String bearer;

    @BeforeEach
    void setup() {
        mvc = webAppContextSetup(wac).addFilters(springSecurityFilterChain).build();
        AppUser admin = new AppUser();
        admin.setUsername("it-admin");
        admin.setEmail("it-admin@test.local");
        admin.setPassword("{noop}unused");          // auth par JWT, jamais par mot de passe
        admin.setRole(UserRole.ADMIN);
        admin.setEnabled(true);
        userRepo.save(admin);
        bearer = "Bearer " + jwtService.generateAccessToken(admin);
    }

    /** Crée un incident réel via le VRAI point d'ingestion HTTP et renvoie son id. */
    private long ingestSyntheticIncident(String target) throws Exception {
        String body = "{\"incidentType\":\"NODE_HIGH_CPU\",\"namespace\":\"k8s-nodes\",\"target\":\"" + target +
                      "\",\"severity\":\"HIGH\",\"detail\":\"synthetic fixture\"}";
        String json = mvc.perform(post("/api/incidents/ingest/host").header("Authorization", bearer)
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().is2xxSuccessful())
                .andReturn().getResponse().getContentAsString();
        Number id = JsonPath.read(json, "$.incidentId");
        return id.longValue();
    }

    // ── Sécurité réelle appliquée (anti-contournement) ──────────────────────────────
    @Test
    void card_unauthenticated_isRejected() throws Exception {
        mvc.perform(get("/api/incidents/1/card")).andExpect(result -> {
            int s = result.getResponse().getStatus();
            if (s != 401 && s != 403) throw new AssertionError("carte attendue protégée (401/403), reçu " + s);
        });
    }

    // ── Trajet complet : ingestion -> carte -> vérification RESOLVED(PASS) via HTTP ─
    @Test
    void bridgeKey_valid_allowsHostIngestionWithoutJwt() throws Exception {
        String body = "{\"incidentType\":\"NODE_HIGH_CPU\","
                + "\"namespace\":\"k8s-nodes\","
                + "\"target\":\"k8s-worker-bridge-key\","
                + "\"severity\":\"HIGH\","
                + "\"detail\":\"synthetic bridge-key fixture\"}";

        mvc.perform(post("/api/incidents/ingest/host")
                        .header("X-HELIOS-Bridge-Key", "bridge-test-secret")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.incidentId").exists());
    }

    @Test
    void bridgeKey_valid_allowsCardWriteEndpointsWithoutJwt() throws Exception {
        long inc = ingestSyntheticIncident("k8s-worker-bridge-write");
        String key = "bridge-test-secret";

        String event = "{\"stage\":\"SELECTED\","
                + "\"action\":\"HOST_SERVICE_CPU_QUOTA\","
                + "\"detail\":\"synthetic authorization fixture\"}";

        mvc.perform(post("/api/incidents/" + inc + "/execution-event")
                        .header("X-HELIOS-Bridge-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(event))
                .andExpect(status().isCreated());

        String evidence = "{\"vm\":\"k8s-worker-bridge-write\","
                + "\"phase\":\"BEFORE\",\"scenario\":\"CPU\","
                + "\"source\":\"FIXTURE\","
                + "\"outputs\":[{\"command\":\"uptime\","
                + "\"exitCode\":0,\"stdout\":\"fixture\","
                + "\"stderr\":\"\","
                + "\"capturedAt\":\"2026-10-16T12:00:00\"}]}";

        mvc.perform(post("/api/incidents/" + inc + "/evidence/collect")
                        .header("X-HELIOS-Bridge-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(evidence))
                .andExpect(status().isCreated());

        // ESCALATED ?vite de pr?senter des donn?es synth?tiques comme une r?paration r?elle.
        String verification = "{\"criteria\":\"fixture only\","
                + "\"result\":\"ESCALATED\","
                + "\"reason\":\"synthetic authorization test\","
                + "\"available\":false}";

        mvc.perform(post("/api/incidents/" + inc + "/verification")
                        .header("X-HELIOS-Bridge-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(verification))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.result").value("ESCALATED"));
    }

    @Test
    void bridgeKey_missingOrInvalid_isRejectedForIngestion() throws Exception {
        String body = "{\"incidentType\":\"NODE_HIGH_CPU\","
                + "\"namespace\":\"k8s-nodes\","
                + "\"target\":\"k8s-worker-bridge-auth-negative\","
                + "\"severity\":\"HIGH\"}";

        for (String key : new String[]{null, "wrong-test-key"}) {
            var request = post("/api/incidents/ingest/host")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(body);

            if (key != null) {
                request.header("X-HELIOS-Bridge-Key", key);
            }

            mvc.perform(request).andExpect(result -> {
                int code = result.getResponse().getStatus();
                if (code != 401 && code != 403) {
                    throw new AssertionError(
                            "Cl? absente/invalide : HTTP 401/403 attendu, re?u " + code);
                }
            });
        }
    }

    @Test
    void bridgeKey_doesNotAuthorizeReadsOrOtherWrites() throws Exception {
        String key = "bridge-test-secret";

        mvc.perform(get("/api/incidents/1/card")
                        .header("X-HELIOS-Bridge-Key", key))
                .andExpect(result -> {
                    int code = result.getResponse().getStatus();
                    if (code != 401 && code != 403) {
                        throw new AssertionError(
                                "Lecture avec cl? Bridge seule : HTTP 401/403 attendu, re?u " + code);
                    }
                });

        mvc.perform(post("/api/incidents")
                        .header("X-HELIOS-Bridge-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(result -> {
                    int code = result.getResponse().getStatus();
                    if (code != 401 && code != 403) {
                        throw new AssertionError(
                                "Route d'?criture hors p?rim?tre : HTTP 401/403 attendu, re?u " + code);
                    }
                });
    }

    @Test
    void happyPath_ingest_card_verification_resolved_viaHttp() throws Exception {
        long inc = ingestSyntheticIncident("k8s-worker1");   // réception + persistance (HTTP réel)

        // la carte de l'incident réel est servie par le vrai contrôleur
        mvc.perform(get("/api/incidents/" + inc + "/card").header("Authorization", bearer))
           .andExpect(status().isOk());

        // vérification absente au départ -> 204 (jamais présentée comme un succès)
        mvc.perform(get("/api/incidents/" + inc + "/verification").header("Authorization", bearer))
           .andExpect(status().isNoContent());

        // écriture d'un verdict PASS valide (critères + mesures réelles) -> 201
        String pass = "{\"criteria\":\"node Ready; host_cpu<90%\",\"result\":\"PASS\"," +
                      "\"measuresJson\":\"{\\\"nodeReady\\\":true,\\\"cpu\\\":12}\",\"available\":true}";
        mvc.perform(post("/api/incidents/" + inc + "/verification").header("Authorization", bearer)
                        .contentType(MediaType.APPLICATION_JSON).content(pass))
           .andExpect(status().isCreated())
           .andExpect(jsonPath("$.result").value("PASS"));

        // relecture par le vrai contrôleur -> PASS persisté
        mvc.perform(get("/api/incidents/" + inc + "/verification").header("Authorization", bearer))
           .andExpect(status().isOk())
           .andExpect(jsonPath("$.result").value("PASS"))
           .andExpect(jsonPath("$.available").value(true));
    }

    // ── Vérification ESCALATE via HTTP ───────────────────────────────────────────────
    @Test
    void verification_escalated_viaHttp() throws Exception {
        long inc = ingestSyntheticIncident("k8s-worker2");
        String esc = "{\"result\":\"ESCALATED\",\"reason\":\"aucune action sûre\",\"available\":true}";
        mvc.perform(post("/api/incidents/" + inc + "/verification").header("Authorization", bearer)
                        .contentType(MediaType.APPLICATION_JSON).content(esc))
           .andExpect(status().isCreated())
           .andExpect(jsonPath("$.result").value("ESCALATED"));
    }

    // ── Garde anti-FAUX-succès : PASS sans critères ni mesures -> EXACTEMENT 400, rien de sauvé ─
    // Depuis le correctif de GlobalExceptionHandler (ResponseStatusException -> statut porté), le service renvoie
    // bien 400 (et non plus 500). On exige le code EXACT + la NON-persistance.
    @Test
    void verification_passWithoutObservations_isBadRequest_andNotPersisted() throws Exception {
        long inc = ingestSyntheticIncident("k8s-worker1");
        String badPass = "{\"result\":\"PASS\",\"available\":true}";   // ni critères ni mesures
        mvc.perform(post("/api/incidents/" + inc + "/verification").header("Authorization", bearer)
                        .contentType(MediaType.APPLICATION_JSON).content(badPass))
           .andExpect(status().isBadRequest());                         // EXACTEMENT 400
        // rien n'a été persisté -> l'absence n'est pas devenue un faux succès
        mvc.perform(get("/api/incidents/" + inc + "/verification").header("Authorization", bearer))
           .andExpect(status().isNoContent());
    }

    // ── Incident inexistant -> EXACTEMENT 404 (grâce au correctif GlobalExceptionHandler) ─
    @Test
    void card_unknownIncident_returns404() throws Exception {
        mvc.perform(get("/api/incidents/99999999/card").header("Authorization", bearer))
           .andExpect(status().isNotFound());
    }
}
