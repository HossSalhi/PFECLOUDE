
package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.ExecutionEventRequest;
import com.orange.pfe.backend.dto.IncidentCardResponse;
import com.orange.pfe.backend.dto.IncidentVerificationRequest;
import com.orange.pfe.backend.dto.IncidentVerificationResponse;
import com.orange.pfe.backend.dto.TerminalEvidenceRequest;
import com.orange.pfe.backend.dto.TerminalEvidenceResponse;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.core.IncidentHistory;
import com.orange.pfe.backend.model.core.IncidentTerminalEvidence;
import com.orange.pfe.backend.model.core.IncidentVerification;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.IncidentTerminalEvidenceRepository;
import com.orange.pfe.backend.repository.IncidentVerificationRepository;
import com.orange.pfe.backend.service.remediation.NodeReadinessObservation;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.Comparator;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * HELIOS - Incident Identity Card.
 *
 * Assemble la carte d'identité d'un incident à partir des données persistées.
 *
 * Ajouts VM_DOWN :
 * - Persistance des observations Kubernetes réelles.
 * - Séparation des observations par incident et workflow.
 * - Validation de deux observations Ready consécutives.
 * - Intervalle minimal de 15 secondes.
 * - Refus du verdict PASS générique pour les incidents de récupération de nœud.
 *
 * Cette classe n'exécute aucune commande distante.
 * Les observations proviennent de l'exécuteur Kubernetes.
 */
@Service
public class IncidentCardService {

    private static final Logger log =
            LoggerFactory.getLogger(IncidentCardService.class);

    private static final String NODE_READY_SOURCE_PREFIX = "K8S_API_WF_";

    private static final int NODE_READY_MIN_INTERVAL_SECONDS = 15;

    private static final Pattern NODE_SAMPLE_PATTERN =
            Pattern.compile("\"sample\"\\s*:\\s*(\\d+)");

    private final IncidentRepository incidentRepository;
    private final IncidentTerminalEvidenceRepository evidenceRepository;
    private final IncidentVerificationRepository verificationRepository;
    private final IncidentHistoryService historyService;

    public IncidentCardService(
            IncidentRepository incidentRepository,
            IncidentTerminalEvidenceRepository evidenceRepository,
            IncidentVerificationRepository verificationRepository,
            IncidentHistoryService historyService) {

        this.incidentRepository = incidentRepository;
        this.evidenceRepository = evidenceRepository;
        this.verificationRepository = verificationRepository;
        this.historyService = historyService;
    }

