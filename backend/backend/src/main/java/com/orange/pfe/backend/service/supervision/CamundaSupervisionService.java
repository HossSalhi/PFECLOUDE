package com.orange.pfe.backend.service.supervision;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaIncident;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaIncidents;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaInstance;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaInstances;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaOverview;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.CamundaSteps;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.DefinitionInfo;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.StepInfo;
import com.orange.pfe.backend.service.supervision.SupervisionClient.UnavailableException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Vue en LECTURE SEULE de Camunda pour tous les utilisateurs connectés : état du moteur, nombre d'instances par état,
 * instances récentes, incidents actifs, et étapes d'un processus (lues dans son BPMN). Seules des requêtes de
 * CONSULTATION (recherches et lecture du BPMN) sont envoyées, vers une liste fixe de chemins ; aucun démarrage,
 * annulation, relance ou modification. Les identifiants éventuels ({@code camunda.username/password}) ne sont jamais
 * renvoyés. Camunda injoignable : réponse « indisponible », jamais d'erreur serveur.
 */
@Service
public class CamundaSupervisionService {

    private static final Set<String> STATES = Set.of("ACTIVE", "COMPLETED", "TERMINATED");
    private static final Pattern KEY = Pattern.compile("^[0-9]{1,24}$");
    private static final Duration READ = Duration.ofSeconds(15);
    private static final long CACHE_MS = 15_000L;

    private final ObjectMapper mapper = new ObjectMapper();
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final Map<String, Object[]> cache = new LinkedHashMap<>();

    @Value("${camunda.rest-address:}")
    private String restAddress;

    @Value("${camunda.username:}")
    private String username;

    @Value("${camunda.password:}")
    private String password;

    // ── Consultation ──────────────────────────────────────────────────────────

    public CamundaOverview overview() {
        return cached("overview", () -> {
            try {
                JsonNode topology = get("/v2/topology");
                JsonNode broker = topology.path("brokers").path(0);
                boolean healthy = broker.path("partitions").isArray() && broker.path("partitions").size() > 0
                        && java.util.stream.StreamSupport.stream(broker.path("partitions").spliterator(), false)
                        .allMatch(p -> "healthy".equals(p.path("health").asText()));
                long active = total(post("/v2/process-instances/search", "{\"filter\":{\"state\":\"ACTIVE\"},\"page\":{\"limit\":1}}"));
                long completed = total(post("/v2/process-instances/search", "{\"filter\":{\"state\":\"COMPLETED\"},\"page\":{\"limit\":1}}"));
                long terminated = total(post("/v2/process-instances/search", "{\"filter\":{\"state\":\"TERMINATED\"},\"page\":{\"limit\":1}}"));
                long incidents = total(post("/v2/incidents/search", "{\"filter\":{\"state\":\"ACTIVE\"},\"page\":{\"limit\":1}}"));
                List<DefinitionInfo> defs = new ArrayList<>();
                for (JsonNode d : post("/v2/process-definitions/search", "{\"filter\":{\"isLatestVersion\":true},\"page\":{\"limit\":50}}").path("items")) {
                    defs.add(new DefinitionInfo(d.path("processDefinitionKey").asText(), d.path("processDefinitionId").asText(),
                            d.path("name").asText(d.path("processDefinitionId").asText()), d.path("version").asInt()));
                }
                return new CamundaOverview(true, null, LocalDateTime.now(), healthy, topology.path("gatewayVersion").asText(null),
                        topology.path("clusterSize").asInt(), active, completed, terminated, incidents, defs);
            } catch (UnavailableException e) {
                return new CamundaOverview(false, e.getMessage(), LocalDateTime.now(), false, null, 0, 0, 0, 0, 0, List.of());
            }
        }, CamundaOverview::available);
    }

