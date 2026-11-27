package com.orange.pfe.backend.service.supervision;

import com.orange.pfe.backend.dto.supervision.SupervisionDtos.DiagramEdge;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.DiagramNode;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.ElementStats;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BpmnLayoutTest {

    /** start → T1 → gateway → (T2 | T3) → end, avec une boucle T3 → T1. */
    private static final String NO_DI = """
            <?xml version="1.0" encoding="UTF-8"?>
            <bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL">
              <bpmn:process id="p">
                <bpmn:startEvent id="S" name="Début"/>
                <bpmn:serviceTask id="T1" name="Analyser"/>
                <bpmn:exclusiveGateway id="G" name="Décision"/>
                <bpmn:serviceTask id="T2" name="Réparer"/>
                <bpmn:userTask id="T3" name="Valider"/>
                <bpmn:endEvent id="E" name="Fin"/>
                <bpmn:sequenceFlow id="f1" sourceRef="S" targetRef="T1"/>
                <bpmn:sequenceFlow id="f2" sourceRef="T1" targetRef="G"/>
                <bpmn:sequenceFlow id="f3" sourceRef="G" targetRef="T2" name="ok"/>
                <bpmn:sequenceFlow id="f4" sourceRef="G" targetRef="T3" name="à vérifier"/>
                <bpmn:sequenceFlow id="f5" sourceRef="T2" targetRef="E"/>
                <bpmn:sequenceFlow id="f6" sourceRef="T3" targetRef="E"/>
                <bpmn:sequenceFlow id="f7" sourceRef="T3" targetRef="T1"/>
              </bpmn:process>
            </bpmn:definitions>
            """;

    private static final String WITH_DI = """
            <?xml version="1.0" encoding="UTF-8"?>
            <bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                              xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                              xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI">
              <bpmn:process id="p">
                <bpmn:startEvent id="S"/><bpmn:serviceTask id="T" name="Tâche"/><bpmn:endEvent id="E"/>
                <bpmn:sequenceFlow id="f1" sourceRef="S" targetRef="T"/><bpmn:sequenceFlow id="f2" sourceRef="T" targetRef="E"/>
              </bpmn:process>
              <bpmndi:BPMNDiagram><bpmndi:BPMNPlane bpmnElement="p">
                <bpmndi:BPMNShape bpmnElement="S"><dc:Bounds x="100" y="200" width="36" height="36"/></bpmndi:BPMNShape>
                <bpmndi:BPMNShape bpmnElement="T"><dc:Bounds x="200" y="180" width="100" height="80"/></bpmndi:BPMNShape>
                <bpmndi:BPMNShape bpmnElement="E"><dc:Bounds x="360" y="200" width="36" height="36"/></bpmndi:BPMNShape>
                <bpmndi:BPMNEdge bpmnElement="f1"><di:waypoint x="136" y="218"/><di:waypoint x="200" y="220"/></bpmndi:BPMNEdge>
                <bpmndi:BPMNEdge bpmnElement="f2"><di:waypoint x="300" y="220"/><di:waypoint x="360" y="218"/></bpmndi:BPMNEdge>
              </bpmndi:BPMNPlane></bpmndi:BPMNDiagram>
            </bpmn:definitions>
            """;

    private static DiagramNode node(BpmnLayout.Laid l, String id) {
        return l.nodes().stream().filter(n -> n.id().equals(id)).findFirst().orElseThrow();
    }

    private static DiagramEdge edge(BpmnLayout.Laid l, String id) {
        return l.edges().stream().filter(e -> e.id().equals(id)).findFirst().orElseThrow();
    }

    @Test
    void shapesAreClassifiedAndEveryStepIsPlaced() throws Exception {
        BpmnLayout.Laid l = BpmnLayout.build(BpmnLayout.parse(NO_DI), Map.of());

        assertThat(l.auto()).isTrue();
        assertThat(l.nodes()).hasSize(6);
        assertThat(node(l, "S").shape()).isEqualTo("EVENT_START");
        assertThat(node(l, "E").shape()).isEqualTo("EVENT_END");
        assertThat(node(l, "G").shape()).isEqualTo("GATEWAY");
        assertThat(node(l, "T1").shape()).isEqualTo("TASK");
        assertThat(l.edges()).hasSize(7);
        assertThat(l.width()).isGreaterThan(0);
        assertThat(l.height()).isGreaterThan(0);
    }

    @Test
    void theAutomaticLayoutFlowsLeftToRightAlongTheSequenceFlows() throws Exception {
        BpmnLayout.Laid l = BpmnLayout.build(BpmnLayout.parse(NO_DI), Map.of());

        assertThat(node(l, "S").x()).isLessThan(node(l, "T1").x());
        assertThat(node(l, "T1").x()).isLessThan(node(l, "G").x());
        assertThat(node(l, "G").x()).isLessThan(node(l, "T2").x());
        assertThat(node(l, "T2").x()).isLessThan(node(l, "E").x());
        // deux branches de la passerelle : même colonne, lignes différentes
        assertThat(node(l, "T2").x()).isEqualTo(node(l, "T3").x());
        assertThat(node(l, "T2").y()).isNotEqualTo(node(l, "T3").y());
    }

    @Test
    void noTwoNodesOverlap() throws Exception {
        BpmnLayout.Laid l = BpmnLayout.build(BpmnLayout.parse(NO_DI), Map.of());

        for (DiagramNode a : l.nodes()) {
            for (DiagramNode b : l.nodes()) {
                if (a != b) {
                    boolean overlap = a.x() < b.x() + b.w() && b.x() < a.x() + a.w() && a.y() < b.y() + b.h() && b.y() < a.y() + a.h();
                    assertThat(overlap).as(a.id() + " / " + b.id()).isFalse();
                }
            }
        }
    }

    @Test
    void aLoopGoesBackOverTheTopInsteadOfBreakingTheLayout() throws Exception {
        BpmnLayout.Laid l = BpmnLayout.build(BpmnLayout.parse(NO_DI), Map.of());

        DiagramEdge loop = edge(l, "f7");   // T3 → T1 : retour
        assertThat(loop.points().size()).isGreaterThanOrEqualTo(5);
        double topOfNodes = l.nodes().stream().mapToDouble(DiagramNode::y).min().orElseThrow();
        assertThat(loop.points().stream().mapToDouble(p -> p.y()).min().orElseThrow()).isLessThan(topOfNodes);
        // la boucle ne décale pas les niveaux : T1 reste avant la passerelle
        assertThat(node(l, "T1").x()).isLessThan(node(l, "G").x());
    }

    @Test
    void edgesStartAtTheSourceAndEndAtTheTarget() throws Exception {
        BpmnLayout.Laid l = BpmnLayout.build(BpmnLayout.parse(NO_DI), Map.of());

        DiagramEdge f1 = edge(l, "f1");
        DiagramNode s = node(l, "S"), t = node(l, "T1");
        assertThat(f1.points().get(0).x()).isEqualTo(s.x() + s.w());
        assertThat(f1.points().get(f1.points().size() - 1).x()).isEqualTo(t.x());
        assertThat(edge(l, "f3").label()).isEqualTo("ok");
    }

    @Test
    void statisticsAreAttachedToTheirStep() throws Exception {
        BpmnLayout.Laid l = BpmnLayout.build(BpmnLayout.parse(NO_DI), Map.of("T1", new ElementStats(10, 2, 1, 3)));

        assertThat(node(l, "T1").stats()).isEqualTo(new ElementStats(10, 2, 1, 3));
        assertThat(node(l, "T2").stats()).isEqualTo(new ElementStats(0, 0, 0, 0));
    }

    @Test
    void modelerCoordinatesAreUsedWhenTheFileHasThem() throws Exception {
        BpmnLayout.Laid l = BpmnLayout.build(BpmnLayout.parse(WITH_DI), Map.of());

        assertThat(l.auto()).isFalse();
        assertThat(node(l, "T").w()).isEqualTo(100);
        assertThat(node(l, "T").h()).isEqualTo(80);
        assertThat(node(l, "S").x()).isEqualTo(BpmnLayout.MARGIN);   // recalé dans la marge
        assertThat(edge(l, "f1").points()).hasSize(2);
    }

    @Test
    void aGraphWithOnlyCyclesStillGetsALayout() throws Exception {
        String xml = """
                <bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"><bpmn:process id="p">
                <bpmn:serviceTask id="A"/><bpmn:serviceTask id="B"/>
                <bpmn:sequenceFlow id="f1" sourceRef="A" targetRef="B"/><bpmn:sequenceFlow id="f2" sourceRef="B" targetRef="A"/>
                </bpmn:process></bpmn:definitions>""";

        BpmnLayout.Laid l = BpmnLayout.build(BpmnLayout.parse(xml), Map.of());

        assertThat(l.nodes()).hasSize(2);
        assertThat(l.edges()).hasSize(2);
    }

    @Test
    void aFlowToAnUnknownStepIsIgnored() throws Exception {
        String xml = """
                <bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"><bpmn:process id="p">
                <bpmn:serviceTask id="A"/><bpmn:sequenceFlow id="f1" sourceRef="A" targetRef="ABSENT"/>
                </bpmn:process></bpmn:definitions>""";

        BpmnLayout.Laid l = BpmnLayout.build(BpmnLayout.parse(xml), Map.of());

        assertThat(l.nodes()).hasSize(1);
        assertThat(l.edges()).isEmpty();
    }

    @Test
    void anExternalEntityIsRejectedNotExpanded() {
        String xml = "<?xml version=\"1.0\"?><!DOCTYPE x [<!ENTITY e SYSTEM \"file:///etc/passwd\">]><x>&e;</x>";

        assertThatThrownBy(() -> BpmnLayout.parse(xml)).isInstanceOf(Exception.class);
    }

    @Test
    void theLayoutIsDeterministic() throws Exception {
        BpmnLayout.Laid a = BpmnLayout.build(BpmnLayout.parse(NO_DI), Map.of());
        BpmnLayout.Laid b = BpmnLayout.build(BpmnLayout.parse(NO_DI), Map.of());

        assertThat(a.nodes()).isEqualTo(b.nodes());
        assertThat(a.edges()).isEqualTo(b.edges());
    }
}
