package com.orange.pfe.backend.service.supervision;

import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaIncidents;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaInstances;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaOverview;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaSteps;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Vue Camunda contre un FAUX Camunda (vrai HTTP local) : lecture seule, dégradation propre, protection XXE. */
class CamundaSupervisionServiceTest {

    private static final String BPMN = """
            <?xml version="1.0" encoding="UTF-8"?>
            <bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL">
              <bpmn:process id="p1">
                <bpmn:startEvent id="S" name="Incident détecté"/>
                <bpmn:serviceTask id="T1" name="Analyser"/>
                <bpmn:exclusiveGateway id="G"/>
                <bpmn:userTask id="T2" name="Valider"/>
                <bpmn:endEvent id="E" name="Terminé"/>
                <bpmn:sequenceFlow id="F1" sourceRef="S" targetRef="T1"/>
              </bpmn:process>
            </bpmn:definitions>
            """;

    private HttpServer server;
    private final List<String> received = new ArrayList<>();
    private CamundaSupervisionService service;

    private static String page(long total, String items) {
        return "{\"page\":{\"totalItems\":" + total + "},\"items\":[" + items + "]}";
    }

    private String respond(String method, String path, String body) {
        if (path.equals("/v2/topology")) {
            return "{\"brokers\":[{\"partitions\":[{\"health\":\"healthy\"}]}],\"clusterSize\":1,\"gatewayVersion\":\"8.9.0\"}";
        }
        if (path.equals("/v2/process-instances/search")) {
            if (body.contains("\"state\":\"ACTIVE\"") && body.contains("\"limit\":1}")) return page(142, "");
            if (body.contains("\"state\":\"COMPLETED\"") && body.contains("\"limit\":1}")) return page(6942, "");
            if (body.contains("\"state\":\"TERMINATED\"")) return page(0, "");
            return page(7084, "{\"processInstanceKey\":\"2251799826967014\",\"processDefinitionKey\":\"2251799824503656\","
                    + "\"processDefinitionId\":\"helios-camunda-full-cycle\",\"processDefinitionName\":\"Cycle complet\",\"processDefinitionVersion\":22,"
                    + "\"state\":\"COMPLETED\",\"hasIncident\":false,\"startDate\":\"2026-11-27T02:15:00.809Z\",\"endDate\":\"2026-11-27T02:15:02.035Z\"}");
        }
        if (path.equals("/v2/incidents/search")) {
            if (body.contains("\"limit\":1}")) return page(141, "");
            return page(141, "{\"incidentKey\":\"9\",\"processDefinitionId\":\"incident-remediation\",\"processInstanceKey\":\"7\","
                    + "\"errorType\":\"JOB_NO_RETRIES\",\"errorMessage\":\"" + "x".repeat(400) + "\",\"elementId\":\"Task_Exec\","
                    + "\"creationTime\":\"2026-10-15T10:23:39.329Z\"}");
        }
        if (path.equals("/v2/process-definitions/search")) {
            return page(1, "{\"processDefinitionKey\":\"55\",\"processDefinitionId\":\"helios-camunda-full-cycle\",\"name\":\"Cycle complet\",\"version\":22}");
        }
        if (path.equals("/v2/process-definitions/55/xml")) {
            return BPMN;
        }
        if (path.equals("/v2/process-definitions/66/xml")) {
            return "<?xml version=\"1.0\"?><!DOCTYPE x [<!ENTITY e SYSTEM \"file:///etc/passwd\">]><x>&e;</x>";
        }
        return "{}";
    }

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            synchronized (received) {
                received.add(ex.getRequestMethod() + " " + ex.getRequestURI().getPath());
            }
            byte[] out = respond(ex.getRequestMethod(), ex.getRequestURI().getPath(), body).getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(200, out.length);
            ex.getResponseBody().write(out);
            ex.close();
        });
        server.start();
        service = new CamundaSupervisionService();
        ReflectionTestUtils.setField(service, "restAddress", "http://127.0.0.1:" + server.getAddress().getPort());
        ReflectionTestUtils.setField(service, "username", "");
        ReflectionTestUtils.setField(service, "password", "");
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    @Test
    void overviewGivesEngineHealthCountsAndDefinitions() {
        CamundaOverview o = service.overview();

        assertThat(o.available()).isTrue();
        assertThat(o.healthy()).isTrue();
        assertThat(o.version()).isEqualTo("8.9.0");
        assertThat(o.active()).isEqualTo(142);
        assertThat(o.completed()).isEqualTo(6942);
        assertThat(o.terminated()).isZero();
        assertThat(o.incidents()).isEqualTo(141);
        assertThat(o.definitions()).singleElement().satisfies(d -> {
            assertThat(d.key()).isEqualTo("55");
            assertThat(d.version()).isEqualTo(22);
        });
    }

    @Test
    void instancesAreListedWithTheirDuration() {
        CamundaInstances i = service.instances("completed", 500);

        assertThat(i.available()).isTrue();
        assertThat(i.total()).isEqualTo(7084);
        assertThat(i.items()).singleElement().satisfies(x -> {
            assertThat(x.state()).isEqualTo("COMPLETED");
            assertThat(x.durationMs()).isEqualTo(1226L);
            assertThat(x.definitionName()).isEqualTo("Cycle complet");
        });
    }

    @Test
    void anUnknownStateIsRefused() {
        assertThatThrownBy(() -> service.instances("'; DROP", 10)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void incidentMessagesAreTruncated() {
        CamundaIncidents i = service.incidents(10);

        assertThat(i.total()).isEqualTo(141);
        assertThat(i.items().get(0).errorMessage().length()).isLessThanOrEqualTo(300);
        assertThat(i.items().get(0).errorType()).isEqualTo("JOB_NO_RETRIES");
    }

    @Test
    void stepsAreReadFromTheBpmnInOrder() {
        CamundaSteps s = service.steps("55");

        assertThat(s.available()).isTrue();
        assertThat(s.steps()).extracting(x -> x.name()).containsExactly("Incident détecté", "Analyser", "G", "Valider", "Terminé");
        assertThat(s.steps()).extracting(x -> x.kind()).containsExactly("Début", "Tâche automatique", "Choix exclusif", "Tâche humaine", "Fin");
    }

    @Test
    void aMaliciousBpmnWithAnExternalEntityIsRejectedNotExpanded() {
        CamundaSteps s = service.steps("66");

        assertThat(s.available()).isFalse();
        assertThat(s.error()).contains("inexploitable");
    }

    @Test
    void anInvalidProcessKeyIsRefusedBeforeAnyRequest() {
        assertThatThrownBy(() -> service.steps("../../admin")).isInstanceOf(IllegalArgumentException.class);
        synchronized (received) {
            assertThat(received).isEmpty();
        }
    }

    @Test
    void onlyReadRequestsAreSentToCamunda() {
        service.overview();
        service.instances(null, 5);
        service.incidents(5);
        service.steps("55");

        synchronized (received) {
            assertThat(received).isNotEmpty().allSatisfy(r -> {
                assertThat(r).matches("(GET /v2/(topology|process-definitions/\\d+/xml))|(POST /v2/[a-z-]+/search)");
            });
        }
    }

    @Test
    void whenCamundaIsDownEverySectionAnswersUnavailable() {
        server.stop(0);

        assertThat(service.overview().available()).isFalse();
        assertThat(service.instances(null, 5).available()).isFalse();
        assertThat(service.incidents(5).available()).isFalse();
        assertThat(service.steps("55").available()).isFalse();
    }

    @Test
    void anUnconfiguredCamundaIsReportedAsSuch() {
        ReflectionTestUtils.setField(service, "restAddress", "");

        assertThat(service.overview().error()).contains("pas configuré");
    }
}
