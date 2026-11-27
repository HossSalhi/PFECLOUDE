package com.orange.pfe.backend.service.aiassist.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.EntityCoverage;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Finding;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.QueryTrace;
import com.orange.pfe.backend.service.supervision.SupervisionClient;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Sonde de la STACK de supervision et des charges de travail, en LECTURE SEULE (Prometheus, kube-state-metrics, santé de
 * Prometheus, Loki et Grafana). Elle complète le détecteur de métriques/logs pour répondre à « que se passe-t-il dans HELIOS » côté
 * infrastructure (cibles de supervision, alertes, outils de supervision) ET côté application (déploiements, redémarrages de pods,
 * pods bloqués). Aucune règle ne modifie quoi que ce soit : ce sont des constats chiffrés, rattachés à leur requête exacte.
 * Une requête en échec est rapportée (jamais masquée) et rend la qualité des données « partielle ».
 */
@Component
public class TelemetryStackProbe {

    /** Résultat : constats, traces de requêtes, couverture, échecs nommés. {@code anyOk} : au moins une requête a répondu. */
    public record Result(List<Finding> findings, List<QueryTrace> queries, List<EntityCoverage> coverage, List<String> failures, boolean anyOk,
                         int entities) {
    }

    static final String TARGETS_DOWN_QL = "up == 0";
    static final String DEPLOY_QL = "kube_deployment_spec_replicas - kube_deployment_status_replicas_available > 0";
    static final String NOT_RUNNING_QL = "kube_pod_status_phase{phase=~\"Pending|Failed|Unknown\"} == 1";
    static final String STUCK_QL = "kube_pod_container_status_waiting_reason{reason=~\"CrashLoopBackOff|ImagePullBackOff|ErrImagePull|CreateContainerConfigError\"} == 1";

    private final SupervisionClient client;

    public TelemetryStackProbe(SupervisionClient client) {
        this.client = client;
    }

