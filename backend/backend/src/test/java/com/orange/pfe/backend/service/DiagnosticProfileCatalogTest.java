package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.enums.EvidenceProvenance;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * LOT HELIOS-TERMINAL-EVIDENCE-REAL-COLLECTION-01 — Phase D (local, SANS réseau ni VM). Vérifie que les
 * profils sont fermés, que le transport terminal est signalé INDISPONIBLE, et que la provenance empêche
 * de qualifier une donnée de preuve réelle sans exécution avérée.
 */
class DiagnosticProfileCatalogTest {

    private final DiagnosticProfileCatalog catalog = new DiagnosticProfileCatalog();

    @Test
    void profils_fermes_nonVides_incluantVmRecovery() {
        assertNotNull(catalog.profile("CPU"));
        assertTrue(catalog.profile("CPU").stream().anyMatch(c -> c.contains("helios-cpuload.service")));
        assertNotNull(catalog.profile("DISK"));
        assertTrue(catalog.profile("DISK").contains("df -hT /"));
        assertNotNull(catalog.profile(DiagnosticProfileCatalog.VM_RECOVERY));
        assertEquals(3, catalog.scenarios().size());
        // Aucun profil libre : un scénario inconnu ne renvoie PAS de commande.
        assertNull(catalog.profile("ARBITRAIRE"));
    }

    @Test
    void transport_terminal_estIndisponible() {
        assertFalse(catalog.terminalTransportAvailable(),
                "aucun transport sûr/read-only n'existe : la collecte terminal réelle n'est pas opérationnelle");
    }

    @Test
    void provenance_empecheFauxReel() {
        assertTrue(EvidenceProvenance.REAL_EXECUTION.isRealTerminal());
        assertFalse(EvidenceProvenance.FIXTURE.isRealTerminal());
        assertFalse(EvidenceProvenance.PROMETHEUS.isRealTerminal());   // métrique != terminal
        assertFalse(EvidenceProvenance.KUBERNETES_API.isRealTerminal()); // état nœud != terminal
        assertEquals(EvidenceProvenance.FIXTURE, EvidenceProvenance.fromSource("FIXTURE"));
        assertEquals(EvidenceProvenance.REAL_EXECUTION, EvidenceProvenance.fromSource("WATCHDOG"));
        assertEquals(EvidenceProvenance.UNAVAILABLE, EvidenceProvenance.fromSource(null));
        // Même une provenance REAL_EXECUTION ne peut être qualifiée réelle tant que le transport manque.
        assertFalse(catalog.canQualifyAsRealTerminal(EvidenceProvenance.REAL_EXECUTION));
    }
}
