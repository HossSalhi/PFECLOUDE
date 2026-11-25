package com.orange.pfe.backend.service.vmpower;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Collectors;

/**
 * VMDOWN-POLICY-20261105 — politique UNIQUE de mise sous tension des VM HELIOS (scénario VM_DOWN).
 *
 * <p>Table FERMÉE, identifiants EXACTS. Sa source de vérité est {@code infra/helios-vm-policy.yaml} ;
 * la parité YAML = Java (= watchdog hôte) est verrouillée par {@code VmPowerPolicyParityTest}.
 * <ul>
 *   <li>{@code k8s-worker2} (.155), {@code k8s-worker1} (.158), {@code k8s-lb2} (.162) et — depuis la consigne de
 *       l'utilisateur du 2026-11-17, qui INVERSE « .153 jamais cible » — {@code k8s-master} (.153, plan de contrôle) :
 *       {@link Mode#AUTO}, route {@link Route#CAMUNDA} (chaîne Camunda existante) ;</li>
 *   <li>{@code k8s-monitoring} (.160) : {@link Mode#DETECT_ONLY} (consigne finale de l'après-midi du 2026-11-06 :
 *       c'est la VM de supervision, donc PAS d'auto-rallumage), route {@link Route#NONE}, notifications
 *       {@link Notifications#UI_ONLY} (interface seulement : aucun e-mail, aucune escalade Camunda). La route
 *       {@link Route#BACKEND_DIRECT} reste dans le langage de la politique (validée par un essai réel le 2026-11-06
 *       13:28) mais DORMANTE : aucune ligne de la table ne l'utilise ;</li>
 *   <li>{@link Mode#DENY_RESTART} : mode CONSERVÉ et honoré (refus {@link Refusal#NOT_AUTHORIZED}) mais plus utilisé par
 *       aucune ligne de la table depuis le 2026-11-17 (c'était celui de {@code k8s-master}) ;</li>
 *   <li>toute autre cible (alias {@code lb2}/{@code monitoring}, adresse IP, {@code k8s-aiCamunda}, casse ou
 *       espaces différents, valeur nulle ou injectée) : {@link Mode#DENY}.</li>
 * </ul>
 *
 * <p>Cette classe est PURE (aucun accès réseau, base ou fichier) : elle ne démarre rien. Elle dit seulement
 * si un démarrage serait permis, à partir de FAITS établis par l'appelant. Elle est FAIL-CLOSED : un fait non
 * établi ({@code null}) refuse ({@link Refusal#FACTS_INCOMPLETE}).
 */
public final class VmPowerPolicy {

    private VmPowerPolicy() {
    }

    /**
     * Révision de la table : MARQUEUR de version lu dans le bytecode de l'image DÉPLOYÉE par
     * {@code infra/helios-sot/tools/policy_reconcile.py} (pool de constantes). 2026-11-17 : {@code k8s-master} (.153) passe
     * de DENY_RESTART à AUTO (route CAMUNDA). 2026-11-18 : cooldown de redémarrage 30 → 5 min ({@link #RESTART_COOLDOWN}).
     * Toute révision de la table OU des seuils doit changer cette valeur ET le marqueur de l'outil.
     */
    public static final String POLICY_REVISION = "2026-11-18-cooldown-5min";

    // ── Seuils (miroir de infra/helios-vm-policy.yaml, section "thresholds") ─────────────────────

    /** Une preuve d'état (vmrun list) plus vieille que cette durée est refusée. */
    public static final long EVIDENCE_MAX_AGE_MS = 15_000L;
    /** Nombre minimal d'observations POWERED_OFF consécutives. */
    public static final int CONFIRMATION_MIN_OBSERVATIONS = 2;
    /** Écart minimal entre la première et la dernière observation POWERED_OFF. */
    public static final long CONFIRMATION_MIN_SPAN_MS = 30_000L;
    /**
     * Une VM n'est pas redémarrée deux fois dans cette fenêtre, même après un échec. 5 min depuis le 2026-11-18 (était 30 min :
     * trois arrêts consécutifs de .153 étaient différés jusqu'à 23 min). DOIT rester ≥ au cooldown du watchdog
     * ({@code HELIOS_COOLDOWN_S}, 240 s) : sinon le backend demanderait un démarrage que le watchdog refuserait
     * ({@code COOLDOWN_ACTIVE}) — verrouillé par {@code scripts/helios-ops/test_cooldown_coherence.py} et {@code VmPowerPolicyParityTest}.
     */
    public static final Duration RESTART_COOLDOWN = Duration.ofMinutes(5);
    /** Valeur de {@link Request#msSinceLastRestartAction()} quand aucune action RESTART_VM n'est connue. */
    public static final long NEVER = Long.MAX_VALUE;

