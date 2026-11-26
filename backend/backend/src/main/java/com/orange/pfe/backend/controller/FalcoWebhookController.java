package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.IncidentRequest;
import com.orange.pfe.backend.dto.IncidentResponse;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.AIOpsOrchestrationService;
import com.orange.pfe.backend.service.IncidentService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;

/**
 * INGESTION FALCO -> INCIDENT DE SÉCURITÉ -> IA -> CAMUNDA.
 *
 * <p>Falco (sonde eBPF sur chaque nœud) détecte des comportements de sécurité au niveau syscall
 * (shell interactif dans un conteneur, lecture de fichier sensible, écriture sous un répertoire de
 * binaires, mining, connexion sortante suspecte…). Falcosidekick POST chaque alerte ici. On la
 * transforme en INCIDENT HELIOS réel (catégorie Sécurité), on fixe l'action de remédiation
 * exécutable, puis on déclenche la remédiation Camunda — exactement le même chemin
 * ({@link AIOpsOrchestrationService#executeRecommendation}) que les autres incidents.
 *
 * <p>Ainsi HELIOS DÉTECTE une catégorie entière (Sécurité) qu'aucun signal Prometheus/état-K8s ne
 * voyait, et Camunda EXÉCUTE la réponse (remplacement du pod compromis) DANS le cluster.
 */
@RestController
@RequestMapping("/api/webhooks/falco")
public class FalcoWebhookController {

    private static final Logger log = LoggerFactory.getLogger(FalcoWebhookController.class);

    private final IncidentService incidentService;
    private final AIOpsOrchestrationService orchestrationService;
    private final IncidentRepository incidentRepository;

    public FalcoWebhookController(IncidentService incidentService,
                                  AIOpsOrchestrationService orchestrationService,
                                  IncidentRepository incidentRepository) {
        this.incidentService = incidentService;
        this.orchestrationService = orchestrationService;
        this.incidentRepository = incidentRepository;
    }

    /**
     * Jeton partagé Falcosidekick -> backend (en-tête {@value #TOKEN_HEADER}). FERMÉ PAR DÉFAUT : aucun jeton par défaut ;
     * tant que {@code HELIOS_FALCO_WEBHOOK_TOKEN} n'est pas fourni par l'environnement, TOUTES les requêtes sont refusées.
     */
    @Value("${helios.falco.webhook-token:}")
    private String webhookToken = "";

    static final String TOKEN_HEADER = "X-Helios-Falco-Token";

    private boolean tokenAccepted(String supplied) {
        if (webhookToken == null || webhookToken.isBlank() || supplied == null) {
            return false;
        }
        // Comparaison en temps constant des condensats : pas de fuite sur la longueur ni sur le préfixe.
        try {
            MessageDigest sha = MessageDigest.getInstance("SHA-256");
            byte[] a = sha.digest(webhookToken.getBytes(StandardCharsets.UTF_8));
            byte[] b = sha.digest(supplied.getBytes(StandardCharsets.UTF_8));
            return MessageDigest.isEqual(a, b);
        } catch (java.security.NoSuchAlgorithmException e) {
            return false;
        }
    }

