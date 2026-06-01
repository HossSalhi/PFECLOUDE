package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.AIOpsAnalyzeResponse;
import com.orange.pfe.backend.dto.AIPredictEvidence;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.exception.AIOpsIntegrationException;
import com.orange.pfe.backend.exception.GlobalExceptionHandler;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.service.AIOpsOrchestrationService;
import com.orange.pfe.backend.service.FaultInjectionService;
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

@WebMvcTest(AIOpsController.class)
@AutoConfigureMockMvc(addFilters = false)
@Import(GlobalExceptionHandler.class)
class AIOpsControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private AIOpsOrchestrationService orchestrationService;

    @MockitoBean
    private FaultInjectionService faultInjectionService;

    @Test
    void shouldReturnPredictionWhenRequestIsValid() throws Exception {
        AIPredictEvidence evidence = new AIPredictEvidence();
        evidence.setLogScore(0.4);
        evidence.setMetricScore(0.9);
        evidence.setRuleScore(0.72);
        evidence.setModelScore(null);

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setNoise(false);
        prediction.setIncidentType("CPU_STRESS");
        prediction.setSeverity("HIGH");
        prediction.setRecommendedAction("RESTART_OR_SCALE");
        prediction.setConfidence(0.87);
        prediction.setSummary("High CPU risk pattern detected");
        prediction.setEvidence(evidence);

        AIOpsAnalyzeResponse serviceResponse = new AIOpsAnalyzeResponse();
        serviceResponse.setPrediction(prediction);
        serviceResponse.setIncidentCreated(true);
        serviceResponse.setIncidentId(101L);
        serviceResponse.setWorkflowId(55L);
        serviceResponse.setIncidentStatus(IncidentStatus.IN_PROGRESS);
        serviceResponse.setMessage("Incident created from AI prediction");

        given(orchestrationService.analyze(any())).willReturn(serviceResponse);

        mockMvc.perform(post("/api/ai/predict")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(requestBody("pfe", "backend", 5, true)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.incidentCreated").value(true))
                .andExpect(jsonPath("$.incidentId").value(101))
                .andExpect(jsonPath("$.workflowId").value(55))
                .andExpect(jsonPath("$.incidentStatus").value("IN_PROGRESS"))
                .andExpect(jsonPath("$.prediction.incident").value(true))
                .andExpect(jsonPath("$.prediction.incident_type").value("CPU_STRESS"))
                .andExpect(jsonPath("$.prediction.recommended_action").value("RESTART_OR_SCALE"));
    }

    @Test
    void shouldReturnBadRequestWhenValidationFails() throws Exception {
        mockMvc.perform(post("/api/ai/predict")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(requestBody("", "backend", 5, true)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.status").value(400))
                .andExpect(jsonPath("$.error", containsString("namespace")));

        verifyNoInteractions(orchestrationService);
    }

    @Test
    void shouldReturnBadGatewayWhenAiServiceFails() throws Exception {
        given(orchestrationService.analyze(any()))
                .willThrow(new AIOpsIntegrationException("AI service call failed"));

        mockMvc.perform(post("/api/ai/predict")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(requestBody("pfe", "backend", 5, true)))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.status").value(502))
                .andExpect(jsonPath("$.error").value("AI service call failed"));
    }

    private String requestBody(String namespace, String app, int windowMinutes, boolean createIncident) {
        return String.format(
                "{\"namespace\":\"%s\",\"app\":\"%s\",\"windowMinutes\":%d,\"createIncident\":%s}",
                namespace,
                app,
                windowMinutes,
                createIncident
        );
    }
}