    public Result probe(long windowStart, long end, Set<ResourceScope> scopes) {
        long window = Math.max(300, Math.min(end - windowStart, 86_400));
        LocalDateTime start = TelemetryRules.at(windowStart), stop = TelemetryRules.at(end);
        List<Finding> findings = new ArrayList<>();
        List<QueryTrace> queries = new ArrayList<>();
        List<EntityCoverage> coverage = new ArrayList<>();
        List<String> failures = new ArrayList<>();
        boolean[] anyOk = {false};

        String unstableQl = "avg_over_time(up[" + window + "s]) < 0.95";
        String restartsQl = "sum by (namespace, container) (increase(kube_pod_container_status_restarts_total[" + window + "s])) >= 1";

        prom("PROBE_TARGETS_DOWN", "TARGET", TARGETS_DOWN_QL, start, stop, queries, failures, anyOk, r -> {
            for (JsonNode s : r) {
                String job = s.path("metric").path("job").asText("?"), inst = s.path("metric").path("instance").asText("");
                add(findings, scopes, scopeOfJob(job), targetName(job, inst), "TARGET", "TARGET_DOWN", "HIGH", start, stop, 0.0, null, null,
                        "Cible de supervision « " + job + " » injoignable (up = 0) : ses métriques ne sont plus collectées", TARGETS_DOWN_QL, "PROBE_TARGETS_DOWN");
            }
        });
        prom("PROBE_TARGETS_UNSTABLE", "TARGET", unstableQl, start, stop, queries, failures, anyOk, r -> {
            for (JsonNode s : r) {
                String job = s.path("metric").path("job").asText("?"), inst = s.path("metric").path("instance").asText("");
                double up = value(s);
                add(findings, scopes, scopeOfJob(job), targetName(job, inst), "TARGET", "TARGET_UNSTABLE", "MEDIUM", start, stop, round1(up * 100), 100.0, "%",
                        "Cible de supervision « " + job + " » instable : disponible " + round1(up * 100) + " % de la période", unstableQl, "PROBE_TARGETS_UNSTABLE");
            }
        });
        prom("PROBE_DEPLOYMENTS", "WORKLOAD", DEPLOY_QL, start, stop, queries, failures, anyOk, r -> {
            for (JsonNode s : r) {
                String ns = s.path("metric").path("namespace").asText("?"), dep = s.path("metric").path("deployment").asText("?");
                double missing = value(s);
                add(findings, scopes, TelemetryDetector.scopeOfApp(ns + "/" + dep), ns + "/" + dep, "WORKLOAD", "DEPLOYMENT_UNAVAILABLE", "HIGH", start, stop, missing,
                        null, "réplica(s)", "Déploiement incomplet : " + (int) missing + " réplica(s) manquant(s) par rapport à ce qui est demandé", DEPLOY_QL, "PROBE_DEPLOYMENTS");
            }
        });
        prom("PROBE_RESTARTS", "WORKLOAD", restartsQl, start, stop, queries, failures, anyOk, r -> {
            for (JsonNode s : r) {
                String ns = s.path("metric").path("namespace").asText("?"), ct = s.path("metric").path("container").asText("?");
                double n = Math.round(value(s));
                add(findings, scopes, TelemetryDetector.scopeOfApp(ns + "/" + ct), ns + "/" + ct, "WORKLOAD", "POD_RESTARTS", n >= 5 ? "HIGH" : "MEDIUM", start, stop, n, 0.0,
                        "redémarrage(s)", "Le conteneur a redémarré " + (int) n + " fois sur la période", restartsQl, "PROBE_RESTARTS");
            }
        });
        prom("PROBE_PODS_NOT_RUNNING", "WORKLOAD", NOT_RUNNING_QL, start, stop, queries, failures, anyOk, r -> {
            Map<String, Integer> grouped = new LinkedHashMap<>();         // N pods d'un même déploiement/tâche = UN constat
            for (JsonNode s : r) {
                grouped.merge(s.path("metric").path("namespace").asText("?") + "/" + podBase(s.path("metric").path("pod").asText("?")) + "|"
                        + s.path("metric").path("phase").asText("?"), 1, Integer::sum);
            }
            grouped.forEach((k, n) -> {
                String[] parts = k.split("[|]");
                add(findings, scopes, TelemetryDetector.scopeOfApp(parts[0]), parts[0], "WORKLOAD", "POD_NOT_RUNNING", "MEDIUM", start, stop, (double) n, null, "pod(s)",
                        n + " pod(s) en état « " + parts[1] + " » (ne tournent pas normalement)", NOT_RUNNING_QL, "PROBE_PODS_NOT_RUNNING");
            });
        });
        prom("PROBE_PODS_STUCK", "WORKLOAD", STUCK_QL, start, stop, queries, failures, anyOk, r -> {
            for (JsonNode s : r) {
                String ns = s.path("metric").path("namespace").asText("?"), ct = s.path("metric").path("container").asText("?"), why = s.path("metric").path("reason").asText("?");
                add(findings, scopes, TelemetryDetector.scopeOfApp(ns + "/" + ct), ns + "/" + ct, "WORKLOAD", "POD_STUCK", "HIGH", start, stop, 1.0, null, null,
                        "Conteneur bloqué : " + why, STUCK_QL, "PROBE_PODS_STUCK");
            }
        });
        alerts(findings, scopes, start, stop, queries, failures, anyOk);
        ready(findings, scopes, start, stop, queries, failures, anyOk);

        if (failures.isEmpty()) {
            coverage.add(new EntityCoverage("Supervision", "stack de supervision", "PROBES", 1.0, queries.size() + " sondes sur " + queries.size() + " ont répondu"));
        } else {
            coverage.add(new EntityCoverage("Supervision", "stack de supervision", "PROBES", round2((queries.size() - failures.size()) / (double) Math.max(1, queries.size())),
                    failures.size() + " sonde(s) en échec : " + String.join(" ; ", failures)));
        }
        return new Result(findings, queries, coverage, failures, anyOk[0], findings.stream().map(Finding::entity).collect(java.util.stream.Collectors.toSet()).size());
    }

    // ── alertes Prometheus en cours (état « firing ») ──────────────────────────────────────────────────────────────────

    private void alerts(List<Finding> findings, Set<ResourceScope> scopes, LocalDateTime start, LocalDateTime stop, List<QueryTrace> queries, List<String> failures,
                        boolean[] anyOk) {
        String id = "PROBE_ALERTS";
        try {
            JsonNode all = client.promAlerts();
            int n = 0;
            if (all != null) {
                for (JsonNode a : all) {
                    if (!"firing".equals(a.path("state").asText())) {
                        continue;
                    }
                    n++;
                    JsonNode l = a.path("labels");
                    String name = l.path("alertname").asText("alerte"), sev = l.path("severity").asText("warning");
                    String ns = l.path("namespace").asText(""), who = !ns.isBlank() ? ns + "/" + l.path("pod").asText(l.path("deployment").asText(name))
                            : l.path("instance").isMissingNode() ? "alerte " + name : TelemetryDetector.hostName(l.path("instance").asText());
                    ResourceScope scope = !ns.isBlank() ? TelemetryDetector.scopeOfApp(who) : l.path("instance").isMissingNode() ? ResourceScope.SERVICE : ResourceScope.VM;
                    add(findings, scopes, scope, who, "ALERT", "ALERT_FIRING", "critical".equalsIgnoreCase(sev) ? "HIGH" : "MEDIUM", start, stop, 1.0, null, null,
                            "Alerte Prometheus « " + name + " » en cours (gravité " + sev + ")", "GET /api/v1/alerts (state=firing)", id);
                }
            }
            queries.add(new QueryTrace(id, "Prometheus", "ALERT", "GET /api/v1/alerts (state=firing)", "OK", null, null, start, stop, n, n));
            anyOk[0] = true;
        } catch (RuntimeException e) {
            fail(id, "Prometheus", "ALERT", "GET /api/v1/alerts (state=firing)", start, stop, queries, failures, e);
        }
    }

