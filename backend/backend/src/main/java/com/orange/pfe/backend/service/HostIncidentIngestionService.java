package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.HostIncidentIngestionRequest;
import com.orange.pfe.backend.dto.HostIncidentIngestionResponse;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Optional;

/**
 * LOT HELIOS-INCIDENT-CARD-EVENT-INTEGRATION-01 — persiste (ou corrèle) une carte pour un incident
 * host-mesuré (ex. NODE_HIGH_CPU) qui, sans cela, transite par Camunda-direct → Bridge sans entité
 * backend. Réutilise l'entité {@code Incident} et les mécanismes de déduplication existants. Source =
 * {@code PROMETHEUS} (détecteur déterministe, PAS l'IA).
 *
 * Corrélation / épisodes (documentée) : un même type+cible ACTIF => corrélation (pas de doublon) ; un
 * incident récent (&lt; 5 min) même résolu => corrélation (anti-flapping) ; sinon nouvel épisode. SANS
 * début-d'épisode/ID fourni par la source, deux épisodes du même type+cible ne sont distingués que si le
 * précédent est clos ET hors fenêtre de 5 min (limite assumée ; une clé d'épisode explicite lèverait
 * l'ambiguïté — cf. rapport 02).
 */
@Service
public class HostIncidentIngestionService {

    private static final Logger log = LoggerFactory.getLogger(HostIncidentIngestionService.class);
    static final List<IncidentStatus> ACTIVE = List.of(
            IncidentStatus.NEW, IncidentStatus.OPEN, IncidentStatus.IN_PROGRESS, IncidentStatus.WAITING_FOR_APPROVAL);
    static final IncidentSource SOURCE = IncidentSource.PROMETHEUS;
    private static final int FLAP_MIN = 5;

    private final IncidentRepository repo;
    private final IncidentHistoryService history;
    private final HostCpuIncidentRemediationOrchestrator cpuRemediation;
    private final HostDiskIncidentRemediationOrchestrator diskRemediation;
    private final HostMemoryIncidentRemediationOrchestrator memoryRemediation;

    @org.springframework.beans.factory.annotation.Autowired
    public HostIncidentIngestionService(
            IncidentRepository repo,
            IncidentHistoryService history,
            HostCpuIncidentRemediationOrchestrator cpuRemediation,
            HostDiskIncidentRemediationOrchestrator diskRemediation,
            HostMemoryIncidentRemediationOrchestrator memoryRemediation) {
        this.repo = repo;
        this.history = history;
        this.cpuRemediation = cpuRemediation;
        this.diskRemediation = diskRemediation;
        this.memoryRemediation = memoryRemediation;
    }

    // Compatibilité : constructeur sans orchestrateur mémoire (memoryRemediation = null).
    public HostIncidentIngestionService(
            IncidentRepository repo,
            IncidentHistoryService history,
            HostCpuIncidentRemediationOrchestrator cpuRemediation,
            HostDiskIncidentRemediationOrchestrator diskRemediation) {
        this(repo, history, cpuRemediation, diskRemediation, null);
    }

    // Compatibilité avec les tests et appels utilisant l'ancien constructeur à trois arguments.
    public HostIncidentIngestionService(
            IncidentRepository repo,
            IncidentHistoryService history,
            HostCpuIncidentRemediationOrchestrator cpuRemediation) {
        this(repo, history, cpuRemediation, null);
    }

    // Compatibilité avec les tests historiques sans orchestrateur.
    public HostIncidentIngestionService(
            IncidentRepository repo,
            IncidentHistoryService history) {
        this(repo, history, null, null);
    }
    @Transactional
    public HostIncidentIngestionResponse ingest(HostIncidentIngestionRequest req) {
        if (isBlank(req.incidentType()) || isBlank(req.target())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "incidentType et target obligatoires — aucun incident créé sans données suffisantes");
        }
        final String ns = isBlank(req.namespace()) ? "k8s-nodes" : req.namespace().trim();
        final String app = req.target().trim();
        final String type = req.incidentType().trim();
        final String times = "detecte=" + str(req.detectedAt()) + " observe=" + str(req.observedAt())
                + " persiste=" + LocalDateTime.now();

        // 1) Épisode ACTIF du même type+cible => corrélation (pas de doublon).
        Optional<Incident> active = repo
                .findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(ACTIVE, SOURCE, ns, app)
                .filter(i -> type.equalsIgnoreCase(i.getIncidentType()));
        if (active.isPresent()) {
            Incident inc = active.get();
            history.record(inc, inc.getStatus(),
                    "[INGEST] re-observation correlee a l'episode actif (" + type + " ; " + times + ")");
            log.info("Host-ingest: correle incident actif #{} ({}/{} {})", inc.getId(), ns, app, type);
            return new HostIncidentIngestionResponse(inc.getId(), type, app, false, true, inc.getStatus().name());
        }

        // 2) Anti-flapping : incident récent (<5 min), même résolu, du même type+cible => corréler.
        Optional<Incident> recent = repo.findBySource(SOURCE).stream()
                .filter(i -> ns.equalsIgnoreCase(i.getNamespace()) && app.equalsIgnoreCase(i.getApp()))
                .filter(i -> type.equalsIgnoreCase(i.getIncidentType()))
                .filter(i -> i.getCreatedAt() != null
                        && i.getCreatedAt().isAfter(LocalDateTime.now().minusMinutes(FLAP_MIN)))
                .max(Comparator.comparing(Incident::getCreatedAt));
        if (recent.isPresent()) {
            Incident inc = recent.get();
            history.record(inc, inc.getStatus(),
                    "[INGEST] re-observation <" + FLAP_MIN + "min correlee (anti-doublon ; " + times + ")");
            return new HostIncidentIngestionResponse(inc.getId(), type, app, false, true, inc.getStatus().name());
        }