    @PostMapping
    public ResponseEntity<Map<String, Object>> ingest(@RequestBody Map<String, Object> event,
            @RequestHeader(value = TOKEN_HEADER, required = false) String token) {
        if (!tokenAccepted(token)) {
            // Jamais le jeton (ni reçu ni attendu) dans les journaux.
            log.warn("Webhook Falco refusé : authentification absente, invalide ou non configurée");
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(Map.of("status", "unauthorized"));
        }
        // Toujours répondre 200 : Falcosidekick ne doit pas re-tenter en boucle sur une erreur.
        try {
            String rule = str(event.get("rule"));
            String priority = str(event.get("priority"));
            Object ofRaw = event.get("output_fields");
            @SuppressWarnings("unchecked")
            Map<String, Object> of = (ofRaw instanceof Map) ? (Map<String, Object>) ofRaw : Map.of();

            String ns = firstNonBlank(str(of.get("k8s.ns.name")), str(of.get("k8s.namespace.name")));
            String podName = str(of.get("k8s.pod.name"));
            String containerId = str(of.get("container.id"));

            // On ne remédie QUE les événements rattachés à un pod applicatif du namespace surveillé.
            // Les événements « host » (container.id=host, systemd/kubelet du nœud) et les namespaces
            // système sont ignorés — ils sont bruités et non remédiables par remplacement de pod.
            if (rule == null || podName == null || podName.isBlank()
                    || "host".equalsIgnoreCase(containerId)
                    || ns == null || ns.isBlank()
                    || isSystemNamespace(ns)) {
                return ResponseEntity.ok(Map.of("status", "ignored"));
            }

            // GARDE FAUX-POSITIFS : on ne remédie QUE les règles à haute confiance (signatures d'attaque
            // franches, quasi jamais déclenchées par une app saine). Le reste = ignoré -> aucune app de
            // démo n'est redémarrée sur du bruit. Précision > rappel côté auto-remédiation.
            if (!isActionableRule(rule)) {
                return ResponseEntity.ok(Map.of("status", "ignored-low-confidence", "rule", rule));
            }

            String app = deploymentOf(podName);

            // AUTO-PROTECTION : ne JAMAIS auto-remédier les composants de HELIOS lui-même
            // (backend/frontend/DB) ni l'infra — sinon Falco pourrait redémarrer le backend qui
            // remédie (boucle d'auto-sabotage). Ces apps ont un accès K8s/DB légitime.
            if (isProtectedApp(app)) {
                return ResponseEntity.ok(Map.of("status", "ignored-protected-app", "app", app, "rule", rule));
            }

            String type = mapType(rule, event.get("tags"));
            String action = actionFor(type);
            IncidentSeverity severity = severityOf(priority);
            String falcoOutput = firstNonBlank(str(event.get("output")), rule);

            // Description avec les MARQUEURS lus par executeRecommendation : « | action=X | ... ».
            String description = "SÉCURITÉ (Falco/eBPF) — " + trunc(falcoOutput, 900)
                    + " | Type: " + type
                    + " | action=" + action
                    + " | pod=" + podName
                    + " | Résolution (catalogue HELIOS): " + catalogResolution(type);

            IncidentRequest req = new IncidentRequest();
            req.setTitle(type + " : " + app + " (Falco)");
            req.setDescription(trunc(description, 1990));
            req.setSeverity(severity);
            req.setSource(IncidentSource.OTHER);   // OTHER = capteur externe (Falco)
            req.setNamespace(ns);
            req.setApp(app);

            IncidentResponse created = incidentService.createIncident(req);
            Long id = created.getId();

            // Fixer le TYPE (le DTO de création ne le porte pas) pour l'affichage + l'explication.
            Optional<Incident> ent = incidentRepository.findById(id);
            if (ent.isPresent()) {
                Incident inc = ent.get();
                if (inc.getIncidentType() == null || inc.getIncidentType().isBlank()) {
                    inc.setIncidentType(type);
                    inc.setExplanation("Détection Falco (eBPF, MITRE) — règle « " + rule
                            + " » sur le pod " + podName + ". Action recommandée : " + action
                            + ". Résolution : " + catalogResolution(type));
                    incidentRepository.save(inc);
                }
            }

            log.warn("FALCO -> incident #{} type={} ns={} app={} action={} rule='{}'",
                    id, type, ns, app, action, rule);

            // Remédiation Camunda (remplacement du pod compromis) — même chemin que tout incident.
            orchestrationService.executeRecommendation(id);

            return ResponseEntity.ok(Map.of("status", "accepted", "incidentId", id, "type", type, "action", action));
        } catch (Exception e) {
            log.error("FALCO webhook: traitement échoué: {}", e.getMessage());
            return ResponseEntity.ok(Map.of("status", "error", "message", String.valueOf(e.getMessage())));
        }
    }

    /**
     * Règles Falco « haute confiance » : des actions qu'une application saine ne fait JAMAIS
     * (spawn d'un shell, gestion de paquets, écriture d'un binaire, lecture de /etc/shadow, mining,
     * appel direct à l'API K8s, exécution d'un binaire fraîchement déposé). On matche sur des
     * fragments du nom de règle pour rester robuste aux variantes du ruleset Falco.
     */
    private boolean isActionableRule(String rule) {
        String r = rule.toLowerCase(Locale.ROOT);
        return r.contains("terminal shell in container")
                || r.contains("shell in a container")
                || r.contains("run shell untrusted")
                || r.contains("package management process in container")
                || r.contains("write below binary dir")
                || r.contains("write below etc")
                || r.contains("write below root")
                || r.contains("read sensitive file untrusted")
                || r.contains("launch privileged container")
                || r.contains("privilege escalation")
                // NB: « Contact K8S API Server From Container » VOLONTAIREMENT exclu : c'est un
                // comportement LÉGITIME du backend HELIOS (remédiation) et des opérateurs -> bruité.
                || r.contains("drop and execute new binary in container")
                || r.contains("crypto") || r.contains("miner") || r.contains("stratum")
                || r.contains("reverse shell") || r.contains("outbound connection to c2");
    }

