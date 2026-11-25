package com.orange.pfe.backend.service.vmpower;

import com.orange.pfe.backend.config.VmPowerProperties;
import com.orange.pfe.backend.service.remediation.NodeReadinessObservation;
import com.orange.pfe.backend.service.remediation.PodSetObservation;
import com.orange.pfe.backend.service.remediation.RemediationExecutor;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.Entry;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.PowerState;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.VmClass;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.VmState;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * VMDOWN-POLICY — UNE passe de vérification de récupération d'une VM cible. Aucune écriture, aucun effet de bord :
 * le verdict d'une passe ne suffit jamais à résoudre un incident (il faut DEUX passes consécutives espacées d'au
 * moins 15 s, persistées par {@code IncidentCardService}). Un simple ping ou un état « Powered On » ne suffit JAMAIS.
 *
 * <p><b>.162</b> (load balancer, DNS actif, membre etcd) : VM allumée selon vmrun (preuve fraîche) ∧ SSH ∧ HAProxy (tous
 * les serveurs UP) ∧ chemin API Kubernetes via HAProxy (frontend {@code k8s_api} OPEN, backend {@code k8s_masters} UP)
 * ∧ ports 443/6443 en direct ∧ DNS UDP ∧ membre etcd sain ET voyant un leader ∧ quorum etcd (TOUS les membres dont la VM
 * n'est pas confirmée éteinte : 5/5 si .160 est allumée, sinon N/N avec au moins 3 voix).
 *
 * <p><b>.160</b> (monitoring, LB1, DNS, membre etcd) : VM allumée ∧ SSH ∧ Prometheus prêt ∧ Grafana (base OK) ∧ Loki prêt ∧
 * entrées du détecteur saines (celles que {@code detecter.py} exige avant de décider : Grafana base OK et séries
 * Prometheus {@code up}) ∧ HAProxy (serveurs UP, chemin API) ∧ 443/6443 ∧ DNS ∧ membre etcd sain voyant un leader.
 *
 * <p><b>Workers (.155/.158)</b> : nœud Ready ∧ kubelet prouvé actif (condition {@code KubeletReady} ET Lease renouvelé) ∧
 * SSH ∧ membre etcd sain voyant un leader ∧ cluster PostgreSQL CNPG sain (toutes les instances prêtes, un primaire, un
 * réplica au moins) ∧ backend disponible (tous les réplicas du Deployment).
 */
@Component
public class VmRecoveryVerifier {

    /** Résultat d'UNE passe : {@code criteria} garde l'ordre d'évaluation (affiché dans la carte d'incident). */
    public record PassResult(boolean passed, Map<String, Boolean> criteria, String summary) {

        public String criteriaJson() {
            return criteria.entrySet().stream()
                    .map(e -> "\"" + e.getKey() + "\":" + e.getValue())
                    .collect(Collectors.joining(",", "{", "}"));
        }
    }

    private static final Pattern ETCD_HAS_LEADER = Pattern.compile("(?m)^etcd_server_has_leader\\s+1(?:\\.0+)?\\s*$");
    private static final String CNPG_ROLE_LABEL = "cnpg.io/instanceRole";

    private final VmProbes probes;
    private final RemediationExecutor cluster;
    private final VmPowerProperties props;

    public VmRecoveryVerifier(VmProbes probes, RemediationExecutor cluster, VmPowerProperties props) {
        this.probes = probes;
        this.cluster = cluster;
        this.props = props;
    }

    // ── VM d'infrastructure (.162, .160) ─────────────────────────────────────────────────────────

    /** Une passe pour une VM d'infrastructure ; {@code states} = états d'alimentation de TOUTES les VM (watchdog). */
    public PassResult verifyInfra(Entry entry, Map<String, VmState> states) {
        Map<String, Boolean> c = new LinkedHashMap<>();
        VmState self = states == null ? null : states.get(entry.name());
        c.put("power_running", isRunningFresh(self));
        c.put("ssh", probes.tcp(entry.ip(), 22));
        if (entry.vmClass() == VmClass.INFRA_LB) {
            String csv = probes.httpGet("http://" + entry.ip() + ":8404/;csv");
            c.put("haproxy_servers_up", allHaproxyServersUp(csv));
            c.put("api_via_haproxy", apiPathViaHaproxy(csv));
            c.put("frontend_443", probes.tcp(entry.ip(), 443));
            c.put("frontend_6443", probes.tcp(entry.ip(), 6443));
            c.put("dns_udp53", probes.dnsAnswers(entry.ip()));
            c.put("etcd_self", etcdHealthy(entry.ip()));
            c.put("etcd_has_leader", etcdHasLeader(entry.ip()));
            c.put("etcd_quorum", etcdQuorum(states));
        } else if (entry.vmClass() == VmClass.INFRA_MONITORING) {
            String grafana = probes.httpGet("http://" + entry.ip() + ":3000/api/health");
            String prometheusUp = probes.httpGet("http://" + entry.ip() + ":9090/api/v1/query?query=up");
            String csv = probes.httpGet("http://" + entry.ip() + ":8404/;csv");
            c.put("prometheus_ready", prometheusReady(probes.httpGet("http://" + entry.ip() + ":9090/-/ready")));
            c.put("grafana_health", grafanaDatabaseOk(grafana));
            c.put("loki_ready", lokiReady(probes.httpGet("http://" + entry.ip() + ":3100/ready")));
            c.put("detector_inputs", grafanaDatabaseOk(grafana) && prometheusHasUpSeries(prometheusUp));
            c.put("haproxy_servers_up", allHaproxyServersUp(csv));
            c.put("api_via_haproxy", apiPathViaHaproxy(csv));
            c.put("frontend_443", probes.tcp(entry.ip(), 443));
            c.put("frontend_6443", probes.tcp(entry.ip(), 6443));
            c.put("dns_udp53", probes.dnsAnswers(entry.ip()));
            c.put("etcd_self", etcdHealthy(entry.ip()));
            c.put("etcd_has_leader", etcdHasLeader(entry.ip()));
        } else {
            c.put("unsupported_class", false);   // jamais de PASS pour une classe non prévue
        }
        return result(entry, c);
    }

    // ── Nœuds Kubernetes (workers .155/.158 ; plan de contrôle .153 depuis le 2026-11-17) ────────

    /**
     * Une passe pour un nœud Kubernetes cible de rallumage, à partir d'UNE lecture du nœud. Si le nœud n'est même pas
     * Ready, les autres critères ne sont pas évalués (inutile et sans valeur de preuve) : la passe échoue sur
     * {@code node_ready} seul. Le plan de contrôle ({@code K8S_CONTROL_PLANE}, .153) doit en plus répondre sur son
     * apiserver (TCP 6443, critère {@code apiserver_6443}).
     */
    public PassResult verifyWorker(Entry entry, NodeReadinessObservation node) {
        Map<String, Boolean> c = new LinkedHashMap<>();
        boolean ready = node != null && node.apiAvailable() && node.ready()
                && "True".equalsIgnoreCase(node.conditionStatus());
        c.put("node_ready", ready);
        if (ready) {
            c.put("kubelet_active", node.kubeletActive());
            c.put("ssh", probes.tcp(entry.ip(), 22));
            if (entry.vmClass() == VmClass.K8S_CONTROL_PLANE) {
                c.put("apiserver_6443", probes.tcp(entry.ip(), 6443));
            }
            c.put("etcd_self", etcdHealthy(entry.ip()));
            c.put("etcd_has_leader", etcdHasLeader(entry.ip()));
            c.put("cnpg_healthy", cnpgHealthy());
            c.put("backend_available", backendAvailable());
        }
        return result(entry, c);
    }

    /**
     * Lecture « effective » du nœud pour le chemin Camunda : identique à la lecture brute tant que TOUS les critères de
     * {@link #verifyWorker} sont satisfaits ; sinon le nœud est présenté comme NON prêt (condition {@code PROOFS_FAILED})
     * avec la liste des critères en échec — la preuve persistée reflète donc le verdict complet, pas le seul Ready.
     */
    public NodeReadinessObservation effectiveWorkerObservation(Entry entry, NodeReadinessObservation raw) {
        if (raw == null || !raw.apiAvailable() || !raw.ready() || !"True".equalsIgnoreCase(raw.conditionStatus())) {
            return raw;
        }
        PassResult r = verifyWorker(entry, raw);
        if (r.passed()) {
            return new NodeReadinessObservation(true, true, raw.conditionStatus(),
                    raw.message() + " ; preuves complémentaires satisfaites " + r.criteria().keySet(),
                    raw.reason(), raw.leaseAgeSeconds());
        }
        return new NodeReadinessObservation(true, false, "PROOFS_FAILED",
                raw.message() + " ; " + r.summary(), raw.reason(), raw.leaseAgeSeconds());
    }

    // ── Outils communs ───────────────────────────────────────────────────────────────────────────

    private static PassResult result(Entry entry, Map<String, Boolean> c) {
        boolean passed = !c.isEmpty() && c.values().stream().allMatch(Boolean::booleanValue);
        String failed = c.entrySet().stream().filter(e -> !e.getValue()).map(Map.Entry::getKey)
                .collect(Collectors.joining(","));
        return new PassResult(passed, c, passed
                ? entry.name() + " : tous les critères de récupération sont satisfaits (" + c.keySet() + ")"
                : entry.name() + " : critères en échec [" + failed + "]");
    }

    static boolean isRunningFresh(VmState s) {
        return s != null && s.powerState() == PowerState.RUNNING && s.evidence() != null && s.evidence().isFresh();
    }

    private static boolean confirmedOff(VmState s) {
        return s != null && s.powerState() == PowerState.POWERED_OFF && s.evidence() != null && s.evidence().isFresh();
    }

    boolean etcdHealthy(String ip) {
        String body = probes.httpGet("http://" + ip + ":2381/health");
        return body != null && body.replaceAll("\\s", "").contains("\"health\":\"true\"");
    }

    /** Le membre voit un leader (appartenance à un cluster en quorum) : {@code etcd_server_has_leader 1}. */
    boolean etcdHasLeader(String ip) {
        String metrics = probes.httpGet("http://" + ip + ":2381/metrics");
        return metrics != null && ETCD_HAS_LEADER.matcher(metrics).find();
    }

    /** Tous les membres dont la VM n'est PAS confirmée éteinte doivent être sains, et il faut au moins 3 voix. */
    boolean etcdQuorum(Map<String, VmState> states) {
        int expected = 0;
        int healthy = 0;
        for (Entry member : VmPowerPolicy.entries()) {
            if (confirmedOff(states == null ? null : states.get(member.name()))) {
                continue;
            }
            expected++;
            if (etcdHealthy(member.ip())) {
                healthy++;
            }
        }
        return expected >= 3 && healthy == expected;
    }

    /**
     * Cluster PostgreSQL CNPG sain : l'API répond, au moins {@code cnpgInstances} pods, TOUS prêts (ni Pending ni en
     * suppression), exactement UN primaire et au moins un réplica. Aucun switchover n'est jamais déclenché ici.
     */
    boolean cnpgHealthy() {
        if (cluster == null) {
            return false;
        }
        PodSetObservation p = cluster.inspectPods(props.getCnpgNamespace(), "cnpg.io/cluster=" + props.getCnpgCluster());
        if (p == null || !p.apiAvailable() || p.pods().size() < Math.max(2, props.getCnpgInstances())) {
            return false;
        }
        if (!p.pods().stream().allMatch(PodSetObservation.PodState::ready)) {
            return false;
        }
        long primaries = p.pods().stream().filter(x -> "primary".equals(x.labels().get(CNPG_ROLE_LABEL))).count();
        long replicas = p.pods().stream().filter(x -> "replica".equals(x.labels().get(CNPG_ROLE_LABEL))).count();
        return primaries == 1 && replicas >= 1;
    }

    /** Backend disponible : tous les réplicas désirés du Deployment sont disponibles. */
    boolean backendAvailable() {
        return cluster != null && cluster.isWorkloadHealthy(props.getBackendNamespace(), props.getBackendDeployment());
    }

    static boolean prometheusReady(String body) {
        if (body == null) {
            return false;
        }
        String lower = body.toLowerCase();
        return lower.contains("is ready") && !lower.contains("not ready");
    }

    static boolean lokiReady(String body) {
        return body != null && "ready".equalsIgnoreCase(body.trim());
    }

    static boolean grafanaDatabaseOk(String body) {
        return body != null && body.replaceAll("\\s", "").contains("\"database\":\"ok\"");
    }

    /** Réponse de {@code /api/v1/query?query=up} : succès ET au moins une série (le détecteur exige {@code prom("up")}). */
    static boolean prometheusHasUpSeries(String body) {
        if (body == null) {
            return false;
        }
        String compact = body.replaceAll("\\s", "");
        return compact.contains("\"status\":\"success\"") && compact.contains("\"result\":[{");
    }

    /**
     * Chemin API Kubernetes via HAProxy : le frontend {@code k8s_api} est {@code OPEN} ET le backend {@code k8s_masters}
     * est {@code UP} (HAProxy sonde lui-même {@code /readyz} des apiservers avec vérification TLS).
     */
    static boolean apiPathViaHaproxy(String csv) {
        List<Map<String, String>> rows = csvRows(csv);
        boolean frontendOpen = false;
        boolean backendUp = false;
        for (Map<String, String> r : rows) {
            if ("k8s_api".equals(r.get("pxname")) && "FRONTEND".equals(r.get("svname"))
                    && String.valueOf(r.get("status")).startsWith("OPEN")) {
                frontendOpen = true;
            }
            if ("k8s_masters".equals(r.get("pxname")) && "BACKEND".equals(r.get("svname"))
                    && String.valueOf(r.get("status")).startsWith("UP")) {
                backendUp = true;
            }
        }
        return frontendOpen && backendUp;
    }

    private static List<Map<String, String>> csvRows(String csv) {
        List<Map<String, String>> out = new ArrayList<>();
        if (csv == null || csv.isBlank()) {
            return out;
        }
        List<String> lines = csv.lines().filter(l -> !l.isBlank()).toList();
        if (lines.isEmpty() || !lines.get(0).startsWith("#")) {
            return out;
        }
        List<String> header = List.of(lines.get(0).substring(1).trim().split(",", -1));
        for (String line : lines.subList(1, lines.size())) {
            String[] f = line.split(",", -1);
            Map<String, String> row = new HashMap<>();
            for (int i = 0; i < header.size() && i < f.length; i++) {
                row.put(header.get(i), f[i]);
            }
            out.add(row);
        }
        return out;
    }

    /**
     * Lecture du CSV de statistiques HAProxy : tous les serveurs (hors lignes FRONTEND/BACKEND) doivent être
     * {@code UP} ; au moins un serveur est exigé (un CSV vide ou illisible n'est jamais un succès).
     */
    static boolean allHaproxyServersUp(String csv) {
        if (csv == null || csv.isBlank()) {
            return false;
        }
        List<String> lines = csv.lines().filter(l -> !l.isBlank()).toList();
        if (lines.isEmpty() || !lines.get(0).startsWith("#")) {
            return false;
        }
        List<String> header = List.of(lines.get(0).substring(1).trim().split(",", -1));
        int svname = header.indexOf("svname");
        int status = header.indexOf("status");
        if (svname < 0 || status < 0) {
            return false;
        }
        int servers = 0;
        for (String line : lines.subList(1, lines.size())) {
            String[] f = line.split(",", -1);
            if (f.length <= Math.max(svname, status)) {
                return false;
            }
            String name = f[svname];
            if ("FRONTEND".equals(name) || "BACKEND".equals(name)) {
                continue;
            }
            servers++;
            if (!f[status].startsWith("UP")) {
                return false;
            }
        }
        return servers > 0;
    }
}
