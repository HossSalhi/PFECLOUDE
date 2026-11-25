package com.orange.pfe.backend.service.vmpower;

import com.orange.pfe.backend.config.VmPowerProperties;
import com.orange.pfe.backend.service.remediation.NodeReadinessObservation;
import com.orange.pfe.backend.service.remediation.PodSetObservation;
import com.orange.pfe.backend.service.remediation.PodSetObservation.PodState;
import com.orange.pfe.backend.service.remediation.RemediationExecutor;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.Entry;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.PowerState;
import com.orange.pfe.backend.service.vmpower.VmRecoveryVerifier.PassResult;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.Evidence;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.Guards;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.VmState;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * VMDOWN-POLICY — UNE passe de vérification de récupération (.162, .160 et workers). Les sondes et l'API Kubernetes
 * sont simulées ; les fixtures reprennent les réponses RÉELLES relevées en lecture seule sur l'infrastructure.
 */
class VmRecoveryVerifierTest {

    private VmProbes probes;
    private RemediationExecutor cluster;
    private VmRecoveryVerifier verifier;
    private Entry lb2;
    private Entry monitoring;
    private Entry worker1;
    private final Set<String> tcpDown = new HashSet<>();      // "ip:port"
    private final Set<String> etcdUnhealthy = new HashSet<>(); // ip : /health KO
    private final Set<String> etcdNoLeader = new HashSet<>();  // ip : etcd_server_has_leader 0
    private final Set<String> httpDown = new HashSet<>();      // fragments d'URL en échec (null)
    private String haproxyCsv;
    private boolean dnsUp = true;
    private final Map<String, VmState> states = new LinkedHashMap<>();

    /** Extrait réel du CSV HAProxy (.160/.162) : un serveur en « UP 1/3 » pour tester le préfixe UP. */
    static final String CSV_OK = "# pxname,svname,qcur,qmax,scur,smax,slim,stot,bin,bout,dreq,dresp,ereq,econ,eresp,wretr,wredis,status,weight\n"
            + "k8s_api,FRONTEND,,,0,0,0,0,0,0,0,0,0,,,,,OPEN,\n"
            + "k8s_masters,master,0,0,0,0,,0,0,0,,0,,0,0,0,0,UP,1\n"
            + "k8s_masters,worker1,0,0,0,0,,0,0,0,,0,,0,0,0,0,UP 1/3,1\n"
            + "k8s_masters,worker2,0,0,0,0,,0,0,0,,0,,0,0,0,0,UP,1\n"
            + "k8s_masters,BACKEND,0,0,0,0,0,0,0,0,0,0,,0,0,0,0,UP,1\n"
            + "ingress_http,node-155,0,0,0,0,,0,0,0,,0,,0,0,0,0,UP,1\n"
            + "ingress_http,BACKEND,0,0,0,0,0,0,0,0,0,0,,0,0,0,0,UP,1\n";

    static final String GRAFANA_OK = "{\n  \"database\": \"ok\",\n  \"version\": \"13.0.1\",\n  \"commit\": \"a100054f\"\n}";
    static final String PROM_UP_OK = "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\",\"result\":[{\"metric\":{\"__name__\":\"up\","
            + "\"instance\":\"127.0.0.1:8080\",\"job\":\"kube-state-metrics\"},\"value\":[1793921839.719,\"1\"]}]}}";

