package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.TerminalCommandOutput;
import com.orange.pfe.backend.dto.TerminalEvidenceRequest;
import com.orange.pfe.backend.dto.TerminalEvidenceResponse;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * LOT HELIOS-INCIDENT-CARD-EVENT-INTEGRATION-01 — collecteur de preuves terminal.
 *
 * Fournit un **catalogue fixe** de commandes diagnostiques NON destructives (CPU/DISK) et transforme des
 * sorties DÉJÀ collectées en preuves persistées (via {@link IncidentCardService#recordEvidence}). **Le
 * backend n'exécute AUCUNE commande distante ici** : l'exécution réelle relève d'un mécanisme d'exécution
 * établi (watchdog/Bridge) à raccorder dans un lot autorisé (cf. rapport 04). Aucune commande arbitraire
 * fournie par l'utilisateur/HTTP n'est acceptée : seul le catalogue fixe est « autorisé ».
 */
@Service
public class TerminalEvidenceCollectorService {

    /** Commandes diagnostiques CPU (lecture seule, non destructives). */
    public static final List<String> CPU_COMMANDS = List.of(
            "date -Is",
            "uptime",
            "top -b -n 1",
            "systemctl is-active helios-cpuload.service");

    /** Commandes diagnostiques DISK (lecture seule, non destructives). */
    public static final List<String> DISK_COMMANDS = List.of(
            "date -Is",
            "df -hT /",
            "df -ih /",
            "lsblk");

    private final IncidentCardService cardService;

    public TerminalEvidenceCollectorService(IncidentCardService cardService) {
        this.cardService = cardService;
    }

    /** Catalogue fixe des commandes autorisées pour un scénario (CPU par défaut). */
    public List<String> catalog(String scenario) {
        return "DISK".equalsIgnoreCase(scenario) ? DISK_COMMANDS : CPU_COMMANDS;
    }

    /**
     * Enregistre des preuves à partir de sorties FOURNIES (jamais exécutées ici). Chaque sortie devient une
     * ligne de preuve ; les mesures sont extraites au mieux (jamais inventées). Réutilise l'anti-doublon de
     * recordEvidence.
     */
    @Transactional
    public List<TerminalEvidenceResponse> recordFromOutputs(
            Long incidentId, String vm, TerminalEvidencePhase phase, String scenario,
            String source, List<TerminalCommandOutput> outputs) {
        List<TerminalEvidenceResponse> res = new ArrayList<>();
        if (outputs == null) return res;
        final String src = (source == null || source.isBlank()) ? "COLLECTOR" : source;
        for (TerminalCommandOutput o : outputs) {
            if (o == null) continue;
            boolean available = o.command() != null
                    && (o.stdout() != null || o.stderr() != null || o.exitCode() != null);
            String measures = available ? extractMeasures(scenario, o) : null;
            TerminalEvidenceRequest req = new TerminalEvidenceRequest(
                    vm, phase, o.capturedAt(), o.command(), o.exitCode(), o.stdout(), o.stderr(),
                    measures, available, src);
            TerminalEvidenceResponse r = cardService.recordEvidence(incidentId, req);
            if (r != null) res.add(r);
        }
        return res;
    }

    /** Extraction best-effort de mesures depuis une sortie diagnostique. Null si rien d'exploitable. */
    String extractMeasures(String scenario, TerminalCommandOutput o) {
        if (o == null || o.command() == null || o.stdout() == null) return null;
        String cmd = o.command().trim();
        String out = o.stdout();
        Map<String, String> m = new LinkedHashMap<>();
        if (cmd.startsWith("systemctl is-active")) {
            m.put("helios_cpuload_active", out.trim());
        } else if (cmd.startsWith("df -hT")) {
            for (String line : out.split("\\r?\\n")) {
                String[] p = line.trim().split("\\s+");
                if (p.length >= 7 && "/".equals(p[p.length - 1])) {
                    m.put("disk_use_pct", p[p.length - 2]);
                    m.put("disk_avail", p[p.length - 3]);
                }
            }
        } else if (cmd.startsWith("df -ih")) {
            for (String line : out.split("\\r?\\n")) {
                String[] p = line.trim().split("\\s+");
                if (p.length >= 6 && "/".equals(p[p.length - 1])) {
                    m.put("inode_use_pct", p[p.length - 2]);
                }
            }
        } else if (cmd.startsWith("uptime")) {
            int i = out.indexOf("load average");
            if (i >= 0) m.put("load_average", out.substring(i).replace("load average:", "").trim());
        }
        if (m.isEmpty()) return null;
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, String> e : m.entrySet()) {
            if (!first) sb.append(",");
            first = false;
            sb.append("\"").append(e.getKey()).append("\":\"")
              .append(String.valueOf(e.getValue()).replace("\"", "'")).append("\"");
        }
        return sb.append("}").toString();
    }
}