    /** Instances les plus récentes (état facultatif : ACTIVE, COMPLETED ou TERMINATED), 1 à 50. */
    public CamundaInstances instances(String state, int limit) {
        int n = Math.max(1, Math.min(limit, 50));
        String st = state == null ? "" : state.trim().toUpperCase();
        if (!st.isEmpty() && !STATES.contains(st)) {
            throw new IllegalArgumentException("État inconnu : " + state);
        }
        return cached("instances:" + st + ":" + n, () -> {
            try {
                String filter = st.isEmpty() ? "" : "\"filter\":{\"state\":\"" + st + "\"},";
                JsonNode r = post("/v2/process-instances/search",
                        "{" + filter + "\"sort\":[{\"field\":\"startDate\",\"order\":\"DESC\"}],\"page\":{\"limit\":" + n + "}}");
                List<CamundaInstance> out = new ArrayList<>();
                for (JsonNode i : r.path("items")) {
                    Instant start = parse(i.path("startDate").asText(null));
                    Instant end = parse(i.path("endDate").asText(null));
                    out.add(new CamundaInstance(i.path("processInstanceKey").asText(), i.path("processDefinitionKey").asText(),
                            i.path("processDefinitionId").asText(), i.path("processDefinitionName").asText(i.path("processDefinitionId").asText()),
                            i.path("processDefinitionVersion").asInt(), i.path("state").asText(), i.path("hasIncident").asBoolean(),
                            local(start), local(end), start != null && end != null ? end.toEpochMilli() - start.toEpochMilli() : null));
                }
                return new CamundaInstances(true, null, r.path("page").path("totalItems").asLong(), out);
            } catch (UnavailableException e) {
                return new CamundaInstances(false, e.getMessage(), 0, List.of());
            }
        }, CamundaInstances::available);
    }

    /** Incidents Camunda actifs, 1 à 50. */
    public CamundaIncidents incidents(int limit) {
        int n = Math.max(1, Math.min(limit, 50));
        return cached("incidents:" + n, () -> {
            try {
                JsonNode r = post("/v2/incidents/search", "{\"filter\":{\"state\":\"ACTIVE\"},\"sort\":[{\"field\":\"creationTime\",\"order\":\"DESC\"}],\"page\":{\"limit\":" + n + "}}");
                List<CamundaIncident> out = new ArrayList<>();
                for (JsonNode i : r.path("items")) {
                    out.add(new CamundaIncident(i.path("incidentKey").asText(), i.path("processDefinitionId").asText(),
                            i.path("processInstanceKey").asText(), i.path("errorType").asText(),
                            truncate(i.path("errorMessage").asText(""), 300), i.path("elementId").asText(),
                            local(parse(i.path("creationTime").asText(null)))));
                }
                return new CamundaIncidents(true, null, r.path("page").path("totalItems").asLong(), out);
            } catch (UnavailableException e) {
                return new CamundaIncidents(false, e.getMessage(), 0, List.of());
            }
        }, CamundaIncidents::available);
    }

    /** Étapes d'un processus, dans l'ordre du fichier BPMN (tâches, passerelles, événements). */
    public CamundaSteps steps(String processDefinitionKey) {
        if (processDefinitionKey == null || !KEY.matcher(processDefinitionKey).matches()) {
            throw new IllegalArgumentException("Identifiant de processus invalide");
        }
        return cached("steps:" + processDefinitionKey, () -> {
            try {
                String xml = getText("/v2/process-definitions/" + processDefinitionKey + "/xml");
                return new CamundaSteps(true, null, processDefinitionKey, parseSteps(xml));
            } catch (UnavailableException e) {
                return new CamundaSteps(false, e.getMessage(), processDefinitionKey, List.of());
            }
        }, CamundaSteps::available);
    }

    /** Historique d'UNE instance de processus : état, étapes réellement exécutées, incidents (jamais les variables). */
    public record InstanceDetail(boolean available, String error, String definitionId, String state, LocalDateTime start,
                                 LocalDateTime end, boolean hasIncident,
                                 List<com.orange.pfe.backend.dto.audit.SelfHealingDtos.CamundaStepInfo> steps,
                                 List<com.orange.pfe.backend.dto.audit.SelfHealingDtos.CamundaIncidentInfo> incidents) {}

