package com.orange.pfe.backend.service.supervision;

import com.orange.pfe.backend.dto.supervision.SupervisionDtos.ProcessDiagram;
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

/** Diagramme d'un processus Camunda contre un FAUX Camunda : étapes, compteurs, absence de statistiques, lecture seule. */
class CamundaDiagramTest {

    private static final String BPMN = """
            <?xml version="1.0" encoding="UTF-8"?>
            <bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL">
              <bpmn:process id="p">
                <bpmn:startEvent id="S" name="Début"/><bpmn:serviceTask id="T" name="Exécuter"/><bpmn:endEvent id="E" name="Fin"/>
                <bpmn:sequenceFlow id="f1" sourceRef="S" targetRef="T"/><bpmn:sequenceFlow id="f2" sourceRef="T" targetRef="E"/>
              </bpmn:process>
            </bpmn:definitions>
            """;

    private HttpServer server;
    private final List<String> received = new ArrayList<>();
    private volatile boolean statsFail;
    private CamundaSupervisionService service;

    @BeforeEach
    void start() throws IOException {
        statsFail = false;
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            String path = ex.getRequestURI().getPath();
            synchronized (received) {
                received.add(ex.getRequestMethod() + " " + path);
            }
            ex.getRequestBody().readAllBytes();
            String body;
            int status = 200;
            if (path.equals("/v2/process-definitions/55")) {
                body = "{\"name\":\"Cycle complet\",\"version\":22,\"processDefinitionId\":\"full-cycle\"}";
            } else if (path.equals("/v2/process-definitions/55/xml")) {
                body = BPMN;
            } else if (path.equals("/v2/process-definitions/55/statistics/element-instances")) {
                if (statsFail) {
                    status = 500;
                    body = "{}";
                } else {
                    body = "{\"items\":[{\"elementId\":\"T\",\"active\":2,\"canceled\":1,\"incidents\":3,\"completed\":100}]}";
                }
            } else {
                status = 404;
                body = "{}";
            }
            byte[] out = body.getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(status, out.length);
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
    void theDiagramHasTheStepsTheFlowsAndTheCountersOfEachStep() {
        ProcessDiagram d = service.diagram("55");

        assertThat(d.available()).isTrue();
        assertThat(d.name()).isEqualTo("Cycle complet");
        assertThat(d.version()).isEqualTo(22);
        assertThat(d.autoLayout()).isTrue();
        assertThat(d.nodes()).extracting(n -> n.id()).containsExactlyInAnyOrder("S", "T", "E");
        assertThat(d.edges()).hasSize(2);
        var task = d.nodes().stream().filter(n -> n.id().equals("T")).findFirst().orElseThrow();
        assertThat(task.stats().completed()).isEqualTo(100);
        assertThat(task.stats().active()).isEqualTo(2);
        assertThat(task.stats().incidents()).isEqualTo(3);
        assertThat(d.width()).isGreaterThan(0);
    }

    @Test
    void withoutStatisticsTheDiagramIsStillDrawnWithZeroCounters() {
        statsFail = true;

        ProcessDiagram d = service.diagram("55");

        assertThat(d.available()).isTrue();
        assertThat(d.nodes()).allSatisfy(n -> assertThat(n.stats().completed()).isZero());
    }

    @Test
    void anUnknownProcessIsReportedUnavailableNotAnError() {
        ProcessDiagram d = service.diagram("999");

        assertThat(d.available()).isFalse();
        assertThat(d.error()).isNotBlank();
        assertThat(d.nodes()).isEmpty();
    }

    @Test
    void anInvalidKeyNeverReachesCamunda() {
        assertThatThrownBy(() -> service.diagram("../x")).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.diagram(null)).isInstanceOf(IllegalArgumentException.class);
        synchronized (received) {
            assertThat(received).isEmpty();
        }
    }

    @Test
    void onlyReadRequestsAreSent() {
        service.diagram("55");

        synchronized (received) {
            assertThat(received).isNotEmpty().allSatisfy(r -> assertThat(r).matches(
                    "(GET /v2/process-definitions/55(/xml)?)|(POST /v2/process-definitions/55/statistics/element-instances)"));
        }
    }

    @Test
    void whenCamundaIsDownTheAnswerIsUnavailable() {
        server.stop(0);

        assertThat(service.diagram("55").available()).isFalse();
    }
}
