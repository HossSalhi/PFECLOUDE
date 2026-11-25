package com.orange.pfe.backend.service.vmpower;

import com.orange.pfe.backend.config.VmPowerProperties;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.Entry;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.VmClass;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * VMDOWN-POLICY-20261105 — sondes réseau INDÉPENDANTES de .160 (ni Prometheus, ni le détecteur, ni le Bridge) :
 * connexions TCP et requête DNS UDP, exécutées EN PARALLÈLE avec des délais BORNÉS. Une VM dont toutes les sondes
 * échouent est « muette » ; combinée à l'état d'alimentation prouvé par le watchdog, elle fonde la détection.
 *
 * <p>Classe volontairement sans état métier : le pire cas d'un cycle est {@code timeout + marge} quel que soit le
 * nombre de VM muettes (c'est ce qui manquait au contrôle d'infrastructure séquentiel, bloqué ~30 s par .160).
 */
@Component
public class VmProbes {

    private static final Logger log = LoggerFactory.getLogger(VmProbes.class);

    /** Résultat de vivacité d'une VM : au moins une sonde réussie => la VM répond. */
    public record Liveness(int ok, int total, List<String> okProbes, List<String> failedProbes) {
        /** Toutes les sondes ont échoué (et il y en avait). */
        public boolean allFailed() {
            return total > 0 && ok == 0;
        }
    }

    private static final Map<VmClass, int[]> TCP_PORTS = Map.of(
            VmClass.K8S_WORKER, new int[]{22, 10250, 9100},
            VmClass.K8S_CONTROL_PLANE, new int[]{22, 6443, 2381},
            VmClass.INFRA_LB, new int[]{22, 53, 80, 443, 6443, 8404, 2381},
            VmClass.INFRA_MONITORING, new int[]{22, 9090, 3000, 3100, 2381});

    private final VmPowerProperties props;
    private final ExecutorService pool;
    private final HttpClient http;

    public VmProbes(VmPowerProperties props) {
        this.props = props;
        AtomicInteger n = new AtomicInteger();
        this.pool = Executors.newFixedThreadPool(Math.max(2, props.getProbeThreads()), r -> {
            Thread t = new Thread(r, "vmprobe-" + n.incrementAndGet());
            t.setDaemon(true);
            return t;
        });
        this.http = HttpClient.newBuilder().connectTimeout(Duration.ofMillis(Math.max(200, props.getProbeTimeoutMs())))
                .version(HttpClient.Version.HTTP_1_1).build();
    }

    @PreDestroy
    void shutdown() {
        pool.shutdownNow();
    }

    /** Vivacité de toutes les VM de la table, sondes parallélisées (une seule vague, délai borné). */
    public Map<String, Liveness> livenessAll(Collection<Entry> entries) {
        Map<String, Map<String, Future<Boolean>>> submitted = new LinkedHashMap<>();
        for (Entry e : entries) {
            Map<String, Future<Boolean>> byProbe = new LinkedHashMap<>();
            for (int port : TCP_PORTS.getOrDefault(e.vmClass(), new int[]{22})) {
                byProbe.put("tcp/" + port, pool.submit(() -> tcp(e.ip(), port)));
            }
            if (e.vmClass() == VmClass.INFRA_LB || e.vmClass() == VmClass.INFRA_MONITORING) {
                byProbe.put("udp/53", pool.submit(() -> dnsAnswers(e.ip())));
            }
            submitted.put(e.name(), byProbe);
        }
        return collect(submitted);
    }

    /** Sondes explicites (tests et vérifications ciblées) : mêmes règles, ports fournis par l'appelant. */
    public Liveness livenessOf(String ip, int[] tcpPorts, boolean dns) {
        Map<String, Future<Boolean>> byProbe = new LinkedHashMap<>();
        for (int port : tcpPorts) {
            byProbe.put("tcp/" + port, pool.submit(() -> tcp(ip, port)));
        }
        if (dns) {
            byProbe.put("udp/53", pool.submit(() -> dnsAnswers(ip)));
        }
        return collect(Map.of("x", byProbe)).get("x");
    }

    private Map<String, Liveness> collect(Map<String, Map<String, Future<Boolean>>> submitted) {
        long wait = Math.max(500, props.getProbeTimeoutMs()) + 1000L;
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(wait);
        Map<String, Liveness> out = new LinkedHashMap<>();
        for (Map.Entry<String, Map<String, Future<Boolean>>> vm : submitted.entrySet()) {
            List<String> ok = new ArrayList<>();
            List<String> ko = new ArrayList<>();
            for (Map.Entry<String, Future<Boolean>> p : vm.getValue().entrySet()) {
                boolean up;
                try {
                    long left = Math.max(1, TimeUnit.NANOSECONDS.toMillis(deadline - System.nanoTime()));
                    up = Boolean.TRUE.equals(p.getValue().get(left, TimeUnit.MILLISECONDS));
                } catch (Exception ex) {
                    p.getValue().cancel(true);
                    up = false;
                }
                (up ? ok : ko).add(p.getKey());
            }
            out.put(vm.getKey(), new Liveness(ok.size(), ok.size() + ko.size(), List.copyOf(ok), List.copyOf(ko)));
        }
        return out;
    }

    /** Connexion TCP bornée. */
    public boolean tcp(String ip, int port) {
        try (Socket s = new Socket()) {
            s.connect(new InetSocketAddress(ip, port), Math.max(200, props.getProbeTimeoutMs()));
            return true;
        } catch (IOException ex) {
            return false;
        }
    }

    /** Requête DNS UDP : TOUTE réponse (même NXDOMAIN/REFUSED) prouve que le résolveur est vivant. */
    public boolean dnsAnswers(String ip) {
        return dnsAnswers(ip, 53);
    }

    boolean dnsAnswers(String ip, int port) {
        try (DatagramSocket socket = new DatagramSocket()) {
            socket.setSoTimeout(Math.max(200, props.getProbeTimeoutMs()));
            byte[] query = dnsQuery("kubernetes.default.svc.cluster.local");
            socket.send(new DatagramPacket(query, query.length, InetAddress.getByName(ip), port));
            byte[] buf = new byte[512];
            DatagramPacket reply = new DatagramPacket(buf, buf.length);
            socket.receive(reply);
            return reply.getLength() >= 12 && buf[0] == query[0] && buf[1] == query[1];  // même identifiant de requête
        } catch (IOException ex) {
            return false;
        }
    }

    static byte[] dnsQuery(String name) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(0x48); out.write(0x31);          // identifiant
        out.write(0x01); out.write(0x00);          // RD
        out.write(0x00); out.write(0x01);          // QDCOUNT = 1
        out.write(0); out.write(0); out.write(0); out.write(0); out.write(0); out.write(0);
        for (String label : name.split("\\.")) {
            byte[] b = label.getBytes(StandardCharsets.US_ASCII);
            out.write(b.length);
            out.write(b, 0, b.length);
        }
        out.write(0);
        out.write(0x00); out.write(0x01);          // QTYPE A
        out.write(0x00); out.write(0x01);          // QCLASS IN
        return out.toByteArray();
    }

    /** GET HTTP borné ; corps si 2xx, sinon {@code null}. */
    public String httpGet(String url) {
        try {
            HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                    .timeout(Duration.ofMillis(Math.max(500, props.getProbeTimeoutMs()))).GET().build();
            HttpResponse<String> r = http.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            return r.statusCode() / 100 == 2 ? r.body() : null;
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            return null;
        } catch (Exception ex) {
            log.debug("sonde HTTP {} -> {}", url, ex.getClass().getSimpleName());
            return null;
        }
    }
}
