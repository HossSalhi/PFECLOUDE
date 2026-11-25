package com.orange.pfe.backend.service.vmpower;

import com.orange.pfe.backend.config.VmPowerProperties;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * VMDOWN-POLICY-20261105 — sondes réseau indépendantes de .160 : TCP et DNS UDP, parallèles, à délai BORNÉ.
 * Uniquement des sockets locaux (127.0.0.1) : aucun réseau externe, aucune VM.
 */
class VmProbesTest {

    private VmProbes probes;
    private final List<AutoCloseable> toClose = new ArrayList<>();

    @BeforeEach
    void setUp() {
        VmPowerProperties props = new VmPowerProperties();
        props.setProbeTimeoutMs(400);
        probes = new VmProbes(props);
    }

    @AfterEach
    void tearDown() throws Exception {
        for (AutoCloseable c : toClose) {
            c.close();
        }
        probes.shutdown();
    }

    private int openTcpPort() throws Exception {
        ServerSocket s = new ServerSocket(0, 50, InetAddress.getLoopbackAddress());
        toClose.add(s);
        return s.getLocalPort();
    }

    private int closedTcpPort() throws Exception {
        try (ServerSocket s = new ServerSocket(0, 1, InetAddress.getLoopbackAddress())) {
            return s.getLocalPort();   // fermé dès la sortie du try : plus personne n'écoute
        }
    }

    /** Résolveur DNS factice : répond à toute requête en renvoyant le même identifiant, drapeau QR=1, RCODE=3. */
    private int startDnsResponder(boolean answer) throws Exception {
        DatagramSocket socket = new DatagramSocket(0, InetAddress.getLoopbackAddress());
        toClose.add(socket);
        Thread t = new Thread(() -> {
            byte[] buf = new byte[512];
            while (!socket.isClosed()) {
                try {
                    DatagramPacket p = new DatagramPacket(buf, buf.length);
                    socket.receive(p);
                    if (answer) {
                        byte[] resp = new byte[12];
                        System.arraycopy(buf, 0, resp, 0, 2);   // même identifiant
                        resp[2] = (byte) 0x81;                    // QR=1, RD=1
                        resp[3] = (byte) 0x83;                    // RA=1, RCODE=3 (NXDOMAIN) : le résolveur est vivant
                        socket.send(new DatagramPacket(resp, resp.length, p.getAddress(), p.getPort()));
                    }
                } catch (Exception ex) {
                    return;
                }
            }
        }, "fake-dns");
        t.setDaemon(true);
        t.start();
        return socket.getLocalPort();
    }

    @Test
    void tcp_ouvert_reussit_ferme_echoue() throws Exception {
        assertTrue(probes.tcp("127.0.0.1", openTcpPort()));
        assertFalse(probes.tcp("127.0.0.1", closedTcpPort()));
    }

    @Test
    void dns_toute_reponse_prouve_la_vie_meme_nxdomain_et_le_silence_echoue() throws Exception {
        assertTrue(probes.dnsAnswers("127.0.0.1", startDnsResponder(true)));
        assertFalse(probes.dnsAnswers("127.0.0.1", startDnsResponder(false)), "résolveur muet => échec");
    }

    @Test
    void la_requete_dns_est_une_requete_A_valide() {
        byte[] q = VmProbes.dnsQuery("kubernetes.default.svc.cluster.local");
        assertEquals(0x48, q[0] & 0xff);
        assertEquals(0x31, q[1] & 0xff);
        assertEquals(0x01, q[2] & 0xff);   // RD
        assertEquals(1, q[5]);             // QDCOUNT = 1
        assertEquals(10, q[12]);           // longueur du premier label « kubernetes »
        assertArrayEquals(new byte[]{0, 0, 1, 0, 1}, java.util.Arrays.copyOfRange(q, q.length - 5, q.length));
    }

    @Test
    void liveness_agrege_au_moins_une_sonde_reussie_signifie_vivante() throws Exception {
        int open = openTcpPort();
        int closed = closedTcpPort();
        VmProbes.Liveness mixed = probes.livenessOf("127.0.0.1", new int[]{open, closed}, false);
        assertEquals(1, mixed.ok());
        assertEquals(2, mixed.total());
        assertFalse(mixed.allFailed());

        VmProbes.Liveness dead = probes.livenessOf("127.0.0.1", new int[]{closed, closedTcpPort()}, false);
        assertTrue(dead.allFailed());
        assertEquals(0, dead.ok());
        assertEquals(2, dead.failedProbes().size());
    }

    @Test
    void les_sondes_sont_paralleles_et_le_pire_cas_est_borne() {
        // 14 sondes vers une adresse de « trou noir » : en séquence elles dureraient 14 x 400 ms = 5,6 s.
        int[] ports = new int[14];
        for (int i = 0; i < ports.length; i++) {
            ports[i] = 20000 + i;
        }
        long t0 = System.nanoTime();
        VmProbes.Liveness l = probes.livenessOf("10.255.255.1", ports, false);
        long elapsedMs = (System.nanoTime() - t0) / 1_000_000;
        assertTrue(l.allFailed());
        assertTrue(elapsedMs < 3_500, "sondes en parallèle, délai borné : " + elapsedMs + " ms");
    }
}