    /**
     * Enregistre une étape d'exécution réelle dans la chronologie.
     */
    @Transactional
    public void recordExecutionEvent(Long id, ExecutionEventRequest req) {

        if (req == null) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Événement d'exécution invalide"
            );
        }

        Incident inc = require(id);

        String success = req.success() == null
                ? "inconnu"
                : req.success().toString();

        String action = req.action() != null && !req.action().isBlank()
                ? req.action() + " — "
                : "";

        String detail = req.detail() != null
                ? req.detail()
                : "";

        historyService.record(
                inc,
                inc.getStatus(),
                "[EXEC:" + req.stage() + "] "
                        + action
                        + detail
                        + " (success=" + success + ")"
        );
    }

    private Incident require(Long id) {

        return incidentRepository.findById(id)
                .orElseThrow(() ->
                        new ResponseStatusException(
                                HttpStatus.NOT_FOUND,
                                "incident " + id + " introuvable"
                        )
                );
    }

    @Transactional(readOnly = true)
    public IncidentCardResponse buildCard(Long id) {

        Incident inc = require(id);

        IncidentCardResponse.CardIdentity identity =
                new IncidentCardResponse.CardIdentity(
                        inc.getId(),
                        inc.getTitle(),
                        inc.getIncidentType(),
                        inc.getSeverity() != null
                                ? inc.getSeverity().name()
                                : null,
                        inc.getStatus() != null
                                ? inc.getStatus().name()
                                : null,
                        inc.getSource() != null
                                ? inc.getSource().name()
                                : null,
                        inc.getNamespace(),
                        inc.getApp(),
                        inc.getCreatedAt(),
                        inc.getUpdatedAt(),
                        inc.getDescription(),
                        inc.getExplanation()
                );

        List<IncidentCardResponse.CardTimelineEntry> timeline =
                inc.getHistory().stream()
                        .sorted(
                                Comparator.comparing(
                                        IncidentHistory::getCreatedAt,
                                        Comparator.nullsLast(
                                                Comparator.naturalOrder()
                                        )
                                )
                        )
                        .map(h ->
                                new IncidentCardResponse.CardTimelineEntry(
                                        h.getId(),
                                        h.getStatus() != null
                                                ? h.getStatus().name()
                                                : null,
                                        h.getComment(),
                                        h.getCreatedAt()
                                )
                        )
                        .toList();

        List<TerminalEvidenceResponse> before =
                evidenceRepository
                        .findByIncidentIdAndPhaseOrderByCapturedAtAsc(
                                id,
                                TerminalEvidencePhase.BEFORE
                        )
                        .stream()
                        .map(TerminalEvidenceResponse::from)
                        .toList();

        List<TerminalEvidenceResponse> after =
                evidenceRepository
                        .findByIncidentIdAndPhaseOrderByCapturedAtAsc(
                                id,
                                TerminalEvidencePhase.AFTER
                        )
                        .stream()
                        .map(TerminalEvidenceResponse::from)
                        .toList();

        IncidentVerificationResponse verif =
                verificationRepository
                        .findTopByIncidentIdOrderByVerifiedAtDesc(id)
                        .map(IncidentVerificationResponse::from)
                        .orElse(null);

        IncidentCardResponse.CardCompleteness completeness =
                new IncidentCardResponse.CardCompleteness(
                        !timeline.isEmpty(),
                        !before.isEmpty(),
                        !after.isEmpty(),
                        verif != null
                );

        return new IncidentCardResponse(
                identity,
                timeline,
                before,
                after,
                verif,
                completeness
        );
    }

    @Transactional(readOnly = true)
    public List<TerminalEvidenceResponse> listEvidence(Long id) {

        require(id);

        return evidenceRepository.findByIncidentIdOrderByCapturedAtAsc(id)
                .stream()
                .map(TerminalEvidenceResponse::from)
                .toList();
    }

    /**
     * Enregistrement générique d'une preuve terminale.
     *
     * L'anti-doublon historique est conservé pour les preuves ordinaires.
     * Les observations Kubernetes VM_DOWN utilisent une méthode dédiée,
     * afin que deux commandes identiques restent deux relevés distincts.
     */
    @Transactional
    public TerminalEvidenceResponse recordEvidence(
            Long id,
            TerminalEvidenceRequest req) {

        require(id);

        if (req == null || req.phase() == null) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Preuve terminale invalide"
            );
        }

        if (req.command() != null
                && evidenceRepository.existsByIncidentIdAndPhaseAndCommand(
                id,
                req.phase(),
                req.command()
        )) {

            log.debug(
                    "Preuve terminale déjà enregistrée (incident {}, phase {}) — doublon ignoré",
                    id,
                    req.phase()
            );

            return evidenceRepository
                    .findByIncidentIdAndPhaseOrderByCapturedAtAsc(
                            id,
                            req.phase()
                    )
                    .stream()
                    .filter(e -> req.command().equals(e.getCommand()))
                    .findFirst()
                    .map(TerminalEvidenceResponse::from)
                    .orElse(null);
        }

        IncidentTerminalEvidence e = new IncidentTerminalEvidence();

        e.setIncidentId(id);
        e.setVm(req.vm());
        e.setPhase(req.phase());
        e.setCapturedAt(req.capturedAt());
        e.setCommand(req.command());
        e.setExitCode(req.exitCode());
        e.setStdout(req.stdout());
        e.setStderr(req.stderr());
        e.setMeasuresJson(req.measuresJson());
        e.setAvailable(Boolean.TRUE.equals(req.available()));
        e.setSource(req.source());

        return TerminalEvidenceResponse.from(
                evidenceRepository.save(e)
        );
    }

    /**
     * Enregistre une observation réelle de l'état Ready d'un nœud.
     *
     * Chaque observation est associée à un workflow et à un numéro de séquence.
     * saveAndFlush force l'envoi de la preuve à la base avant de poursuivre.
     */
    @Transactional
    public TerminalEvidenceResponse recordNodeReadinessEvidence(
            Long incidentId,
            Long workflowId,
            String nodeName,
            int sampleNumber,
            LocalDateTime capturedAt,
            NodeReadinessObservation observation) {

        Incident incident = require(incidentId);

        validateNodeRecoveryTarget(incident, nodeName);

        if (workflowId == null
                || workflowId <= 0
                || sampleNumber < 1
                || capturedAt == null
                || observation == null) {

            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Observation Kubernetes invalide"
            );
        }

        boolean ready =
                observation.apiAvailable()
                        && observation.ready()
                        && "True".equalsIgnoreCase(
                        observation.conditionStatus()
                );

        String source = NODE_READY_SOURCE_PREFIX + workflowId;

        String command =
                "GET /api/v1/nodes/" + nodeName
                        + " [sample=" + sampleNumber + "]";

        String measures =
                "{\"workflowId\":" + workflowId
                        + ",\"sample\":" + sampleNumber
                        + ",\"apiAvailable\":" + observation.apiAvailable()
                        + ",\"ready\":" + ready
                        + ",\"conditionStatus\":\""
                        + escapeJson(observation.conditionStatus())
                        + "\"}";

        IncidentTerminalEvidence evidence =
                new IncidentTerminalEvidence();

        evidence.setIncidentId(incidentId);
        evidence.setVm(nodeName);
        evidence.setPhase(TerminalEvidencePhase.AFTER);
        evidence.setCapturedAt(capturedAt);
        evidence.setCommand(command);

        // 200 représente une réponse Kubernetes obtenue.
        // Une erreur API ne doit jamais être enregistrée comme un succès.
        evidence.setExitCode(
                observation.apiAvailable() ? 200 : null
        );

        evidence.setStdout(observation.conditionStatus());

        evidence.setStderr(
                observation.apiAvailable()
                        ? null
                        : observation.message()
        );

        evidence.setMeasuresJson(measures);
        evidence.setAvailable(observation.apiAvailable());
        evidence.setSource(source);

        return TerminalEvidenceResponse.from(
                evidenceRepository.saveAndFlush(evidence)
        );
    }

    /**
     * Confirme que deux observations Ready consécutives ont été réellement
     * persistées pour le même incident, le même workflow et le même nœud.
     */
    @Transactional(readOnly = true)
    public boolean hasTwoConsecutiveNodeReadyEvidence(
            Long incidentId,
            Long workflowId,
            String nodeName) {

        return findTwoConsecutiveNodeReadyEvidence(
                incidentId,
                workflowId,
                nodeName
        ).size() == 2;
    }

    /**
     * Enregistre le verdict PASS VM_DOWN uniquement après vérification
     * des deux preuves persistées.
     *
     * Cette méthode est le seul chemin autorisé pour produire un PASS
     * de récupération de nœud.
     */
    @Transactional
    public IncidentVerificationResponse recordNodeRecoveryVerification(
            Long incidentId,
            Long workflowId,
            String nodeName,
            String message) {

        List<IncidentTerminalEvidence> pair =
                findTwoConsecutiveNodeReadyEvidence(
                        incidentId,
                        workflowId,
                        nodeName
                );

        if (pair.size() != 2) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "PASS VM_DOWN refusé : deux preuves Ready consécutives "
                            + "persistées et espacées d'au moins 15 secondes sont requises"
            );
        }

        IncidentTerminalEvidence first = pair.get(0);
        IncidentTerminalEvidence second = pair.get(1);

        int firstSample = extractSampleNumber(first);
        int secondSample = extractSampleNumber(second);

        String measures =
                "{\"workflowId\":" + workflowId
                        + ",\"node\":\"" + escapeJson(nodeName) + "\""
                        + ",\"observations\":["
                        + "{\"evidenceId\":" + first.getId()
                        + ",\"sample\":" + firstSample
                        + ",\"capturedAt\":\"" + first.getCapturedAt() + "\"},"
                        + "{\"evidenceId\":" + second.getId()
                        + ",\"sample\":" + secondSample
                        + ",\"capturedAt\":\"" + second.getCapturedAt() + "\"}"
                        + "]}";

        IncidentVerification verification = new IncidentVerification();

        verification.setIncidentId(incidentId);
        verification.setCriteria(
                "Node " + nodeName
                        + " Ready confirmed by two consecutive Kubernetes observations"
                        + " separated by at least "
                        + NODE_READY_MIN_INTERVAL_SECONDS
                        + " seconds"
        );
        verification.setResult("PASS");
        verification.setMeasuresJson(measures);
        verification.setReason(
                message == null || message.isBlank()
                        ? "Two persisted Kubernetes Ready observations confirmed"
                        : message
        );
        verification.setVerifiedAt(LocalDateTime.now());
        verification.setAvailable(true);

        return IncidentVerificationResponse.from(
                verificationRepository.saveAndFlush(verification)
        );
    }

    /**
     * Recherche les deux preuves correspondantes.
     *
     * Les preuves d'un autre workflow ne sont jamais réutilisées.
     * Une observation indisponible ou non Ready rompt la séquence.
     */
    private List<IncidentTerminalEvidence> findTwoConsecutiveNodeReadyEvidence(
            Long incidentId,
            Long workflowId,
            String nodeName) {

        Incident incident = require(incidentId);

        validateNodeRecoveryTarget(incident, nodeName);

        if (workflowId == null || workflowId <= 0) {
            return List.of();
        }

        String expectedSource =
                NODE_READY_SOURCE_PREFIX + workflowId;

        List<IncidentTerminalEvidence> evidence =
                evidenceRepository
                        .findByIncidentIdAndPhaseOrderByCapturedAtAsc(
                                incidentId,
                                TerminalEvidencePhase.AFTER
                        );

        IncidentTerminalEvidence previous = null;
        int previousSample = -1;

        for (IncidentTerminalEvidence current : evidence) {

            // Ignorer les preuves d'autres workflows.
            if (!expectedSource.equals(current.getSource())) {
                continue;
            }

            // Une autre cible ne peut pas constituer une preuve pour ce nœud.
            if (!nodeName.equals(current.getVm())) {
                previous = null;
                previousSample = -1;
                continue;
            }

            int sampleNumber = extractSampleNumber(current);

            boolean validReady =
                    current.getPhase() == TerminalEvidencePhase.AFTER
                            && current.isAvailable()
                            && Integer.valueOf(200).equals(
                            current.getExitCode()
                    )
                            && "True".equalsIgnoreCase(
                            current.getStdout()
                    )
                            && current.getCapturedAt() != null
                            && sampleNumber > 0
                            && current.getMeasuresJson() != null
                            && current.getMeasuresJson().contains(
                            "\"apiAvailable\":true"
                    )
                            && current.getMeasuresJson().contains(
                            "\"ready\":true"
                    )
                            && current.getCommand() != null
                            && current.getCommand().equals(
                            "GET /api/v1/nodes/" + nodeName
                                    + " [sample=" + sampleNumber + "]"
                    );

            if (!validReady) {
                // Une observation NotReady ou indisponible casse
                // la consécutivité.
                previous = null;
                previousSample = -1;
                continue;
            }

            if (previous != null
                    && sampleNumber == previousSample + 1
                    && previous.getCapturedAt() != null) {

                Duration interval = Duration.between(
                        previous.getCapturedAt(),
                        current.getCapturedAt()
                );

                if (!interval.isNegative()
                        && interval.compareTo(
                        Duration.ofSeconds(
                                NODE_READY_MIN_INTERVAL_SECONDS
                        )
                ) >= 0) {

                    return List.of(previous, current);
                }
            }

            previous = current;
            previousSample = sampleNumber;
        }

        return List.of();
    }

    /**
     * Extrait le numéro de séquence de la preuve persistée.
     */
    private int extractSampleNumber(IncidentTerminalEvidence evidence) {

        if (evidence == null || evidence.getMeasuresJson() == null) {
            return -1;
        }

        Matcher matcher =
                NODE_SAMPLE_PATTERN.matcher(evidence.getMeasuresJson());

        if (!matcher.find()) {
            return -1;
        }

        try {
            return Integer.parseInt(matcher.group(1));
        } catch (NumberFormatException ex) {
            return -1;
        }
    }

    /**
     * Vérifie que l'incident concerne bien un nœud Kubernetes
     * et que la cible correspond à l'application de l'incident.
     */
    private void validateNodeRecoveryTarget(
            Incident incident,
            String nodeName) {

        if (!"k8s-nodes".equalsIgnoreCase(incident.getNamespace())
                || nodeName == null
                || nodeName.isBlank()
                || !nodeName.equals(incident.getApp())
                || !isNodeRecoveryIncident(incident)) {

            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Incident ou nœud incompatible avec la vérification VM_DOWN"
            );
        }
    }

    private boolean isNodeRecoveryIncident(Incident incident) {

        if (incident == null || incident.getIncidentType() == null) {
            return false;
        }

        String type = incident.getIncidentType();

        return "NODE_NOT_READY".equalsIgnoreCase(type)
                || "VM_DOWN".equalsIgnoreCase(type)
                || "VM_POWERED_OFF".equalsIgnoreCase(type);
    }

    private static String escapeJson(String value) {

        if (value == null) {
            return "";
        }

        return value
                .replace("\\", "\\\\")
                .replace("\"", "\\\"");
    }

    // ── VMDOWN-POLICY-20261105 : preuves de RECUPERATION d'une VM (clé = incident, sans workflow) ──────────
    //
    // Règle R5 : aucun RESOLVED sans preuve réelle. Une VM est récupérée quand DEUX passes de vérification
    // consécutives, espacées d'au moins 15 s, ont toutes réussi et ont été PERSISTÉES. Un échec (passed=false)
    // casse la série. Le verdict PASS n'est produit que par recordVmRecoveryVerification, après relecture des deux
    // preuves en base. Valable pour les nœuds Kubernetes (Ready), .162 (SSH, HAProxy, DNS, etcd, quorum) et .160.

    public static final String VM_RECOVERY_SOURCE_PREFIX = "VM_RECOVERY:";

    @Transactional
    public TerminalEvidenceResponse recordVmRecoveryEvidence(
            Long incidentId,
            String vm,
            int sampleNumber,
            LocalDateTime capturedAt,
            boolean passed,
            String criteriaJson) {

        Incident incident = require(incidentId);
        validateVmRecoveryTarget(incident, vm);

        if (sampleNumber < 1 || capturedAt == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Observation de récupération VM invalide");
        }

        IncidentTerminalEvidence evidence = new IncidentTerminalEvidence();
        evidence.setIncidentId(incidentId);
        evidence.setVm(vm);
        evidence.setPhase(TerminalEvidencePhase.AFTER);
        evidence.setCapturedAt(capturedAt);
        evidence.setCommand("VM_RECOVERY " + vm + " [sample=" + sampleNumber + "]");
        evidence.setExitCode(passed ? 0 : 1);
        evidence.setStdout(passed ? "PASS" : "FAIL");
        evidence.setMeasuresJson(
                "{\"incident\":" + incidentId
                        + ",\"sample\":" + sampleNumber
                        + ",\"passed\":" + passed
                        + ",\"criteria\":" + (criteriaJson == null || criteriaJson.isBlank() ? "{}" : criteriaJson)
                        + "}");
        evidence.setAvailable(true);
        evidence.setSource(VM_RECOVERY_SOURCE_PREFIX + incidentId);

        return TerminalEvidenceResponse.from(evidenceRepository.saveAndFlush(evidence));
    }

    @Transactional(readOnly = true)
    public boolean hasTwoConsecutiveVmRecoveryEvidence(Long incidentId, String vm) {
        return findTwoConsecutiveVmRecoveryEvidence(incidentId, vm).size() == 2;
    }

    /** Seul chemin autorisé pour produire un PASS de récupération de VM. Refus (409) sans deux preuves persistées. */
    @Transactional
    public IncidentVerificationResponse recordVmRecoveryVerification(Long incidentId, String vm, String message) {

        List<IncidentTerminalEvidence> pair = findTwoConsecutiveVmRecoveryEvidence(incidentId, vm);
        if (pair.size() != 2) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "PASS récupération VM refusé : deux preuves consécutives persistées et espacées d'au moins "
                            + NODE_READY_MIN_INTERVAL_SECONDS + " secondes sont requises");
        }
        IncidentTerminalEvidence first = pair.get(0);
        IncidentTerminalEvidence second = pair.get(1);

        IncidentVerification verification = new IncidentVerification();
        verification.setIncidentId(incidentId);
        verification.setCriteria("VM " + vm + " recovered: two consecutive verification passes separated by at least "
                + NODE_READY_MIN_INTERVAL_SECONDS + " seconds");
        verification.setResult("PASS");
        verification.setMeasuresJson(
                "{\"vm\":\"" + escapeJson(vm) + "\",\"observations\":["
                        + "{\"evidenceId\":" + first.getId() + ",\"sample\":" + extractSampleNumber(first)
                        + ",\"capturedAt\":\"" + first.getCapturedAt() + "\"},"
                        + "{\"evidenceId\":" + second.getId() + ",\"sample\":" + extractSampleNumber(second)
                        + ",\"capturedAt\":\"" + second.getCapturedAt() + "\"}]}");
        verification.setReason(message == null || message.isBlank()
                ? "Two persisted consecutive recovery passes confirmed" : message);
        verification.setVerifiedAt(LocalDateTime.now());
        verification.setAvailable(true);

        return IncidentVerificationResponse.from(verificationRepository.saveAndFlush(verification));
    }

    /** True si un verdict PASS de récupération VM est déjà persisté pour l'incident. */
    @Transactional(readOnly = true)
    public boolean hasPassVerification(Long incidentId) {
        return verificationRepository.findTopByIncidentIdOrderByVerifiedAtDesc(incidentId)
                .map(v -> "PASS".equalsIgnoreCase(v.getResult()) && v.isAvailable())
                .orElse(false);
    }

    private List<IncidentTerminalEvidence> findTwoConsecutiveVmRecoveryEvidence(Long incidentId, String vm) {

        Incident incident = require(incidentId);
        validateVmRecoveryTarget(incident, vm);

        String expectedSource = VM_RECOVERY_SOURCE_PREFIX + incidentId;
        IncidentTerminalEvidence previous = null;
        int previousSample = -1;

        for (IncidentTerminalEvidence current : evidenceRepository
                .findByIncidentIdAndPhaseOrderByCapturedAtAsc(incidentId, TerminalEvidencePhase.AFTER)) {

            if (!expectedSource.equals(current.getSource()) || !vm.equals(current.getVm())) {
                continue;
            }
            int sample = extractSampleNumber(current);
            boolean valid = current.isAvailable()
                    && Integer.valueOf(0).equals(current.getExitCode())
                    && "PASS".equals(current.getStdout())
                    && current.getCapturedAt() != null
                    && sample > 0
                    && current.getMeasuresJson() != null
                    && current.getMeasuresJson().contains("\"passed\":true")
                    && ("VM_RECOVERY " + vm + " [sample=" + sample + "]").equals(current.getCommand());
            if (!valid) {
                previous = null;   // une passe échouée ou illisible casse la série
                previousSample = -1;
                continue;
            }
            if (previous != null && sample == previousSample + 1) {
                Duration gap = Duration.between(previous.getCapturedAt(), current.getCapturedAt());
                if (!gap.isNegative() && gap.compareTo(Duration.ofSeconds(NODE_READY_MIN_INTERVAL_SECONDS)) >= 0) {
                    return List.of(previous, current);
                }
            }
            previous = current;
            previousSample = sample;
        }
        return List.of();
    }

    /** L'incident doit concerner la VM (app = vm) dans la famille VM_DOWN : nœud K8s ou VM d'infrastructure. */
    private void validateVmRecoveryTarget(Incident incident, String vm) {
        boolean sameVm = vm != null && !vm.isBlank() && vm.equals(incident.getApp());
        String ns = incident.getNamespace();
        String type = incident.getIncidentType();
        boolean k8sNode = "k8s-nodes".equals(ns) && isNodeRecoveryIncident(incident);
        boolean infra = "infra".equals(ns) && ("VM_DOWN".equalsIgnoreCase(type) || "VM_UNREACHABLE".equalsIgnoreCase(type));
        if (!sameVm || !(k8sNode || infra)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "Incident ou VM incompatible avec la vérification de récupération VM");
        }
    }

    @Transactional(readOnly = true)
    public IncidentVerificationResponse getVerification(Long id) {

        require(id);

        return verificationRepository
                .findTopByIncidentIdOrderByVerifiedAtDesc(id)
                .map(IncidentVerificationResponse::from)
                .orElse(null);
    }

    /**
     * Enregistrement générique d'une vérification.
     *
     * Un PASS générique est interdit pour les incidents de récupération
     * de nœud. Ils doivent passer par recordNodeRecoveryVerification().
     */
    @Transactional
    public IncidentVerificationResponse recordVerification(
            Long id,
            IncidentVerificationRequest req) {

        Incident incident = require(id);

        if (req == null) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Demande de vérification invalide"
            );
        }

        boolean pass = "PASS".equalsIgnoreCase(req.result());

        if (pass && isNodeRecoveryIncident(incident)) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "PASS VM_DOWN interdit sans validation dédiée "
                            + "des deux preuves Kubernetes"
            );
        }

        boolean hasObs =
                req.measuresJson() != null
                        && !req.measuresJson().isBlank();

        boolean hasCriteria =
                req.criteria() != null
                        && !req.criteria().isBlank();

        boolean available = Boolean.TRUE.equals(req.available());

        if (pass && !(hasObs && hasCriteria && available)) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Verdict PASS refusé : critères, mesures et disponibilité requis"
            );
        }

        IncidentVerification verification = new IncidentVerification();

        verification.setIncidentId(id);
        verification.setCriteria(req.criteria());
        verification.setResult(req.result());
        verification.setMeasuresJson(req.measuresJson());
        verification.setReason(req.reason());
        verification.setVerifiedAt(req.verifiedAt());
        verification.setAvailable(available);

        return IncidentVerificationResponse.from(
                verificationRepository.save(verification)
        );
    }

    /**
     * Enregistrement générique de plusieurs observations.
     *
     * Les PASS sont interdits pour les incidents de récupération de nœud.
     */
    @Transactional
    public List<IncidentVerificationResponse> recordVerifications(
            Long id,
            List<IncidentVerificationRequest> requests) {

        Incident incident = require(id);

        if (requests == null || requests.isEmpty()) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Au moins une observation est requise"
            );
        }

        if (isNodeRecoveryIncident(incident)
                && requests.stream().anyMatch(
                req -> req != null
                        && "PASS".equalsIgnoreCase(req.result())
        )) {

            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "PASS VM_DOWN interdit via enregistrement générique"
            );
        }

        List<IncidentVerification> verifications =
                requests.stream()
                        .map(req -> {

                            if (req == null) {
                                throw new ResponseStatusException(
                                        HttpStatus.BAD_REQUEST,
                                        "Observation de vérification invalide"
                                );
                            }

                            boolean pass =
                                    "PASS".equalsIgnoreCase(req.result());

                            boolean hasObs =
                                    req.measuresJson() != null
                                            && !req.measuresJson().isBlank();

                            boolean hasCriteria =
                                    req.criteria() != null
                                            && !req.criteria().isBlank();

                            boolean available =
                                    Boolean.TRUE.equals(req.available());

                            if (pass
                                    && !(hasObs && hasCriteria && available)) {

                                throw new ResponseStatusException(
                                        HttpStatus.BAD_REQUEST,
                                        "Verdict PASS refusé : critères, mesures et disponibilité requis"
                                );
                            }

                            IncidentVerification verification =
                                    new IncidentVerification();

                            verification.setIncidentId(id);
                            verification.setCriteria(req.criteria());
                            verification.setResult(req.result());
                            verification.setMeasuresJson(req.measuresJson());
                            verification.setReason(req.reason());
                            verification.setVerifiedAt(req.verifiedAt());
                            verification.setAvailable(available);

                            return verification;
                        })
                        .toList();

        return verificationRepository
                .saveAllAndFlush(verifications)
                .stream()
                .map(IncidentVerificationResponse::from)
                .toList();
    }
}
