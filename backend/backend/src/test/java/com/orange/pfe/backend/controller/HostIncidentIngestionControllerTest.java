package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.HostIncidentIngestionResponse;
import com.orange.pfe.backend.exception.GlobalExceptionHandler;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.FaultInjectionService;
import com.orange.pfe.backend.service.HostIncidentIngestionService;
import com.orange.pfe.backend.service.JwtService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.hamcrest.Matchers.containsString;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * HELIOS — CONTRAT HTTP de l'ingestion host-mesurée {@code POST /api/incidents/ingest/host}.
 *
 * <p>Confirme ce qui, avant ce lot, n'était vérifié qu'au niveau service : une requête d'incident CPU
 * VALIDE est ACCEPTÉE (201 nouvel épisode / 200 corrélé) avec un mapping JSON correct, et une requête
 * sans {@code incidentType}/{@code target} est rejetée (400) AVANT tout appel au service. Le service
 * est mocké : ce test isole la responsabilité du contrôleur (validation {@code @Valid} + mapping
 * {@code created}→201/200 + sérialisation JSON). La logique d'ingestion/corrélation et le déclenchement
 * de la remédiation sont couverts par {@code HostIncidentIngestionServiceTest} et
 * {@code HostIncidentDispatchRoutingTest} ; AUCUNE exécution réelle ni charge CPU n'est déclenchée ici.
 */
@WebMvcTest(HostIncidentIngestionController.class)
@AutoConfigureMockMvc(addFilters = false)
@Import(GlobalExceptionHandler.class)
class HostIncidentIngestionControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private HostIncidentIngestionService ingestionService;

    // Même raison que AIOpsControllerTest : le slice @WebMvcTest instancie le vrai Filter JwtAuthFilter
    // (sécurité NON supprimée au niveau du contexte) ; ses collaborateurs hors tranche web sont fournis
    // en mocks pour que le contexte se charge. addFilters=false isole ce test au contrôleur.
    @MockitoBean
    private JwtService jwtService;

    @MockitoBean
    private AppUserRepository appUserRepository;

    // Le slice web instancie aussi le Filter faultInjectionFilter (FaultInjectionService requis).
    @MockitoBean
    private FaultInjectionService faultInjectionService;

    @Test
    void validNewCpuIncidentIsAcceptedWith201() throws Exception {
        given(ingestionService.ingest(any())).willReturn(
                new HostIncidentIngestionResponse(4242L, "NODE_HIGH_CPU", "k8s-worker1",
                        true, false, "OPEN"));

        mockMvc.perform(post("/api/incidents/ingest/host")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"incidentType\":\"NODE_HIGH_CPU\",\"target\":\"k8s-worker1\","
                                + "\"namespace\":\"k8s-nodes\",\"severity\":\"HIGH\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.incidentId").value(4242))
                .andExpect(jsonPath("$.incidentType").value("NODE_HIGH_CPU"))
                .andExpect(jsonPath("$.target").value("k8s-worker1"))
                .andExpect(jsonPath("$.created").value(true))
                .andExpect(jsonPath("$.correlated").value(false))
                .andExpect(jsonPath("$.status").value("OPEN"));
    }

    @Test
    void correlatedCpuIncidentIsAcceptedWith200() throws Exception {
        given(ingestionService.ingest(any())).willReturn(
                new HostIncidentIngestionResponse(4242L, "NODE_HIGH_CPU", "k8s-worker1",
                        false, true, "IN_PROGRESS"));

        mockMvc.perform(post("/api/incidents/ingest/host")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"incidentType\":\"NODE_HIGH_CPU\",\"target\":\"k8s-worker1\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.created").value(false))
                .andExpect(jsonPath("$.correlated").value(true))
                .andExpect(jsonPath("$.status").value("IN_PROGRESS"));
    }

    @Test
    void blankIncidentTypeIsRejectedWith400AndServiceUntouched() throws Exception {
        mockMvc.perform(post("/api/incidents/ingest/host")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"incidentType\":\"\",\"target\":\"k8s-worker1\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.status").value(400))
                .andExpect(jsonPath("$.error", containsString("incidentType")));

        verifyNoInteractions(ingestionService);
    }

    @Test
    void missingTargetIsRejectedWith400AndServiceUntouched() throws Exception {
        mockMvc.perform(post("/api/incidents/ingest/host")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"incidentType\":\"NODE_HIGH_CPU\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.status").value(400))
                .andExpect(jsonPath("$.error", containsString("target")));

        verifyNoInteractions(ingestionService);
    }
}