    @BeforeEach
    void setUp() {
        probes = mock(VmProbes.class);
        cluster = mock(RemediationExecutor.class);
        when(probes.tcp(anyString(), anyInt())).thenAnswer(inv -> !tcpDown.contains(inv.getArgument(0) + ":" + inv.getArgument(1)));
        when(probes.dnsAnswers(anyString())).thenAnswer(inv -> dnsUp);
        when(probes.httpGet(anyString())).thenAnswer(inv -> {
            String url = inv.getArgument(0);
            if (httpDown.stream().anyMatch(url::contains)) {
                return null;
            }
            String ip = url.substring("http://".length(), url.indexOf(':', "http://".length()));
            if (url.endsWith(":8404/;csv")) {
                return haproxyCsv;
            }
            if (url.endsWith(":2381/health")) {
                return etcdUnhealthy.contains(ip) ? null : "{\"health\":\"true\",\"reason\":\"\"}";
            }
            if (url.endsWith(":2381/metrics")) {
                return "# HELP etcd_server_has_leader Whether or not a leader exists. 1 is existence, 0 is not.\n"
                        + "# TYPE etcd_server_has_leader gauge\n"
                        + "etcd_server_has_leader " + (etcdNoLeader.contains(ip) ? "0" : "1") + "\n"
                        + "etcd_server_is_leader 0\n";
            }
            if (url.endsWith(":9090/-/ready")) {
                return "Prometheus Server is Ready.\n";
            }
            if (url.endsWith(":3000/api/health")) {
                return GRAFANA_OK;
            }
            if (url.endsWith(":3100/ready")) {
                return "ready\n";
            }
            if (url.endsWith(":9090/api/v1/query?query=up")) {
                return PROM_UP_OK;
            }
            return null;
        });
        VmPowerProperties props = new VmPowerProperties();
        verifier = new VmRecoveryVerifier(probes, cluster, props);
        lb2 = VmPowerPolicy.find("k8s-lb2").orElseThrow();
        monitoring = VmPowerPolicy.find("k8s-monitoring").orElseThrow();
        worker1 = VmPowerPolicy.find("k8s-worker1").orElseThrow();
        haproxyCsv = CSV_OK;
        for (Entry e : VmPowerPolicy.entries()) {
            states.put(e.name(), state(e.name(), PowerState.RUNNING, 1000));
        }
        // Kubernetes sain par défaut : CNPG 2/2 (un primaire, un réplica), backend 2/2.
        when(cluster.inspectPods(eq("pfe"), eq("cnpg.io/cluster=postgres"))).thenReturn(cnpg(true, true, true));
        when(cluster.isWorkloadHealthy(eq("pfe"), eq("backend"))).thenReturn(true);
    }

    private static PodSetObservation cnpg(boolean primaryReady, boolean replicaReady, boolean api) {
        return new PodSetObservation(api, List.of(
                new PodState("postgres-5", "Running", primaryReady, Map.of("cnpg.io/instanceRole", "primary")),
                new PodState("postgres-7", "Running", replicaReady, Map.of("cnpg.io/instanceRole", "replica"))));
    }

    private static VmState state(String vm, PowerState ps, long ageMs) {
        return new VmState(vm, VmPowerPolicy.modeOf(vm).name(), ps, "OK",
                new Evidence("vmrun -T ws list", Instant.now(), ageMs, 100, 5, ps == PowerState.RUNNING, "a", "b"),
                new Guards(false, false, true, List.of(), 0, 0, null, 8000));
    }

    // ───────────────────────────── .162 ─────────────────────────────

    @Test
    void lb2_tous_criteres_ok_une_passe_reussie() {
        PassResult r = verifier.verifyInfra(lb2, states);
        assertTrue(r.passed(), r.summary());
        assertEquals(List.of("power_running", "ssh", "haproxy_servers_up", "api_via_haproxy", "frontend_443",
                "frontend_6443", "dns_udp53", "etcd_self", "etcd_has_leader", "etcd_quorum"),
                List.copyOf(r.criteria().keySet()));
        assertTrue(r.criteriaJson().startsWith("{\"power_running\":true"));
    }