    // ── Types ────────────────────────────────────────────────────────────────────────────────────

    public enum Mode {
        /** Détection + validation + redémarrage automatique. */
        AUTO,
        /** Détection et journalisation uniquement (aucune action, aucun e-mail, aucune escalade Camunda). */
        DETECT_ONLY,
        /** Jamais redémarrée automatiquement ; la détection existante est conservée. */
        DENY_RESTART,
        /** Mode implicite de toute cible absente de la table. */
        DENY
    }

    public enum VmClass { K8S_WORKER, K8S_CONTROL_PLANE, INFRA_LB, INFRA_MONITORING }

    /** Qui exécute le redémarrage d'une cible {@link Mode#AUTO} (une seule route par VM : jamais deux acteurs). */
    public enum Route {
        /** Chaîne Camunda existante : incident-remediation → execute-remediation → watchdog hôte. */
        CAMUNDA,
        /** Le backend décide ET exécute (VmPowerWatch → watchdog hôte), SANS workflow Camunda ni escalade Camunda. */
        BACKEND_DIRECT,
        /** Aucune route de redémarrage (DENY_RESTART / DENY). */
        NONE
    }

    /** Canaux de notification autorisés pour les incidents de la VM. */
    public enum Notifications {
        /** Notifications habituelles (interface + e-mail selon la sévérité). */
        EMAIL_AND_UI,
        /** Interface (SSE) UNIQUEMENT : aucun e-mail, aucun webhook. */
        UI_ONLY
    }

    public enum PowerState {
        RUNNING, POWERED_OFF, UNKNOWN;

        /** Lecture du fil (watchdog) : toute valeur inconnue, vide ou nulle devient UNKNOWN (jamais un démarrage). */
        public static PowerState fromWire(String raw) {
            if (raw == null) {
                return UNKNOWN;
            }
            return switch (raw.trim().toUpperCase(Locale.ROOT)) {
                case "RUNNING" -> RUNNING;
                case "POWERED_OFF" -> POWERED_OFF;
                default -> UNKNOWN;
            };
        }
    }

    /** Motifs de refus. L'ordre de déclaration est l'ordre d'évaluation et d'affichage. */
    public enum Refusal {
        /** Cible absente de la table (nom inexact, alias, IP, null...). */
        UNKNOWN_VM,
        /** Cible en détection seule (.160). */
        DETECTION_ONLY,
        /** Cible sans autorisation de redémarrage (.153). */
        NOT_AUTHORIZED,
        /** Le triplet (namespace, application, type) de l'incident n'est pas celui de la table. */
        SCOPE_MISMATCH,
        /** La VM est allumée : jamais de démarrage d'une VM déjà allumée. */
        STATE_RUNNING,
        /** État d'alimentation inconnu : jamais de démarrage sur UNKNOWN. */
        STATE_UNKNOWN,
        /** Preuve d'état trop vieille. */
        EVIDENCE_STALE,
        /** Arrêt non confirmé par assez d'observations espacées. */
        NOT_CONFIRMED,
        /** Les sondes réseau indépendantes ne sont pas toutes en échec. */
        PROBES_NOT_FAILING,
        /** Drapeau de maintenance posé sur l'hôte. */
        MAINTENANCE,
        /** Un incident précédent du même épisode (RESOLUTION_FAILED) n'est pas clos. */
        EPISODE_OPEN,
        /** Dernière action RESTART_VM de cette VM trop récente. */
        COOLDOWN_ACTIVE,
        /** Une action RESTART_VM existe déjà pour cet incident. */
        ACTION_ALREADY_EXISTS,
        /** Un fait nécessaire à la décision n'est pas établi (toujours en dernier : méta-motif fail-closed). */
        FACTS_INCOMPLETE
    }