    // ── santé des outils de supervision (Prometheus, Loki, Grafana) ────────────────────────────────────────────────────

    private void ready(List<Finding> findings, Set<ResourceScope> scopes, LocalDateTime start, LocalDateTime stop, List<QueryTrace> queries, List<String> failures,
                       boolean[] anyOk) {
        String id = "PROBE_TOOLS_READY", ql = "GET /-/ready (Prometheus), /ready (Loki), /api/health (Grafana)";
        try {
            Map<String, String> res = client.probeReady();
            int bad = 0;
            if (res != null) {
                for (Map.Entry<String, String> e : res.entrySet()) {
                    if (e.getValue() != null) {
                        bad++;
                        add(findings, scopes, ResourceScope.SERVICE, "service " + e.getKey(), "MONITORING", "MONITORING_NOT_READY", "HIGH", start, stop, 0.0, null, null,
                                e.getKey() + " n'est pas prêt : " + e.getValue(), ql, id);
                    }
                }
            }
            queries.add(new QueryTrace(id, "Supervision", "MONITORING", ql, "OK", null, null, start, stop, res == null ? 0 : res.size(), bad));
            anyOk[0] = true;
        } catch (RuntimeException e) {
            fail(id, "Supervision", "MONITORING", ql, start, stop, queries, failures, e);
        }
    }

    // ── utilitaires ────────────────────────────────────────────────────────────────────────────────────────────────────

    private void prom(String id, String signal, String ql, LocalDateTime start, LocalDateTime stop, List<QueryTrace> queries, List<String> failures, boolean[] anyOk,
                      java.util.function.Consumer<JsonNode> handler) {
        try {
            JsonNode r = client.promInstant(ql);
            if (r != null) {
                handler.accept(r);
            }
            queries.add(new QueryTrace(id, "Prometheus", signal, ql, "OK", null, null, start, stop, r == null ? 0 : r.size(), r == null ? 0 : r.size()));
            anyOk[0] = true;
        } catch (RuntimeException e) {
            fail(id, "Prometheus", signal, ql, start, stop, queries, failures, e);
        }
    }

    private static void fail(String id, String source, String signal, String ql, LocalDateTime start, LocalDateTime stop, List<QueryTrace> queries, List<String> failures,
                             RuntimeException e) {
        String reason = TelemetryDetector.reasonOf(e);
        queries.add(new QueryTrace(id, source, signal, ql, "FAILED", reason, null, start, stop, null, null));
        failures.add(id + " (" + reason + ")");
    }

    private static void add(List<Finding> out, Set<ResourceScope> scopes, ResourceScope scope, String entity, String signal, String rule, String severity,
                            LocalDateTime start, LocalDateTime stop, Double observed, Double baseline, String unit, String detail, String evidence, String queryRef) {
        if (scopes != null && !scopes.contains(scope)) {
            return;
        }
        out.add(new Finding(scope.name(), entity, signal, rule, severity, start, stop, observed, baseline, unit, detail,
                evidence.startsWith("GET ") ? evidence : "PromQL : " + evidence, queryRef));
    }

    private static double value(JsonNode series) {
        return series.path("value").path(1).asDouble(Double.NaN);
    }

    static ResourceScope scopeOfJob(String job) {
        return switch (job) {
            case "vm-node-exporters" -> ResourceScope.VM;
            case "etcd", "apiserver", "kubelet", "kubernetes-cadvisor", "kube-state-metrics" -> ResourceScope.CLUSTER;
            case "prometheus", "loki", "grafana", "helios-alarme" -> ResourceScope.SERVICE;
            default -> ResourceScope.APPLICATION;      // backend-direct, camunda, … : composants applicatifs de HELIOS
        };
    }

    static String targetName(String job, String instance) {
        String host = TelemetryDetector.hostName(instance);
        return "vm-node-exporters".equals(job) ? host : "service " + job + (host.equals(instance) || host.isBlank() ? "" : " (" + host + ")");
    }

    /** Nom du pod sans son suffixe de réplica (déploiement-hash-hash). */
    static String podBase(String pod) {
        String p = pod.replaceAll("-[a-z0-9]{8,10}-[a-z0-9]{5}$", "").replaceAll("-[a-z0-9]{5}$", "");
        return p.isBlank() ? pod : p;
    }

    private static double round1(double v) {
        return Math.round(v * 10.0) / 10.0;
    }

    private static double round2(double v) {
        return Math.round(v * 100.0) / 100.0;
    }
}