    @Test
    void chaque_critere_en_echec_fait_echouer_la_passe_et_il_est_nomme() {
        // SSH
        tcpDown.add("192.168.148.162:22");
        assertFailedOn("ssh");
        tcpDown.clear();
        // frontends directs
        tcpDown.add("192.168.148.162:443");
        assertFailedOn("frontend_443");
        tcpDown.clear();
        tcpDown.add("192.168.148.162:6443");
        assertFailedOn("frontend_6443");
        tcpDown.clear();
        // DNS
        dnsUp = false;
        assertFailedOn("dns_udp53");
        dnsUp = true;
        // etcd local
        etcdUnhealthy.add("192.168.148.162");
        PassResult r = verifier.verifyInfra(lb2, states);
        assertFalse(r.passed());
        assertFalse(r.criteria().get("etcd_self"));
        assertFalse(r.criteria().get("etcd_quorum"), "un membre malade empêche aussi le quorum 5/5");
        etcdUnhealthy.clear();
        // membre etcd sain mais SANS leader visible (hors cluster en quorum)
        etcdNoLeader.add("192.168.148.162");
        assertFailedOn("etcd_has_leader");
        etcdNoLeader.clear();
        // VM non allumée / preuve périmée
        states.put("k8s-lb2", state("k8s-lb2", PowerState.POWERED_OFF, 1000));
        assertFailedOn("power_running");
        states.put("k8s-lb2", state("k8s-lb2", PowerState.RUNNING, 20_000));
        assertFailedOn("power_running");
        states.put("k8s-lb2", state("k8s-lb2", PowerState.UNKNOWN, 1000));
        assertFailedOn("power_running");
    }

    private void assertFailedOn(String criterion) {
        assertFailedOn(lb2, criterion);
    }

    private void assertFailedOn(Entry vm, String criterion) {
        PassResult r = verifier.verifyInfra(vm, states);
        assertFalse(r.passed(), criterion);
        assertFalse(r.criteria().get(criterion), criterion);
        assertTrue(r.summary().contains(criterion), r.summary());
    }

    @Test
    void haproxy_un_serveur_down_ou_csv_vide_ou_illisible_fait_echouer() {
        haproxyCsv = CSV_OK.replace("UP 1/3", "DOWN 0/3");
        assertFailedOn("haproxy_servers_up");
        haproxyCsv = CSV_OK.replace("UP 1/3", "NOLB");
        assertFailedOn("haproxy_servers_up");
        haproxyCsv = null;
        assertFailedOn("haproxy_servers_up");
        haproxyCsv = "";
        assertFailedOn("haproxy_servers_up");
        haproxyCsv = "pas du csv";
        assertFailedOn("haproxy_servers_up");
        // aucun serveur (seulement FRONTEND/BACKEND) : pas un succès
        haproxyCsv = "# pxname,svname,status\nfe,FRONTEND,OPEN\nbe,BACKEND,UP\n";
        assertFailedOn("haproxy_servers_up");
        assertTrue(VmRecoveryVerifier.allHaproxyServersUp(CSV_OK));
    }

    @Test
    void acces_api_kubernetes_par_le_chemin_haproxy_exige_frontend_ouvert_et_backend_up() {
        assertTrue(VmRecoveryVerifier.apiPathViaHaproxy(CSV_OK));
        // backend k8s_masters en panne (plus aucun apiserver joignable via HAProxy)
        haproxyCsv = CSV_OK.replace("k8s_masters,BACKEND,0,0,0,0,0,0,0,0,0,0,,0,0,0,0,UP,1",
                "k8s_masters,BACKEND,0,0,0,0,0,0,0,0,0,0,,0,0,0,0,DOWN,1");
        assertFailedOn("api_via_haproxy");
        // frontend k8s_api fermé
        haproxyCsv = CSV_OK.replace("k8s_api,FRONTEND,,,0,0,0,0,0,0,0,0,0,,,,,OPEN,", "k8s_api,FRONTEND,,,0,0,0,0,0,0,0,0,0,,,,,STOP,");
        assertFailedOn("api_via_haproxy");
        // un autre backend UP ne remplace jamais k8s_masters
        assertFalse(VmRecoveryVerifier.apiPathViaHaproxy("# pxname,svname,status\nautre,FRONTEND,OPEN\nautre,BACKEND,UP\n"));
        assertFalse(VmRecoveryVerifier.apiPathViaHaproxy(null));
        assertFalse(VmRecoveryVerifier.apiPathViaHaproxy("illisible"));
    }

    @Test
    void quorum_etcd_exige_cinq_sur_cinq_si_160_est_allumee() {
        etcdUnhealthy.add("192.168.148.153");     // un autre membre en échec
        PassResult r = verifier.verifyInfra(lb2, states);
        assertFalse(r.passed());
        assertTrue(r.criteria().get("etcd_self"), ".162 elle-même est saine");
        assertFalse(r.criteria().get("etcd_quorum"), "4/5 ne suffit pas quand toutes les VM sont allumées");
    }

