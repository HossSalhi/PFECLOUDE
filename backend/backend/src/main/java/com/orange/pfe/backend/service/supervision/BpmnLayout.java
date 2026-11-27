package com.orange.pfe.backend.service.supervision;

import com.orange.pfe.backend.dto.supervision.SupervisionDtos.DiagramEdge;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.DiagramNode;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.DiagramPoint;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.ElementStats;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Transforme un fichier BPMN en diagramme dessinable : formes et flèches avec leurs coordonnées. Si le fichier contient les
 * coordonnées du modeleur (BPMNDI) elles sont reprises ; sinon la disposition est calculée de gauche à droite, par niveaux.
 * Le XML est lu SANS entités externes ni DTD (protection XXE). Aucune dépendance supplémentaire.
 */
final class BpmnLayout {

    static final double MARGIN = 30;
    static final double COL = 210;
    static final double ROW = 104;

    record Parsed(List<Node0> nodes, List<Flow> flows, Map<String, double[]> bounds, Map<String, List<double[]>> waypoints) {}

    record Node0(String id, String type, String shape, String name) {}

    record Flow(String id, String from, String to, String label) {}

    record Laid(List<DiagramNode> nodes, List<DiagramEdge> edges, double width, double height, boolean auto) {}

    private static final Map<String, String> SHAPES = Map.ofEntries(
            Map.entry("startEvent", "EVENT_START"), Map.entry("endEvent", "EVENT_END"),
            Map.entry("intermediateCatchEvent", "EVENT"), Map.entry("intermediateThrowEvent", "EVENT"), Map.entry("boundaryEvent", "EVENT"),
            Map.entry("exclusiveGateway", "GATEWAY"), Map.entry("parallelGateway", "GATEWAY"), Map.entry("inclusiveGateway", "GATEWAY"),
            Map.entry("eventBasedGateway", "GATEWAY"), Map.entry("subProcess", "SUBPROCESS"), Map.entry("serviceTask", "TASK"),
            Map.entry("userTask", "TASK"), Map.entry("scriptTask", "TASK"), Map.entry("sendTask", "TASK"), Map.entry("receiveTask", "TASK"),
            Map.entry("businessRuleTask", "TASK"), Map.entry("manualTask", "TASK"), Map.entry("task", "TASK"), Map.entry("callActivity", "TASK"));

    private BpmnLayout() {
    }

    // ── Lecture du XML ────────────────────────────────────────────────────────

    static Parsed parse(String xml) throws Exception {
        DocumentBuilderFactory f = DocumentBuilderFactory.newInstance();
        f.setNamespaceAware(true);
        f.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
        f.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        f.setXIncludeAware(false);
        f.setExpandEntityReferences(false);
        Document doc = f.newDocumentBuilder().parse(new ByteArrayInputStream(xml.getBytes(StandardCharsets.UTF_8)));

        List<Node0> nodes = new ArrayList<>();
        List<Flow> flows = new ArrayList<>();
        NodeList processes = doc.getElementsByTagNameNS("*", "process");
        for (int p = 0; p < processes.getLength(); p++) {
            for (Node c = processes.item(p).getFirstChild(); c != null; c = c.getNextSibling()) {
                if (!(c instanceof Element e)) {
                    continue;
                }
                String local = e.getLocalName();
                if (SHAPES.containsKey(local)) {
                    String name = e.getAttribute("name");
                    nodes.add(new Node0(e.getAttribute("id"), local, SHAPES.get(local), name.isBlank() ? e.getAttribute("id") : name));
                } else if ("sequenceFlow".equals(local)) {
                    flows.add(new Flow(e.getAttribute("id"), e.getAttribute("sourceRef"), e.getAttribute("targetRef"), e.getAttribute("name")));
                }
            }
        }
        Map<String, double[]> bounds = new HashMap<>();
        NodeList shapes = doc.getElementsByTagNameNS("*", "BPMNShape");
        for (int i = 0; i < shapes.getLength(); i++) {
            Element s = (Element) shapes.item(i);
            NodeList b = s.getElementsByTagNameNS("*", "Bounds");
            if (b.getLength() > 0) {
                Element be = (Element) b.item(0);
                bounds.put(s.getAttribute("bpmnElement"), new double[]{num(be, "x"), num(be, "y"), num(be, "width"), num(be, "height")});
            }
        }
        Map<String, List<double[]>> waypoints = new HashMap<>();
        NodeList edges = doc.getElementsByTagNameNS("*", "BPMNEdge");
        for (int i = 0; i < edges.getLength(); i++) {
            Element ed = (Element) edges.item(i);
            List<double[]> pts = new ArrayList<>();
            NodeList w = ed.getElementsByTagNameNS("*", "waypoint");
            for (int j = 0; j < w.getLength(); j++) {
                Element we = (Element) w.item(j);
                pts.add(new double[]{num(we, "x"), num(we, "y")});
            }
            waypoints.put(ed.getAttribute("bpmnElement"), pts);
        }
        return new Parsed(nodes, flows, bounds, waypoints);
    }