    /**
     * Lit l'instance, ses étapes (100 au plus, dans l'ordre) et ses incidents (20 au plus). Lecture seule : les VARIABLES
     * du processus ne sont jamais lues, car elles peuvent contenir des données sensibles.
     */
    public InstanceDetail instanceDetail(String processInstanceKey) {
        if (processInstanceKey == null || !KEY.matcher(processInstanceKey).matches()) {
            throw new IllegalArgumentException("Identifiant d'instance invalide");
        }
        return cached("instance:" + processInstanceKey, () -> {
            try {
                JsonNode inst = get("/v2/process-instances/" + processInstanceKey);
                JsonNode elements = post("/v2/element-instances/search", "{\"filter\":{\"processInstanceKey\":\"" + processInstanceKey
                        + "\"},\"sort\":[{\"field\":\"startDate\",\"order\":\"ASC\"}],\"page\":{\"limit\":100}}");
                JsonNode incs = post("/v2/incidents/search", "{\"filter\":{\"processInstanceKey\":\"" + processInstanceKey
                        + "\"},\"page\":{\"limit\":20}}");
                List<com.orange.pfe.backend.dto.audit.SelfHealingDtos.CamundaStepInfo> steps = new ArrayList<>();
                for (JsonNode e : elements.path("items")) {
                    Instant s = parse(e.path("startDate").asText(null));
                    Instant en = parse(e.path("endDate").asText(null));
                    String name = e.path("elementName").asText("");
                    steps.add(new com.orange.pfe.backend.dto.audit.SelfHealingDtos.CamundaStepInfo(
                            name.isBlank() ? e.path("elementId").asText("") : truncate(name, 160), e.path("type").asText(""),
                            e.path("state").asText(""), local(s), local(en), s != null && en != null ? en.toEpochMilli() - s.toEpochMilli() : null,
                            e.path("hasIncident").asBoolean()));
                }
                List<com.orange.pfe.backend.dto.audit.SelfHealingDtos.CamundaIncidentInfo> incidents = new ArrayList<>();
                for (JsonNode i : incs.path("items")) {
                    incidents.add(new com.orange.pfe.backend.dto.audit.SelfHealingDtos.CamundaIncidentInfo(i.path("errorType").asText(),
                            truncate(i.path("errorMessage").asText(""), 300), i.path("elementId").asText(), local(parse(i.path("creationTime").asText(null)))));
                }
                return new InstanceDetail(true, null, inst.path("processDefinitionId").asText(null), inst.path("state").asText(null),
                        local(parse(inst.path("startDate").asText(null))), local(parse(inst.path("endDate").asText(null))),
                        inst.path("hasIncident").asBoolean(), steps, incidents);
            } catch (UnavailableException e) {
                String msg = e.getMessage() != null && e.getMessage().contains("HTTP 404")
                        ? "Instance introuvable dans Camunda (purgée ou jamais créée)" : e.getMessage();
                return new InstanceDetail(false, msg, null, null, null, null, false, List.of(), List.of());
            }
        }, InstanceDetail::available);
    }