    /** Une ligne de la table fermée. */
    public record Entry(
            String name,
            String ip,
            Mode mode,
            VmClass vmClass,
            String incidentNamespace,
            String incidentApp,
            String incidentType,
            Route route,
            Notifications notifications) {

        public Entry {
            // Invariant de la table : une cible AUTO a exactement une route ; toute autre n'en a aucune.
            if ((mode == Mode.AUTO) == (route == Route.NONE)) {
                throw new IllegalArgumentException("Table VM_DOWN incohérente pour " + name
                        + " : mode " + mode + " avec route " + route);
            }
        }
    }

    // ── Table fermée (ordre = celui du YAML) ─────────────────────────────────────────────────────

    /** La table de PRODUCTION : fermée et immuable. */
    private static final Map<String, Entry> PRODUCTION_TABLE;

    /**
     * Table consultée par tout le code. C'est {@link #PRODUCTION_TABLE} partout, sauf pendant un test qui a appelé la
     * couture {@link #overrideEntryForTest(Entry)} (package-private : le code de production ne peut pas l'atteindre).
     */
    private static volatile Map<String, Entry> table;

    static {
        Map<String, Entry> t = new LinkedHashMap<>();
        put(t, new Entry("k8s-worker2", "192.168.148.155", Mode.AUTO, VmClass.K8S_WORKER,
                "k8s-nodes", "k8s-worker2", "NODE_NOT_READY", Route.CAMUNDA, Notifications.EMAIL_AND_UI));
        put(t, new Entry("k8s-worker1", "192.168.148.158", Mode.AUTO, VmClass.K8S_WORKER,
                "k8s-nodes", "k8s-worker1", "NODE_NOT_READY", Route.CAMUNDA, Notifications.EMAIL_AND_UI));
        put(t, new Entry("k8s-lb2", "192.168.148.162", Mode.AUTO, VmClass.INFRA_LB,
                "infra", "k8s-lb2", "VM_DOWN", Route.CAMUNDA, Notifications.EMAIL_AND_UI));
        // .160 : DÉTECTION SEULE (consigne finale de l'après-midi du 2026-11-06) — VM de supervision : aucun rallumage
        // automatique, aucun e-mail, aucune escalade Camunda. Rallumage par l'administrateur.
        put(t, new Entry("k8s-monitoring", "192.168.148.160", Mode.DETECT_ONLY, VmClass.INFRA_MONITORING,
                "infra", "k8s-monitoring", "VM_DOWN", Route.NONE, Notifications.UI_ONLY));
        // .153 : AUTO depuis la consigne du 2026-11-17 (était DENY_RESTART / route NONE). Mêmes garde-fous que les workers
        // (VM prouvée POWERED_OFF, sondes en échec, hors maintenance/cooldown/épisode) ; critères de reprise du plan de
        // contrôle dans VmRecoveryVerifier.verifyWorker (apiserver 6443 en plus).
        put(t, new Entry("k8s-master", "192.168.148.153", Mode.AUTO, VmClass.K8S_CONTROL_PLANE,
                "k8s-nodes", "k8s-master", "NODE_NOT_READY", Route.CAMUNDA, Notifications.EMAIL_AND_UI));
        PRODUCTION_TABLE = Collections.unmodifiableMap(t);
        table = PRODUCTION_TABLE;
    }

    private static void put(Map<String, Entry> target, Entry entry) {
        target.put(entry.name(), entry);
    }

