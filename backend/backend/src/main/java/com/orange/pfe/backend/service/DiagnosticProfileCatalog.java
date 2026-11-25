package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.enums.EvidenceProvenance;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * LOT HELIOS-TERMINAL-EVIDENCE-REAL-COLLECTION-01 — CATALOGUE FERMÉ de profils de diagnostic (lecture seule),
 * PRÉPARÉ pour une future collecte réelle. Il n'exécute RIEN et n'expose AUCUN champ de commande libre.
 *
 * Statut de transport : {@link #terminalTransportAvailable()} = false — aucun transport sûr, authentifié,
 * autorisé et en LECTURE SEULE n'existe aujourd'hui pour exécuter ces commandes dans une VM (les seuls
 * transports d'exécution présents sont privilégiés et à liste destructive ; cf. rapport 01). Ce catalogue
 * est donc un artefact de conception testable, pas un collecteur opérationnel.
 */
@Service
public class DiagnosticProfileCatalog {

    public static final String CPU = "CPU";
    public static final String DISK = "DISK";
    public static final String VM_RECOVERY = "VM_RECOVERY";

    private final Map<String, List<String>> profiles = new LinkedHashMap<>();

    public DiagnosticProfileCatalog() {
        // Profils FERMÉS, en lecture seule, à durée bornée. Aucune commande d'écriture/quota/nettoyage.
        profiles.put(CPU, List.of(
                "date -Is",
                "uptime",
                "top -b -n 1",
                "systemctl is-active helios-cpuload.service",
                "systemctl show helios-cpuload.service -p CPUQuotaPerSecUSec",
                "cat /proc/stat    # 2 echantillons horodates requis pour un % d'utilisation"));
        profiles.put(DISK, List.of(
                "date -Is",
                "df -hT /",
                "df -ih /",
                "lsblk"));
        // VM récupérée : preuves depuis une source EXTERNE à la VM éteinte, puis état réel du nœud.
        profiles.put(VM_RECOVERY, List.of(
                "externe:hote vmrun list                 # VM allumee ? (avant/apres, depuis l'hyperviseur)",
                "externe:prometheus up==0                # VM injoignable (etat initial, supervision)",
                "k8s:nodeReady(<node>)                    # etat reel du noeud Kubernetes (API lecture seule)",
                "k8s:etcd member health                   # sante du cluster apres recuperation"));
    }

    /** Profil fermé pour un scénario (null si inconnu — jamais une commande libre). */
    public List<String> profile(String scenario) {
        return scenario == null ? null : profiles.get(scenario.trim().toUpperCase());
    }

    public java.util.Set<String> scenarios() {
        return java.util.Collections.unmodifiableSet(profiles.keySet());
    }

    /**
     * false : aucun transport sûr/read-only/autorisé pour exécuter ces profils dans une VM aujourd'hui.
     * Tant que c'est false, aucune preuve ne doit être marquée REAL_EXECUTION (cf. rapports 01/05).
     */
    public boolean terminalTransportAvailable() {
        return false;
    }

    /** Une preuve ne peut être qualifiée de terminal réelle que si un transport existe ET la provenance l'atteste. */
    public boolean canQualifyAsRealTerminal(EvidenceProvenance provenance) {
        return terminalTransportAvailable() && provenance != null && provenance.isRealTerminal();
    }
}