    @Test
    void quorum_etcd_n_sur_n_si_160_est_confirmee_eteinte() {
        states.put("k8s-monitoring", state("k8s-monitoring", PowerState.POWERED_OFF, 1000));
        etcdUnhealthy.add("192.168.148.160");     // .160 muette : normal puisqu'éteinte
        PassResult r = verifier.verifyInfra(lb2, states);
        assertTrue(r.passed(), r.summary());
        assertTrue(r.criteria().get("etcd_quorum"), "4/4 des membres attendus suffit (au moins 3 voix)");
    }

    @Test
    void quorum_etcd_refuse_si_moins_de_trois_voix_attendues() {
        for (String vm : List.of("k8s-monitoring", "k8s-master", "k8s-worker1")) {
            states.put(vm, state(vm, PowerState.POWERED_OFF, 1000));
        }
        PassResult r = verifier.verifyInfra(lb2, states);
        assertFalse(r.criteria().get("etcd_quorum"), "2 voix attendues < 3 : pas de quorum");
        assertFalse(r.passed());
    }

    @Test
    void etat_inconnu_d_un_membre_ne_le_dispense_pas_du_quorum() {
        states.put("k8s-worker2", state("k8s-worker2", PowerState.UNKNOWN, 1000));
        etcdUnhealthy.add("192.168.148.155");
        assertFalse(verifier.verifyInfra(lb2, states).criteria().get("etcd_quorum"),
                "UNKNOWN n'est pas « éteinte confirmée » : le membre reste attendu");
    }

    // ───────────────────────────── .160 ─────────────────────────────

    @Test
    void monitoring_criteres_propres_a_160_services_detecteur_haproxy_et_etcd() {
        PassResult r = verifier.verifyInfra(monitoring, states);
        assertTrue(r.passed(), r.summary());
        assertEquals(List.of("power_running", "ssh", "prometheus_ready", "grafana_health", "loki_ready",
                "detector_inputs", "haproxy_servers_up", "api_via_haproxy", "frontend_443", "frontend_6443",
                "dns_udp53", "etcd_self", "etcd_has_leader"), List.copyOf(r.criteria().keySet()));
    }

    @Test
    void monitoring_chaque_service_en_echec_est_nomme_et_empeche_le_PASS() {
        httpDown.add("192.168.148.160:9090/-/ready");
        assertFailedOn(monitoring, "prometheus_ready");
        httpDown.clear();
        httpDown.add("192.168.148.160:3000/api/health");
        PassResult r = verifier.verifyInfra(monitoring, states);
        assertFalse(r.passed());
        assertFalse(r.criteria().get("grafana_health"));
        assertFalse(r.criteria().get("detector_inputs"), "le détecteur exige Grafana (base OK) avant de décider");
        httpDown.clear();
        httpDown.add("192.168.148.160:3100/ready");
        assertFailedOn(monitoring, "loki_ready");
        httpDown.clear();
        httpDown.add("192.168.148.160:9090/api/v1/query?query=up");
        PassResult d = verifier.verifyInfra(monitoring, states);
        assertFalse(d.criteria().get("detector_inputs"), "aucune série Prometheus : le détecteur ne peut pas décider");
        assertTrue(d.criteria().get("prometheus_ready"), "Prometheus prêt mais sans séries : seul le détecteur échoue");
        httpDown.clear();
        haproxyCsv = CSV_OK.replace("UP 1/3", "DOWN 0/3");
        assertFailedOn(monitoring, "haproxy_servers_up");
        haproxyCsv = CSV_OK;
        tcpDown.add("192.168.148.160:22");
        assertFailedOn(monitoring, "ssh");
        tcpDown.clear();
        etcdNoLeader.add("192.168.148.160");
        assertFailedOn(monitoring, "etcd_has_leader");
        etcdNoLeader.clear();
        etcdUnhealthy.add("192.168.148.160");
        assertFailedOn(monitoring, "etcd_self");
        etcdUnhealthy.clear();
        states.put("k8s-monitoring", state("k8s-monitoring", PowerState.POWERED_OFF, 1000));
        assertFailedOn(monitoring, "power_running");
    }