    private static double num(Element e, String attr) {
        try {
            return Double.parseDouble(e.getAttribute(attr));
        } catch (NumberFormatException ex) {
            return 0;
        }
    }

    // ── Dessin ────────────────────────────────────────────────────────────────

    /** Diagramme prêt à dessiner : coordonnées du modeleur si elles couvrent le processus, sinon disposition automatique. */
    static Laid build(Parsed p, Map<String, ElementStats> stats) {
        long covered = p.nodes.stream().filter(n -> p.bounds.containsKey(n.id)).count();
        boolean useDi = !p.nodes.isEmpty() && covered >= Math.ceil(p.nodes.size() * 0.8);
        return useDi ? fromDi(p, stats) : auto(p, stats);
    }

    private static ElementStats stat(Map<String, ElementStats> s, String id) {
        return s.getOrDefault(id, new ElementStats(0, 0, 0, 0));
    }

    private static Laid fromDi(Parsed p, Map<String, ElementStats> stats) {
        double minX = Double.MAX_VALUE, minY = Double.MAX_VALUE, maxX = 0, maxY = 0;
        for (Node0 n : p.nodes) {
            double[] b = p.bounds.get(n.id);
            if (b != null) {
                minX = Math.min(minX, b[0]);
                minY = Math.min(minY, b[1]);
                maxX = Math.max(maxX, b[0] + b[2]);
                maxY = Math.max(maxY, b[1] + b[3]);
            }
        }
        double dx = MARGIN - minX, dy = MARGIN - minY;
        List<DiagramNode> nodes = new ArrayList<>();
        for (Node0 n : p.nodes) {
            double[] b = p.bounds.get(n.id);
            if (b != null) {
                nodes.add(new DiagramNode(n.id, n.type, n.shape, n.name, b[0] + dx, b[1] + dy, b[2], b[3], stat(stats, n.id)));
            }
        }
        Map<String, DiagramNode> byId = new HashMap<>();
        nodes.forEach(n -> byId.put(n.id(), n));
        List<DiagramEdge> edges = new ArrayList<>();
        for (Flow f : p.flows) {
            if (!byId.containsKey(f.from) || !byId.containsKey(f.to)) {
                continue;
            }
            List<double[]> wp = p.waypoints.get(f.id);
            List<DiagramPoint> pts = new ArrayList<>();
            if (wp != null && wp.size() >= 2) {
                wp.forEach(w -> pts.add(new DiagramPoint(w[0] + dx, w[1] + dy)));
            } else {
                pts.addAll(straight(byId.get(f.from), byId.get(f.to)));
            }
            edges.add(new DiagramEdge(f.id, f.from, f.to, f.label, pts));
        }
        return new Laid(nodes, edges, maxX - minX + 2 * MARGIN, maxY - minY + 2 * MARGIN, false);
    }

    private static List<DiagramPoint> straight(DiagramNode a, DiagramNode b) {
        return List.of(new DiagramPoint(a.x() + a.w(), a.y() + a.h() / 2), new DiagramPoint(b.x(), b.y() + b.h() / 2));
    }

    private static double[] size(String shape) {
        return switch (shape) {
            case "EVENT_START", "EVENT_END", "EVENT" -> new double[]{40, 40};
            case "GATEWAY" -> new double[]{52, 52};
            case "SUBPROCESS" -> new double[]{160, 72};
            default -> new double[]{150, 66};
        };
    }

