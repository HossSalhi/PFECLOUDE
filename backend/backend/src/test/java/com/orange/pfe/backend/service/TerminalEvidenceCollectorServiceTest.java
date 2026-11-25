package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.TerminalCommandOutput;
import com.orange.pfe.backend.dto.TerminalEvidenceRequest;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * LOT HELIOS-INCIDENT-CARD-EVENT-INTEGRATION-01 — Phase G. Collecteur de preuves : catalogue fixe,
 * extraction best-effort des mesures (df, systemctl), et marquage NON COLLECTÉ quand la sortie est
 * indisponible. Le collecteur n'exécute AUCUNE commande : il consomme des sorties fournies.
 */
class TerminalEvidenceCollectorServiceTest {

    private IncidentCardService cardService;
    private TerminalEvidenceCollectorService collector;

    @BeforeEach
    void setUp() {
        cardService = mock(IncidentCardService.class);
        collector = new TerminalEvidenceCollectorService(cardService);
        when(cardService.recordEvidence(anyLong(), any(TerminalEvidenceRequest.class)))
                .thenAnswer(a -> null); // on capture la requête, le retour n'importe pas ici
    }

    @Test
    void catalogue_cpuEtDisk_sontFixesEtNonVides() {
        assertTrue(collector.catalog("CPU").contains("systemctl is-active helios-cpuload.service"));
        assertTrue(collector.catalog("DISK").contains("df -hT /"));
        // valeur inconnue => CPU par défaut (jamais une commande arbitraire)
        assertTrue(collector.catalog("autre").contains("uptime"));
    }

    @Test
    void extractMeasures_df_extraitUsePctEtAvail() {
        TerminalCommandOutput o = new TerminalCommandOutput(
                "df -hT /",
                0,
                "Filesystem     Type  Size  Used Avail Use% Mounted on\n/dev/sda1      ext4   50G   40G    8G  84% /",
                "", null);
        String m = collector.extractMeasures("DISK", o);
        assertNotNull(m);
        assertTrue(m.contains("disk_use_pct"));
        assertTrue(m.contains("84%"));
    }

    @Test
    void extractMeasures_systemctl_extraitEtatService() {
        TerminalCommandOutput o = new TerminalCommandOutput(
                "systemctl is-active helios-cpuload.service", 0, "active", "", null);
        String m = collector.extractMeasures("CPU", o);
        assertNotNull(m);
        assertTrue(m.contains("helios_cpuload_active"));
        assertTrue(m.contains("active"));
    }

    @Test
    void recordFromOutputs_persisteEtMarqueDisponibilite() {
        TerminalCommandOutput dispo = new TerminalCommandOutput(
                "df -hT /", 0, "/dev/sda1 ext4 50G 40G 8G 84% /", "", null);
        TerminalCommandOutput absent = new TerminalCommandOutput(
                "df -ih /", null, null, null, null); // rien => NON DISPONIBLE

        collector.recordFromOutputs(1L, "k8s-worker1", TerminalEvidencePhase.BEFORE, "DISK",
                "COLLECTOR", List.of(dispo, absent));

        ArgumentCaptor<TerminalEvidenceRequest> cap = ArgumentCaptor.forClass(TerminalEvidenceRequest.class);
        verify(cardService, org.mockito.Mockito.times(2)).recordEvidence(anyLong(), cap.capture());
        List<TerminalEvidenceRequest> reqs = cap.getAllValues();

        TerminalEvidenceRequest r0 = reqs.get(0); // df -hT (disponible, mesures extraites)
        assertTrue(r0.available());
        assertNotNull(r0.measuresJson());

        TerminalEvidenceRequest r1 = reqs.get(1); // df -ih sans sortie (NON DISPONIBLE, pas de mesure inventée)
        assertFalse(r1.available());
        assertNull(r1.measuresJson());
    }
}