    /**
     * COUTURE DE TEST (package-private, jamais appelée par le code de production) : remplace la ligne de la VM de même nom
     * dans la table consultée, jusqu'à {@link #resetForTest()}. Sert à exercer la route {@link Route#BACKEND_DIRECT}
     * (dormante) sans qu'aucune VM de production ne l'utilise. La table de production n'est jamais modifiée.
     */
    static void overrideEntryForTest(Entry replacement) {
        Objects.requireNonNull(replacement, "replacement");
        if (!PRODUCTION_TABLE.containsKey(replacement.name())) {
            throw new IllegalArgumentException("couture de test : « " + replacement.name() + " » n'est pas dans la table");
        }
        Map<String, Entry> copy = new LinkedHashMap<>(PRODUCTION_TABLE);
        copy.put(replacement.name(), replacement);
        table = Collections.unmodifiableMap(copy);
    }

    /** Annule {@link #overrideEntryForTest(Entry)} : la table de production redevient la table consultée. */
    static void resetForTest() {
        table = PRODUCTION_TABLE;
    }

    // ── Consultation de la table ─────────────────────────────────────────────────────────────────

    /** Lignes de la table, dans l'ordre du YAML. */
    public static List<Entry> entries() {
        return List.copyOf(table.values());
    }

    /** Recherche par nom EXACT (sensible à la casse, sans trim, sans alias, sans IP). */
    public static Optional<Entry> find(String vm) {
        if (vm == null) {
            return Optional.empty();
        }
        return Optional.ofNullable(table.get(vm));
    }

    /** Mode de la cible ; {@link Mode#DENY} si elle est inconnue. */
    public static Mode modeOf(String vm) {
        return find(vm).map(Entry::mode).orElse(Mode.DENY);
    }

    /** Vrai uniquement pour une cible {@link Mode#AUTO} (nom exact). */
    public static boolean isAutoRestartable(String vm) {
        return modeOf(vm) == Mode.AUTO;
    }

    /** Vrai uniquement pour une cible {@link Mode#DETECT_ONLY} (nom exact) : aujourd'hui {@code k8s-monitoring} (.160). */
    public static boolean isDetectionOnly(String vm) {
        return modeOf(vm) == Mode.DETECT_ONLY;
    }

    /** Route de redémarrage de la cible ; {@link Route#NONE} si elle est inconnue ou non AUTO. */
    public static Route routeOf(String vm) {
        return find(vm).map(Entry::route).orElse(Route.NONE);
    }

    /** Vrai uniquement pour une cible AUTO relevée par la chaîne Camunda (workers, .162). */
    public static boolean isCamundaRestartable(String vm) {
        return isAutoRestartable(vm) && routeOf(vm) == Route.CAMUNDA;
    }

    /** Vrai uniquement pour une cible AUTO relevée par le chemin direct du backend, sans Camunda (route DORMANTE : aucune VM aujourd'hui). */
    public static boolean isDirectRestartable(String vm) {
        return isAutoRestartable(vm) && routeOf(vm) == Route.BACKEND_DIRECT;
    }

    /** Vrai si les incidents de la cible ne doivent jamais produire d'e-mail ni de webhook (.160). */
    public static boolean isUiOnly(String vm) {
        return find(vm).map(e -> e.notifications() == Notifications.UI_ONLY).orElse(false);
    }

    /** Noms des cibles {@link Mode#AUTO}, triés. */
    public static Set<String> autoTargets() {
        return table.values().stream()
                .filter(e -> e.mode() == Mode.AUTO)
                .map(Entry::name)
                .collect(Collectors.toCollection(TreeSet::new));
    }

    /**
     * Garde STATIQUE (sans fait d'exécution) : motif de refus d'après la seule table, ou vide si la cible est
     * {@link Mode#AUTO}. Utilisable par les exécuteurs comme dernier garde avant l'hyperviseur.
     */
    public static Optional<Refusal> staticRefusal(String vm) {
        Optional<Entry> entry = find(vm);
        if (entry.isEmpty()) {
            return Optional.of(Refusal.UNKNOWN_VM);
        }
        return switch (entry.get().mode()) {
            case AUTO -> Optional.empty();
            case DETECT_ONLY -> Optional.of(Refusal.DETECTION_ONLY);
            case DENY_RESTART, DENY -> Optional.of(Refusal.NOT_AUTHORIZED);
        };
    }

    // ── Autorisation par composition ─────────────────────────────────────────────────────────────