        // 3) Nouvel épisode => créer l'incident persistant (la carte existe désormais).
        Incident inc = new Incident();
        inc.setSource(SOURCE);
        inc.setStatus(IncidentStatus.OPEN);
        inc.setNamespace(ns);
        inc.setApp(app);
        inc.setIncidentType(type);
        inc.setSeverity(parseSeverity(req.severity(), type));
        inc.setTitle("Host-mesure: " + type + " sur " + app);
        inc.setDescription(buildDescription(req, times));
        inc = repo.save(inc);
        history.record(inc, IncidentStatus.OPEN, "[INGEST] incident host-mesure cree — nouvel episode (" + times + ")");
        log.info("Host-ingest: cree incident #{} ({}/{} {})", inc.getId(), ns, app, type);
        boolean cpuIncident =
                "NODE_HIGH_CPU".equalsIgnoreCase(type)
                || "NODE_CPU_SATURATION".equalsIgnoreCase(type);

        boolean diskIncident =
                "NODE_DISK_FULL".equalsIgnoreCase(type)
                || "NODE_DISK_PRESSURE".equalsIgnoreCase(type)
                || "NODE_INODES_FULL".equalsIgnoreCase(type);

        boolean memoryIncident =
                "NODE_HIGH_MEMORY".equalsIgnoreCase(type)
                || "NODE_MEMORY_PRESSURE".equalsIgnoreCase(type);

        boolean dispatchCpu = cpuRemediation != null && cpuIncident;
        boolean dispatchDisk = diskRemediation != null && diskIncident;
        boolean dispatchMemory = memoryRemediation != null && memoryIncident;

        if (dispatchCpu || dispatchDisk || dispatchMemory) {
            if (!org.springframework.transaction.support.TransactionSynchronizationManager
                    .isSynchronizationActive()) {
                throw new IllegalStateException(
                        "Host remediation dispatch requires an active Spring transaction");
            }

            final Incident committedIncident = inc;

            org.springframework.transaction.support.TransactionSynchronizationManager
                    .registerSynchronization(
                            new org.springframework.transaction.support.TransactionSynchronization() {
                                @Override
                                public void afterCommit() {
                                    if (dispatchCpu) {
                                        try {
                                            cpuRemediation.startIfEligible(committedIncident);
                                        } catch (RuntimeException ex) {
                                            log.error(
                                                    "CPU remediation dispatch failed after commit for incident {}",
                                                    committedIncident.getId(), ex);
                                        }
                                    }

                                    if (dispatchDisk) {
                                        try {
                                            diskRemediation.startIfEligible(committedIncident);
                                        } catch (RuntimeException ex) {
                                            log.error(
                                                    "DISK remediation dispatch failed after commit for incident {}",
                                                    committedIncident.getId(), ex);
                                        }
                                    }

                                    if (dispatchMemory) {
                                        try {
                                            memoryRemediation.startIfEligible(committedIncident);
                                        } catch (RuntimeException ex) {
                                            log.error(
                                                    "RAM remediation dispatch failed after commit for incident {}",
                                                    committedIncident.getId(), ex);
                                        }
                                    }
                                }
                            });
        }

        // RAM — quand l'orchestrateur memoire est cable (dispatchMemory ci-dessus), c'est LUI qui decide :
        // remediation reelle (modele CPU : cible labo memoire + Bridge) si les preconditions de surete sont
        // reunies, sinon escalade deterministe fail-closed. Le repli ci-dessous ne sert QUE lorsqu'aucun
        // orchestrateur memoire n'est cable (tests historiques / demarrage degrade) : on trace alors
        // directement l'escalade (aucune action, aucun Bridge/eviction ; incident laisse OUVERT).
        if (memoryIncident && memoryRemediation == null) {
            history.record(inc, IncidentStatus.OPEN,
                    "[RAM] Escalade deterministe : aucun orchestrateur memoire cable "
                    + "(aucune action auto executee, aucun Bridge/eviction). "
                    + "Incident laisse OUVERT pour intervention humaine (fail-closed).");
            log.warn("RAM incident #{} ({}/{} {}) escalade (repli sans orchestrateur) : aucune remediation auto",
                    inc.getId(), ns, app, type);
        }

        return new HostIncidentIngestionResponse(inc.getId(), type, app, true, false, inc.getStatus().name());
    }

    private static boolean isBlank(String s) { return s == null || s.isBlank(); }
    private static String str(LocalDateTime t) { return t == null ? "NON_COLLECTE" : t.toString(); }

    private String buildDescription(HostIncidentIngestionRequest req, String times) {
        StringBuilder sb = new StringBuilder("Incident host-mesure (detecteur deterministe, source=PROMETHEUS). ");
        if (!isBlank(req.detail())) sb.append(req.detail().trim()).append(". ");
        sb.append(times);
        String d = sb.toString();
        return d.length() > 1000 ? d.substring(0, 997) + "..." : d;
    }

    private IncidentSeverity parseSeverity(String s, String incidentType) {
        // Ces types sont classes CRITICAL par le detecteur HELIOS actif.
        // La metrique Prometheus ne transmet pas la severite.
        if ("NODE_DISK_FULL".equalsIgnoreCase(incidentType)
                || "NODE_DISK_PRESSURE".equalsIgnoreCase(incidentType)
                || "NODE_INODES_FULL".equalsIgnoreCase(incidentType)) {
            return IncidentSeverity.CRITICAL;
        }

        // Conserver le comportement historique des autres types.
        if (isBlank(s)) return IncidentSeverity.MEDIUM;
        try {
            return IncidentSeverity.valueOf(s.trim().toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            return IncidentSeverity.MEDIUM;
        }
    }
}