    /** Disposition automatique : niveaux de gauche à droite (plus long chemin), ordre des lignes par barycentre. */
    static Laid auto(Parsed p, Map<String, ElementStats> stats) {
        int n = p.nodes.size();
        Map<String, Integer> index = new LinkedHashMap<>();
        for (int i = 0; i < n; i++) {
            index.put(p.nodes.get(i).id, i);
        }
        List<List<Integer>> out = new ArrayList<>();
        List<List<Integer>> in = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            out.add(new ArrayList<>());
            in.add(new ArrayList<>());
        }
        List<Flow> valid = new ArrayList<>();
        for (Flow f : p.flows) {
            Integer a = index.get(f.from), b = index.get(f.to);
            if (a != null && b != null && !a.equals(b)) {
                out.get(a).add(b);
                in.get(b).add(a);
                valid.add(f);
            }
        }
        // 1. arêtes de retour (boucles) par parcours en profondeur
        Set<Long> back = new HashSet<>();
        int[] state = new int[n];
        List<Integer> order = new ArrayList<>();
        List<Integer> roots = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            if (in.get(i).isEmpty()) {
                roots.add(i);
            }
        }
        for (int i = 0; i < n; i++) {
            roots.add(i);   // nœuds restants (cycles sans entrée)
        }
        for (int r : roots) {
            if (state[r] == 0) {
                dfs(r, out, state, back, order);
            }
        }
        // 2. niveaux : plus long chemin sur les arêtes « avant », dans l'ordre topologique
        int[] layer = new int[n];
        for (int k = order.size() - 1; k >= 0; k--) {
            int u = order.get(k);
            for (int v : out.get(u)) {
                if (!back.contains(key(u, v))) {
                    layer[v] = Math.max(layer[v], layer[u] + 1);
                }
            }
        }
        int maxLayer = 0;
        for (int l : layer) {
            maxLayer = Math.max(maxLayer, l);
        }
        // 3. lignes dans chaque niveau : barycentre des prédécesseurs déjà placés
        List<List<Integer>> columns = new ArrayList<>();
        for (int l = 0; l <= maxLayer; l++) {
            columns.add(new ArrayList<>());
        }
        for (int i = 0; i < n; i++) {
            columns.get(layer[i]).add(i);
        }
        double[] row = new double[n];
        int maxRows = 1;
        for (int l = 0; l <= maxLayer; l++) {
            List<Integer> col = columns.get(l);
            final int level = l;
            col.sort(Comparator.comparingDouble((Integer v) -> bary(v, in, layer, level, row)).thenComparingInt(v -> v));
            for (int r = 0; r < col.size(); r++) {
                row[col.get(r)] = r;
            }
            maxRows = Math.max(maxRows, col.size());
        }
        // 4. coordonnées
        double top = MARGIN + 36;   // place au-dessus pour les flèches de retour
        DiagramNode[] placed = new DiagramNode[n];
        List<DiagramNode> nodes = new ArrayList<>();
        for (int l = 0; l <= maxLayer; l++) {
            List<Integer> col = columns.get(l);
            double offset = (maxRows - col.size()) / 2.0;
            for (int r = 0; r < col.size(); r++) {
                int i = col.get(r);
                Node0 nd = p.nodes.get(i);
                double[] sz = size(nd.shape);
                double cx = MARGIN + l * COL + COL / 2, cy = top + (offset + r) * ROW + ROW / 2;
                placed[i] = new DiagramNode(nd.id, nd.type, nd.shape, nd.name, cx - sz[0] / 2, cy - sz[1] / 2, sz[0], sz[1], stat(stats, nd.id));
                nodes.add(placed[i]);
            }
        }
        nodes.sort(Comparator.comparing(DiagramNode::id));
        // 5. flèches
        List<DiagramEdge> edges = new ArrayList<>();
        int backCount = 0;
        double minY = nodes.stream().mapToDouble(DiagramNode::y).min().orElse(top);   // les retours passent AU-DESSUS de toutes les étapes
        for (Flow f : valid) {
            int a = index.get(f.from), b = index.get(f.to);
            DiagramNode s = placed[a], t = placed[b];
            double sx = s.x() + s.w(), sy = s.y() + s.h() / 2, tx = t.x(), ty = t.y() + t.h() / 2;
            List<DiagramPoint> pts = new ArrayList<>();
            if (back.contains(key(a, b))) {
                double topY = minY - 18 - (backCount++ % 3) * 8;
                pts.add(new DiagramPoint(sx, sy));
                pts.add(new DiagramPoint(sx + 14, sy));
                pts.add(new DiagramPoint(sx + 14, topY));
                pts.add(new DiagramPoint(tx - 14, topY));
                pts.add(new DiagramPoint(tx - 14, ty));
                pts.add(new DiagramPoint(tx, ty));
            } else if (Math.abs(sy - ty) < 1) {
                pts.add(new DiagramPoint(sx, sy));
                pts.add(new DiagramPoint(tx, ty));
            } else {
                double midX = layer[b] - layer[a] > 1 ? tx - 22 : (sx + tx) / 2;
                pts.add(new DiagramPoint(sx, sy));
                pts.add(new DiagramPoint(midX, sy));
                pts.add(new DiagramPoint(midX, ty));
                pts.add(new DiagramPoint(tx, ty));
            }
            edges.add(new DiagramEdge(f.id, f.from, f.to, f.label, pts));
        }
        double width = (maxLayer + 1) * COL + 2 * MARGIN;
        double height = top + maxRows * ROW + MARGIN;
        return new Laid(nodes, edges, width, height, true);
    }

    private static void dfs(int start, List<List<Integer>> out, int[] state, Set<Long> back, List<Integer> order) {
        // parcours itératif (pas de récursion : un processus peut être profond)
        java.util.ArrayDeque<int[]> stack = new java.util.ArrayDeque<>();
        stack.push(new int[]{start, 0});
        state[start] = 1;
        while (!stack.isEmpty()) {
            int[] top = stack.peek();
            int u = top[0];
            if (top[1] < out.get(u).size()) {
                int v = out.get(u).get(top[1]++);
                if (state[v] == 0) {
                    state[v] = 1;
                    stack.push(new int[]{v, 0});
                } else if (state[v] == 1) {
                    back.add(key(u, v));
                }
            } else {
                state[u] = 2;
                order.add(u);
                stack.pop();
            }
        }
    }

    private static double bary(int v, List<List<Integer>> in, int[] layer, int level, double[] row) {
        double sum = 0;
        int c = 0;
        for (int u : in.get(v)) {
            if (layer[u] < level) {
                sum += row[u];
                c++;
            }
        }
        return c == 0 ? Double.MAX_VALUE / 4 : sum / c;
    }

    private static long key(int a, int b) {
        return ((long) a << 32) | (b & 0xffffffffL);
    }
}