    /**
     * Diagramme d'un processus (formes, flèches, coordonnées) avec, sur chaque étape, le nombre d'exécutions terminées, en
     * cours, annulées et en incident. Lecture seule : fichier BPMN + statistiques par élément.
     */
    public com.orange.pfe.backend.dto.supervision.SupervisionDtos.ProcessDiagram diagram(String definitionKey) {
        if (definitionKey == null || !KEY.matcher(definitionKey).matches()) {
            throw new IllegalArgumentException("Identifiant de processus invalide");
        }
        return cached("diagram:" + definitionKey, () -> {
            try {
                JsonNode meta = get("/v2/process-definitions/" + definitionKey);
                String xml = getText("/v2/process-definitions/" + definitionKey + "/xml");
                Map<String, com.orange.pfe.backend.dto.supervision.SupervisionDtos.ElementStats> stats = new LinkedHashMap<>();
                try {
                    JsonNode s = post("/v2/process-definitions/" + definitionKey + "/statistics/element-instances", "{}");
                    for (JsonNode e : s.path("items")) {
                        stats.put(e.path("elementId").asText(), new com.orange.pfe.backend.dto.supervision.SupervisionDtos.ElementStats(
                                e.path("completed").asLong(), e.path("active").asLong(), e.path("canceled").asLong(), e.path("incidents").asLong()));
                    }
                } catch (UnavailableException ignored) {
                    // les statistiques sont un plus : le diagramme s'affiche sans compteurs
                }
                BpmnLayout.Parsed parsed;
                try {
                    parsed = BpmnLayout.parse(xml);
                } catch (Exception e) {
                    throw new UnavailableException("Camunda : le fichier du processus est inexploitable", e);
                }
                if (parsed.nodes().isEmpty()) {
                    return new com.orange.pfe.backend.dto.supervision.SupervisionDtos.ProcessDiagram(false, "Ce processus ne contient aucune étape",
                            definitionKey, meta.path("name").asText(""), meta.path("version").asInt(), false, 0, 0, List.of(), List.of());
                }
                BpmnLayout.Laid laid = BpmnLayout.build(parsed, stats);
                String name = meta.path("name").asText("");
                return new com.orange.pfe.backend.dto.supervision.SupervisionDtos.ProcessDiagram(true, null, definitionKey,
                        name.isBlank() ? meta.path("processDefinitionId").asText("") : name, meta.path("version").asInt(), laid.auto(),
                        laid.width(), laid.height(), laid.nodes(), laid.edges());
            } catch (UnavailableException e) {
                return new com.orange.pfe.backend.dto.supervision.SupervisionDtos.ProcessDiagram(false, e.getMessage(), definitionKey, "", 0,
                        false, 0, 0, List.of(), List.of());
            }
        }, com.orange.pfe.backend.dto.supervision.SupervisionDtos.ProcessDiagram::available);
    }

    // ── BPMN ──────────────────────────────────────────────────────────────────

    private static final Map<String, String> KINDS = Map.ofEntries(
            Map.entry("startEvent", "Début"), Map.entry("endEvent", "Fin"), Map.entry("serviceTask", "Tâche automatique"),
            Map.entry("userTask", "Tâche humaine"), Map.entry("scriptTask", "Script"), Map.entry("sendTask", "Envoi"),
            Map.entry("receiveTask", "Attente de message"), Map.entry("callActivity", "Sous-processus appelé"),
            Map.entry("exclusiveGateway", "Choix exclusif"), Map.entry("parallelGateway", "Parallèle"),
            Map.entry("eventBasedGateway", "Attente d'événement"), Map.entry("intermediateCatchEvent", "Attente"),
            Map.entry("intermediateThrowEvent", "Événement intermédiaire"), Map.entry("boundaryEvent", "Événement limite"),
            Map.entry("subProcess", "Sous-processus"), Map.entry("businessRuleTask", "Règle métier"));

    /** Lit les étapes d'un BPMN ; le XML est analysé SANS entités externes ni DTD (protection XXE). */
    static List<StepInfo> parseSteps(String xml) {
        try {
            DocumentBuilderFactory f = DocumentBuilderFactory.newInstance();
            f.setNamespaceAware(true);
            f.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
            f.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            f.setXIncludeAware(false);
            f.setExpandEntityReferences(false);
            DocumentBuilder b = f.newDocumentBuilder();
            Document doc = b.parse(new ByteArrayInputStream(xml.getBytes(StandardCharsets.UTF_8)));
            List<StepInfo> out = new ArrayList<>();
            NodeList all = doc.getElementsByTagNameNS("*", "*");
            for (int i = 0; i < all.getLength(); i++) {
                Node n = all.item(i);
                if (n instanceof Element e && KINDS.containsKey(e.getLocalName()) && e.getParentNode() != null
                        && !"definitions".equals(e.getParentNode().getLocalName())) {
                    String id = e.getAttribute("id");
                    String name = e.getAttribute("name");
                    out.add(new StepInfo(id, e.getLocalName(), KINDS.get(e.getLocalName()), name.isBlank() ? id : name));
                }
            }
            return out;
        } catch (Exception e) {
            throw new UnavailableException("Camunda : le fichier du processus est inexploitable", e);
        }
    }