    @Test
    void lectures_de_services_tolerent_les_reponses_reelles_et_refusent_le_reste() {
        assertTrue(VmRecoveryVerifier.prometheusReady("Prometheus Server is Ready.\n"));
        assertFalse(VmRecoveryVerifier.prometheusReady("Prometheus Server is Not Ready."));
        assertFalse(VmRecoveryVerifier.prometheusReady(null));
        assertTrue(VmRecoveryVerifier.lokiReady("ready\n"));
        assertFalse(VmRecoveryVerifier.lokiReady("Ingester not ready: waiting for 15s after being ready"));
        assertFalse(VmRecoveryVerifier.lokiReady(null));
        assertTrue(VmRecoveryVerifier.grafanaDatabaseOk(GRAFANA_OK));
        assertFalse(VmRecoveryVerifier.grafanaDatabaseOk("{\"database\":\"failing\"}"));
        assertTrue(VmRecoveryVerifier.prometheusHasUpSeries(PROM_UP_OK));
        assertFalse(VmRecoveryVerifier.prometheusHasUpSeries("{\"status\":\"success\",\"data\":{\"result\":[]}}"));
        assertFalse(VmRecoveryVerifier.prometheusHasUpSeries("{\"status\":\"error\",\"result\":[{}]}"));
        assertFalse(VmRecoveryVerifier.prometheusHasUpSeries(null));
    }

    // ───────────────────────────── Workers (.155, .158) ─────────────────────────────

    private static NodeReadinessObservation nodeOk() {
        return new NodeReadinessObservation(true, true, "True", "Node k8s-worker1 Ready=True (KubeletReady)", "KubeletReady", 8L);
    }

    @Test
    void worker_tous_criteres_ok_une_passe_reussie() {
        PassResult r = verifier.verifyWorker(worker1, nodeOk());
        assertTrue(r.passed(), r.summary());
        assertEquals(List.of("node_ready", "kubelet_active", "ssh", "etcd_self", "etcd_has_leader", "cnpg_healthy",
                "backend_available"), List.copyOf(r.criteria().keySet()));
    }

    // ── Plan de contrôle (.153, consigne du 2026-11-17) : mêmes preuves qu'un worker + apiserver 6443 ──

    private static NodeReadinessObservation masterOk() {
        return new NodeReadinessObservation(true, true, "True", "Node k8s-master Ready=True (KubeletReady)", "KubeletReady", 8L);
    }

    @Test
    void master_tous_criteres_ok_une_passe_reussie_avec_l_apiserver_en_plus() {
        Entry master = VmPowerPolicy.find("k8s-master").orElseThrow();
        PassResult r = verifier.verifyWorker(master, masterOk());
        assertTrue(r.passed(), r.summary());
        assertEquals(List.of("node_ready", "kubelet_active", "ssh", "apiserver_6443", "etcd_self", "etcd_has_leader",
                "cnpg_healthy", "backend_available"), List.copyOf(r.criteria().keySet()));
    }

    @Test
    void master_apiserver_6443_en_echec_fait_echouer_la_passe_et_est_nomme() {
        Entry master = VmPowerPolicy.find("k8s-master").orElseThrow();
        tcpDown.add("192.168.148.153:6443");
        PassResult r = verifier.verifyWorker(master, masterOk());
        assertFalse(r.passed());
        assertFalse(r.criteria().get("apiserver_6443"));
        assertTrue(r.summary().contains("apiserver_6443"), r.summary());
        tcpDown.clear();
        assertTrue(verifier.verifyWorker(master, masterOk()).passed(), "l'apiserver revenu, la passe réussit");
    }