    /**
     * Faits établis par l'appelant. {@code null} = fait NON établi (refus fail-closed).
     *
     * @param vm                         nom exact de la VM visée
     * @param incidentNamespace          namespace de l'incident qui justifie le démarrage
     * @param incidentApp                application de l'incident (doit être le nom de la VM)
     * @param incidentType               type de l'incident
     * @param powerState                 état d'alimentation relevé (null = UNKNOWN)
     * @param evidenceAgeMs              âge de la preuve d'état, en ms
     * @param poweredOffObservations     nombre d'observations POWERED_OFF consécutives
     * @param poweredOffSpanMs           écart entre la première et la dernière de ces observations, en ms
     * @param probesFailing              vrai si TOUTES les sondes réseau indépendantes sont en échec
     * @param maintenance                vrai si un drapeau de maintenance est posé
     * @param failedEpisodeOpen          vrai si un incident précédent RESOLUTION_FAILED du même épisode n'est pas clos
     * @param msSinceLastRestartAction   ms écoulées depuis la dernière action RESTART_VM de cette VM, quel que soit
     *                                   son statut ({@link #NEVER} si aucune)
     * @param restartActionsForIncident  nombre d'actions RESTART_VM déjà créées pour CET incident
     */
    public record Request(
            String vm,
            String incidentNamespace,
            String incidentApp,
            String incidentType,
            PowerState powerState,
            Long evidenceAgeMs,
            Integer poweredOffObservations,
            Long poweredOffSpanMs,
            Boolean probesFailing,
            Boolean maintenance,
            Boolean failedEpisodeOpen,
            Long msSinceLastRestartAction,
            Integer restartActionsForIncident) {

        public static Builder builder() {
            return new Builder();
        }

        /** Constructeur fluide : tout fait non renseigné reste {@code null} (donc refusé). */
        public static final class Builder {
            private String vm;
            private String incidentNamespace;
            private String incidentApp;
            private String incidentType;
            private PowerState powerState;
            private Long evidenceAgeMs;
            private Integer poweredOffObservations;
            private Long poweredOffSpanMs;
            private Boolean probesFailing;
            private Boolean maintenance;
            private Boolean failedEpisodeOpen;
            private Long msSinceLastRestartAction;
            private Integer restartActionsForIncident;

            public Builder vm(String v) { this.vm = v; return this; }
            public Builder incident(String namespace, String app, String type) {
                this.incidentNamespace = namespace;
                this.incidentApp = app;
                this.incidentType = type;
                return this;
            }
            public Builder powerState(PowerState v) { this.powerState = v; return this; }
            public Builder evidenceAgeMs(Long v) { this.evidenceAgeMs = v; return this; }
            public Builder poweredOff(int observations, long spanMs) {
                this.poweredOffObservations = observations;
                this.poweredOffSpanMs = spanMs;
                return this;
            }
            public Builder probesFailing(Boolean v) { this.probesFailing = v; return this; }
            public Builder maintenance(Boolean v) { this.maintenance = v; return this; }
            public Builder failedEpisodeOpen(Boolean v) { this.failedEpisodeOpen = v; return this; }
            public Builder msSinceLastRestartAction(Long v) { this.msSinceLastRestartAction = v; return this; }
            public Builder restartActionsForIncident(Integer v) { this.restartActionsForIncident = v; return this; }

            public Request build() {
                return new Request(vm, incidentNamespace, incidentApp, incidentType, powerState, evidenceAgeMs,
                        poweredOffObservations, poweredOffSpanMs, probesFailing, maintenance, failedEpisodeOpen,
                        msSinceLastRestartAction, restartActionsForIncident);
            }
        }
    }

    /** Résultat : {@code allowed} ssi {@code refusals} est vide. Ne démarre rien. */
    public record Decision(boolean allowed, Mode mode, Entry entry, List<Refusal> refusals) {

        public Decision {
            refusals = List.copyOf(refusals);
        }

        public List<String> refusalCodes() {
            return refusals.stream().map(Enum::name).toList();
        }

        public String summary() {
            String target = entry == null ? "<inconnue>" : entry.name();
            return allowed
                    ? "AUTORISE " + target
                    : "REFUSE " + target + " [" + String.join(",", refusalCodes()) + "]";
        }
    }