    // ── HTTP en lecture seule ─────────────────────────────────────────────────

    private JsonNode get(String path) {
        return readJson(send(HttpRequest.newBuilder(), path).GET().build());
    }

    private JsonNode post(String path, String searchJson) {
        // POST utilisé UNIQUEMENT pour les recherches (/search) et les statistiques par élément : ce sont des lectures
        if (!path.endsWith("/search") && !path.endsWith("/statistics/element-instances")) {
            throw new IllegalArgumentException("Chemin non autorisé");
        }
        return readJson(send(HttpRequest.newBuilder().header("Content-Type", "application/json"), path)
                .POST(HttpRequest.BodyPublishers.ofString(searchJson)).build());
    }

    private String getText(String path) {
        return call(send(HttpRequest.newBuilder(), path).GET().build()).body();
    }

    private HttpRequest.Builder send(HttpRequest.Builder b, String path) {
        if (restAddress == null || restAddress.isBlank()) {
            throw new UnavailableException("Camunda n'est pas configuré", null);
        }
        b.uri(URI.create(restAddress.replaceAll("/+$", "") + path)).timeout(READ);
        if (username != null && !username.isBlank()) {
            b.header("Authorization", "Basic " + Base64.getEncoder().encodeToString((username + ":" + password).getBytes(StandardCharsets.UTF_8)));
        }
        return b;
    }

    private HttpResponse<String> call(HttpRequest req) {
        try {
            HttpResponse<String> r = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (r.statusCode() != 200) {
                throw new UnavailableException("Camunda a répondu HTTP " + r.statusCode(), null);
            }
            return r;
        } catch (IOException e) {
            throw new UnavailableException("Camunda est injoignable", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new UnavailableException("Camunda : requête interrompue", e);
        }
    }

    private JsonNode readJson(HttpRequest req) {
        try {
            return mapper.readTree(call(req).body());
        } catch (IOException e) {
            throw new UnavailableException("Camunda : réponse inexploitable", e);
        }
    }

    // ── Aides ─────────────────────────────────────────────────────────────────

    private static long total(JsonNode r) {
        return r.path("page").path("totalItems").asLong();
    }

    private static Instant parse(String iso) {
        try {
            return iso == null || iso.isBlank() ? null : Instant.parse(iso);
        } catch (RuntimeException e) {
            return null;
        }
    }

    private static LocalDateTime local(Instant i) {
        return i == null ? null : LocalDateTime.ofInstant(i, ZoneId.systemDefault());
    }

    private static String truncate(String s, int max) {
        return s.length() <= max ? s : s.substring(0, max - 1) + "…";
    }

    @SuppressWarnings("unchecked")
    private <T> T cached(String key, java.util.function.Supplier<T> loader, java.util.function.Predicate<T> cacheable) {
        long now = System.currentTimeMillis();
        synchronized (cache) {
            Object[] c = cache.get(key);
            if (c != null && now - (Long) c[0] < CACHE_MS) {
                return (T) c[1];
            }
        }
        T value = loader.get();
        if (cacheable.test(value)) {
            synchronized (cache) {
                cache.put(key, new Object[]{now, value});
                if (cache.size() > 200) {
                    cache.clear();
                }
            }
        }
        return value;
    }
}