    @Test
    void master_chaque_preuve_complementaire_d_un_worker_est_aussi_exigee() {
        Entry master = VmPowerPolicy.find("k8s-master").orElseThrow();
        tcpDown.add("192.168.148.153:22");
        assertFalse(verifier.verifyWorker(master, masterOk()).criteria().get("ssh"));
        tcpDown.clear();
        etcdUnhealthy.add("192.168.148.153");
        assertFalse(verifier.verifyWorker(master, masterOk()).criteria().get("etcd_self"), "le membre etcd de .153 doit être sain");
        etcdUnhealthy.clear();
        etcdNoLeader.add("192.168.148.153");
        assertFalse(verifier.verifyWorker(master, masterOk()).criteria().get("etcd_has_leader"));
        etcdNoLeader.clear();
        PassResult stale = verifier.verifyWorker(master, new NodeReadinessObservation(true, true, "True", "m", "KubeletReady", 300L));
        assertFalse(stale.criteria().get("kubelet_active"), "kubelet muet malgré Ready=True résiduel");
    }

    @Test
    void master_pas_ready_court_circuite_les_autres_criteres_et_un_worker_n_a_pas_de_critere_apiserver() {
        Entry master = VmPowerPolicy.find("k8s-master").orElseThrow();
        PassResult notReady = verifier.verifyWorker(master,
                new NodeReadinessObservation(true, false, "False", "KubeletNotReady", "KubeletNotReady", 100L));
        assertFalse(notReady.passed());
        assertEquals(List.of("node_ready"), List.copyOf(notReady.criteria().keySet()), "inutile de sonder le reste d'un nœud non Ready");
        assertFalse(verifier.verifyWorker(worker1, nodeOk()).criteria().containsKey("apiserver_6443"),
                "le critère apiserver est propre au plan de contrôle");
    }

    @Test
    void master_lecture_effective_PROOFS_FAILED_si_un_critere_manque_comme_pour_un_worker() {
        Entry master = VmPowerPolicy.find("k8s-master").orElseThrow();
        NodeReadinessObservation raw = masterOk();
        assertTrue(verifier.effectiveWorkerObservation(master, raw).ready(), "tous critères OK : lecture inchangée");
        tcpDown.add("192.168.148.153:6443");
        NodeReadinessObservation effective = verifier.effectiveWorkerObservation(master, raw);
        assertFalse(effective.ready());
        assertEquals("PROOFS_FAILED", effective.conditionStatus());
        assertTrue(effective.message().contains("apiserver_6443"), effective.message());
    }

    @Test
    void worker_ready_ne_suffit_jamais_chaque_preuve_complementaire_est_exigee() {
        // kubelet : Lease périmé (kubelet muet malgré Ready=True résiduel)
        PassResult stale = verifier.verifyWorker(worker1,
                new NodeReadinessObservation(true, true, "True", "m", "KubeletReady", 300L));
        assertFalse(stale.passed());
        assertFalse(stale.criteria().get("kubelet_active"));
        // kubelet : forme historique (ni raison ni Lease) = non prouvé
        PassResult legacy = verifier.verifyWorker(worker1, new NodeReadinessObservation(true, true, "True", "Ready"));
        assertFalse(legacy.criteria().get("kubelet_active"), "pas de preuve de kubelet sans Lease ni raison");
        // SSH
        tcpDown.add("192.168.148.158:22");
        assertFalse(verifier.verifyWorker(worker1, nodeOk()).criteria().get("ssh"));
        tcpDown.clear();
        // etcd
        etcdUnhealthy.add("192.168.148.158");
        assertFalse(verifier.verifyWorker(worker1, nodeOk()).criteria().get("etcd_self"));
        etcdUnhealthy.clear();
        etcdNoLeader.add("192.168.148.158");
        assertFalse(verifier.verifyWorker(worker1, nodeOk()).criteria().get("etcd_has_leader"));
        etcdNoLeader.clear();
        // CNPG : réplica pas prêt (resynchronisation en cours), primaire pas prêt, API muette, un seul pod, deux primaires
        when(cluster.inspectPods(anyString(), anyString())).thenReturn(cnpg(true, false, true));
        assertFalse(verifier.verifyWorker(worker1, nodeOk()).criteria().get("cnpg_healthy"));
        when(cluster.inspectPods(anyString(), anyString())).thenReturn(cnpg(false, true, true));
        assertFalse(verifier.verifyWorker(worker1, nodeOk()).criteria().get("cnpg_healthy"));
        when(cluster.inspectPods(anyString(), anyString())).thenReturn(cnpg(true, true, false));
        assertFalse(verifier.verifyWorker(worker1, nodeOk()).criteria().get("cnpg_healthy"), "API muette = aucune preuve");
        when(cluster.inspectPods(anyString(), anyString())).thenReturn(new PodSetObservation(true,
                List.of(new PodState("postgres-5", "Running", true, Map.of("cnpg.io/instanceRole", "primary")))));
        assertFalse(verifier.verifyWorker(worker1, nodeOk()).criteria().get("cnpg_healthy"), "un seul pod : pas de réplica");
        when(cluster.inspectPods(anyString(), anyString())).thenReturn(new PodSetObservation(true, List.of(
                new PodState("postgres-5", "Running", true, Map.of("cnpg.io/instanceRole", "primary")),
                new PodState("postgres-7", "Running", true, Map.of("cnpg.io/instanceRole", "primary")))));
        assertFalse(verifier.verifyWorker(worker1, nodeOk()).criteria().get("cnpg_healthy"), "deux primaires : incohérent");
        // backend
        when(cluster.inspectPods(anyString(), anyString())).thenReturn(cnpg(true, true, true));
        when(cluster.isWorkloadHealthy(anyString(), anyString())).thenReturn(false);
        assertFalse(verifier.verifyWorker(worker1, nodeOk()).criteria().get("backend_available"));
    }