    /**
     * Décide si un démarrage serait permis. Composition (toutes les conditions doivent être vraies) :
     * cible {@link Mode#AUTO} ∧ incident du bon périmètre ∧ VM {@code POWERED_OFF} confirmée (preuve fraîche,
     * ≥ {@value #CONFIRMATION_MIN_OBSERVATIONS} observations espacées) ∧ sondes en échec ∧ pas de maintenance ∧
     * pas d'épisode RESOLUTION_FAILED ouvert ∧ hors cooldown ∧ aucune action RESTART_VM pour cet incident.
     *
     * <p>Toutes les conditions en défaut sont listées (pas seulement la première), pour l'historique.
     */
    public static Decision authorize(Request r) {
        Objects.requireNonNull(r, "request");
        Optional<Entry> found = find(r.vm());
        if (found.isEmpty()) {
            return new Decision(false, Mode.DENY, null, List.of(Refusal.UNKNOWN_VM));
        }
        Entry e = found.get();
        switch (e.mode()) {
            case AUTO:
                break;
            case DETECT_ONLY:
                return new Decision(false, e.mode(), e, List.of(Refusal.DETECTION_ONLY));
            default:
                return new Decision(false, e.mode(), e, List.of(Refusal.NOT_AUTHORIZED));
        }

        List<Refusal> refusals = new ArrayList<>();

        if (!e.incidentNamespace().equals(r.incidentNamespace())
                || !e.incidentApp().equals(r.incidentApp())
                || !e.incidentType().equals(r.incidentType())) {
            refusals.add(Refusal.SCOPE_MISMATCH);
        }

        PowerState state = r.powerState() == null ? PowerState.UNKNOWN : r.powerState();
        boolean factsIncomplete = r.maintenance() == null
                || r.failedEpisodeOpen() == null
                || r.msSinceLastRestartAction() == null
                || r.restartActionsForIncident() == null;

        switch (state) {
            case RUNNING -> refusals.add(Refusal.STATE_RUNNING);
            case UNKNOWN -> refusals.add(Refusal.STATE_UNKNOWN);
            case POWERED_OFF -> {
                if (r.evidenceAgeMs() == null
                        || r.poweredOffObservations() == null
                        || r.poweredOffSpanMs() == null
                        || r.probesFailing() == null) {
                    factsIncomplete = true;
                } else {
                    if (r.evidenceAgeMs() < 0 || r.evidenceAgeMs() > EVIDENCE_MAX_AGE_MS) {
                        refusals.add(Refusal.EVIDENCE_STALE);
                    }
                    if (r.poweredOffObservations() < CONFIRMATION_MIN_OBSERVATIONS
                            || r.poweredOffSpanMs() < CONFIRMATION_MIN_SPAN_MS) {
                        refusals.add(Refusal.NOT_CONFIRMED);
                    }
                    if (!r.probesFailing()) {
                        refusals.add(Refusal.PROBES_NOT_FAILING);
                    }
                }
            }
        }

        if (r.maintenance() != null && r.maintenance()) {
            refusals.add(Refusal.MAINTENANCE);
        }
        if (r.failedEpisodeOpen() != null && r.failedEpisodeOpen()) {
            refusals.add(Refusal.EPISODE_OPEN);
        }
        if (r.msSinceLastRestartAction() != null
                && r.msSinceLastRestartAction() < RESTART_COOLDOWN.toMillis()) {
            refusals.add(Refusal.COOLDOWN_ACTIVE);
        }
        if (r.restartActionsForIncident() != null && r.restartActionsForIncident() != 0) {
            refusals.add(Refusal.ACTION_ALREADY_EXISTS);
        }
        if (factsIncomplete) {
            refusals.add(Refusal.FACTS_INCOMPLETE);
        }

        // Ordre d'affichage = ordre de déclaration de l'énumération.
        refusals.sort(Comparator.comparingInt(Refusal::ordinal));
        return new Decision(refusals.isEmpty(), e.mode(), e, refusals);
    }
}