    /** Falco rule/tags -> type d'incident sécurité HELIOS (catalogue 120). */
    private String mapType(String rule, Object tagsObj) {
        String r = rule.toLowerCase(Locale.ROOT);
        String tags = String.valueOf(tagsObj).toLowerCase(Locale.ROOT);
        if (r.contains("mine") || r.contains("crypto") || r.contains("stratum") || tags.contains("cryptomining")) {
            return "CRYPTO_MINING_DETECTED";
        }
        if (r.contains("privileg") || r.contains("escalat") || r.contains("setuid") || tags.contains("privilege_escalation")) {
            return "PRIVILEGED_CONTAINER";
        }
        if (r.contains("outbound") || r.contains("connect") || r.contains("exfil") || tags.contains("network")) {
            return "SUSPICIOUS_NETWORK_ACTIVITY";
        }
        // shell interactif, lecture de fichier sensible, écriture sous /bin, package-mgmt… = pod compromis
        return "MALICIOUS_POD";
    }

    /** Action EXÉCUTABLE par Camunda pour chaque type de sécurité (jamais « investigate »). */
    private String actionFor(String type) {
        // Remplacer le pod compromis (delete->recreate par le ReplicaSet) = réponse sûre et exécutable.
        // On ne cordonne PAS le nœud (les 3 nœuds sont control-plane) : on neutralise au niveau pod.
        return "DELETE_CRASHLOOPING_PODS";
    }

    private String catalogResolution(String type) {
        return switch (type) {
            case "CRYPTO_MINING_DETECTED"   -> "Neutraliser le pod de minage puis investiguer l'image/source.";
            case "PRIVILEGED_CONTAINER"     -> "Retirer les privilèges du conteneur et remplacer le pod.";
            case "SUSPICIOUS_NETWORK_ACTIVITY" -> "Isoler le pod (remplacement) et analyser le trafic.";
            default                          -> "Supprimer/remplacer le pod malveillant.";
        };
    }

    private IncidentSeverity severityOf(String priority) {
        if (priority == null) return IncidentSeverity.HIGH;
        return switch (priority.toLowerCase(Locale.ROOT)) {
            case "emergency", "alert", "critical" -> IncidentSeverity.CRITICAL;
            case "error", "warning"               -> IncidentSeverity.HIGH;
            case "notice", "informational", "info", "debug" -> IncidentSeverity.MEDIUM;
            default -> IncidentSeverity.HIGH;
        };
    }

    /** pod « podinfo-7f5c6d4f5c-47g42 » -> deployment « podinfo » (retire replicaset+pod hash). */
    private String deploymentOf(String podName) {
        String[] parts = podName.split("-");
        if (parts.length >= 3) {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < parts.length - 2; i++) {
                if (i > 0) sb.append('-');
                sb.append(parts[i]);
            }
            return sb.toString();
        }
        if (parts.length == 2) return parts[0];
        return podName;
    }

    /** Composants HELIOS/infra à ne JAMAIS auto-remédier (accès K8s/DB légitime, auto-sabotage sinon). */
    private boolean isProtectedApp(String app) {
        if (app == null) return true;
        String a = app.toLowerCase(Locale.ROOT);
        return a.equals("backend") || a.equals("frontend")
                || a.startsWith("postgres") || a.startsWith("cnpg")
                || a.startsWith("camunda") || a.startsWith("zeebe");
    }

    private boolean isSystemNamespace(String ns) {
        return ns.equals("kube-system") || ns.equals("falco") || ns.equals("cnpg-system")
                || ns.equals("local-path-storage") || ns.startsWith("kube-");
    }

    private static String str(Object o) { return o == null ? null : String.valueOf(o); }

    private static String firstNonBlank(String a, String b) {
        return (a != null && !a.isBlank() && !"null".equals(a) && !"<NA>".equals(a)) ? a : b;
    }

    private static String trunc(String s, int max) {
        if (s == null) return "";
        return s.length() <= max ? s : s.substring(0, max);
    }
}