    @Test
    void worker_non_ready_echoue_sur_node_ready_seul_sans_sonder_le_reste() {
        PassResult r = verifier.verifyWorker(worker1, new NodeReadinessObservation(true, false, "False", "NotReady"));
        assertFalse(r.passed());
        assertEquals(List.of("node_ready"), List.copyOf(r.criteria().keySet()));
        assertFalse(verifier.verifyWorker(worker1, null).passed());
        assertFalse(verifier.verifyWorker(worker1, new NodeReadinessObservation(false, false, "UNKNOWN", "API KO")).passed());
    }

    @Test
    void lecture_effective_du_noeud_reflete_le_verdict_complet() {
        NodeReadinessObservation ok = nodeOk();
        NodeReadinessObservation eff = verifier.effectiveWorkerObservation(worker1, ok);
        assertTrue(eff.ready() && "True".equals(eff.conditionStatus()), "tout est OK : lecture inchangée");

        when(cluster.isWorkloadHealthy(anyString(), anyString())).thenReturn(false);
        NodeReadinessObservation bad = verifier.effectiveWorkerObservation(worker1, ok);
        assertFalse(bad.ready(), "Ready=True mais backend indisponible : le nœud n'est PAS « revenu »");
        assertEquals("PROOFS_FAILED", bad.conditionStatus());
        assertTrue(bad.message().contains("backend_available"), bad.message());
        assertTrue(bad.apiAvailable());

        // déjà non Ready / absent : retourné tel quel (rien à ajouter)
        NodeReadinessObservation notReady = new NodeReadinessObservation(true, false, "False", "NotReady");
        assertSame(notReady, verifier.effectiveWorkerObservation(worker1, notReady));
        assertNull(verifier.effectiveWorkerObservation(worker1, null));
    }

    @Test
    void verifier_sans_executeur_kubernetes_ne_prouve_ni_cnpg_ni_backend() {
        VmRecoveryVerifier sansCluster = new VmRecoveryVerifier(probes, null, new VmPowerProperties());
        PassResult r = sansCluster.verifyWorker(worker1, nodeOk());
        assertFalse(r.passed());
        assertFalse(r.criteria().get("cnpg_healthy"));
        assertFalse(r.criteria().get("backend_available"));
    }

    // ───────────────────────────── Divers ─────────────────────────────

    @Test
    void classe_non_supportee_n_est_jamais_un_succes() {
        PassResult r = verifier.verifyInfra(worker1, states);   // un worker n'est pas une VM d'infrastructure
        assertFalse(r.passed());
        assertTrue(r.criteria().containsKey("unsupported_class"));
    }

    @Test
    void etats_absents_donnent_une_passe_en_echec() {
        PassResult r = verifier.verifyInfra(lb2, null);
        assertFalse(r.passed());
        assertFalse(r.criteria().get("power_running"));
    }
}
