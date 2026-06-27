package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import io.kubernetes.client.openapi.ApiClient;
import io.kubernetes.client.openapi.ApiException;
import io.kubernetes.client.openapi.Configuration;
import io.kubernetes.client.openapi.apis.CoordinationV1Api;
import io.kubernetes.client.openapi.apis.CoreV1Api;
import io.kubernetes.client.openapi.apis.NetworkingV1Api;
import io.kubernetes.client.openapi.models.V1Lease;
import io.kubernetes.client.openapi.models.V1NetworkPolicy;
import io.kubernetes.client.openapi.models.CoreV1Event;
import io.kubernetes.client.openapi.models.V1Node;
import io.kubernetes.client.openapi.models.V1NodeCondition;
import io.kubernetes.client.openapi.models.V1Pod;
import io.kubernetes.client.util.ClientBuilder;
import io.kubernetes.client.util.Config;
import io.kubernetes.client.util.KubeConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.FileReader;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

@Service
public class KubernetesSignalEnrichmentService {

    private static final Logger logger = LoggerFactory.getLogger(KubernetesSignalEnrichmentService.class);

    private final RemediationProperties properties;
    private volatile CoreV1Api coreV1Api;
    private volatile NetworkingV1Api networkingV1Api;
    private volatile CoordinationV1Api coordinationV1Api;
    // Per-app last cumulative HTTP metrics [total5xx, totalReq, durSum, durCount] for rate (delta) computation.
    private final java.util.concurrent.ConcurrentHashMap<String, double[]> lastHttpMetrics =
            new java.util.concurrent.ConcurrentHashMap<>();
    // Last time a worker node was seen NotReady. Per-app incidents are paused during the outage AND for a
    // GRACE window after recovery (pods keep rescheduling/restarting ~1-2 min after a node rejoins, which
    // would otherwise surface as per-app DEPLOYMENT_UNAVAILABLE/POD_CRASH_LOOP churn).
    private static final long NODE_OUTAGE_GRACE_MS = 300_000L; // 5 min post-recovery (pods rebalance slowly after a node rejoins)
    private volatile long lastWorkerOutageMs = 0L;
    // Fast node-failure signal: a node's Lease (kube-node-lease) renewTime is bumped every ~10s by a
    // healthy kubelet. When the node dies the renewTime FREEZES immediately — so a renewTime older than
    // this is a dead node, detected ~25-30s after power-off, BEFORE the ~40s Ready-condition flip. This
    // closes the race where a per-app DEPLOYMENT_UNAVAILABLE was created before the node was marked down.
    private static final long LEASE_STALE_MS = 30_000L;

    public KubernetesSignalEnrichmentService(RemediationProperties properties) {
        this.properties = properties;
    }

    public Map<String, Double> collectFeatureOverrides(String namespace, String app) {
        return collectFeatureOverrides(namespace, app, true);
    }

    /**
     * @param includePodKillSignal when {@code false}, the historical pod-kill detection is skipped.
     *        Used during post-remediation VERIFICATION: the remediation itself just killed/recreated
     *        the pod (expected), so the recent kill events would otherwise keep the AI confidence high
     *        for ~2 min and block resolution. ImagePullBackOff and node-not-ready (real ongoing
     *        failures) are always evaluated.
     */
    public Map<String, Double> collectFeatureOverrides(String namespace, String app, boolean includePodKillSignal) {
        Map<String, Double> overrides = new LinkedHashMap<>();

        try {
            ensureApi();
            List<V1Pod> pods = coreV1Api.listNamespacedPod(namespace)
                    .labelSelector("app=" + app)
                    .execute()
                    .getItems();

            Set<String> nodeNames = pods.stream()
                    .map(V1Pod::getSpec)
                    .filter(spec -> spec != null && spec.getNodeName() != null && !spec.getNodeName().isBlank())
                    .map(spec -> spec.getNodeName().trim())
                    .collect(Collectors.toSet());

            List<V1Node> nodes = coreV1Api.listNode().execute().getItems();
            double appNodeNotReadyCount = nodes.stream()
                    .filter(node -> node.getMetadata() != null && node.getMetadata().getName() != null)
                    .filter(node -> !nodeNames.isEmpty() && nodeNames.contains(node.getMetadata().getName()))
                    .mapToDouble(node -> isNodeReady(node) ? 0.0d : 1.0d)
                    .sum();
            double workerNodeNotReadyCount = nodes.stream()
                    .filter(this::isWorkerNode)
                    .mapToDouble(node -> isNodeReady(node) ? 0.0d : 1.0d)
                    .sum();

            overrides.put("node_not_ready_count", Math.max(appNodeNotReadyCount, workerNodeNotReadyCount));

            // A WORKER node is down: every app that had a pod on it is rescheduling onto the surviving
            // node, which surfaces as DEPLOYMENT_UNAVAILABLE / POD_CRASH_LOOP churn PER APP — pure
            // COLLATERAL of the node outage, not an app fault. Flag it so analyze() pauses per-app
            // incident creation while a node is down: the single k8s-nodes/<node> incident
            // (InfrastructureHealthScheduler) is the source of truth, avoiding a flood of per-app
            // duplicates. Detection-only (includePodKillSignal) so it never disturbs post-fix verify.
            // EARLY signal: a worker whose Lease renewTime froze is dead ~25-30s before its Ready
            // condition flips to NotReady (~40s). Combine both so the per-app pause kicks in fast and
            // we never create a per-app DEPLOYMENT_UNAVAILABLE before the node is recognized as down.
            double workerNodeStaleLease = countWorkerNodesWithStaleLease(nodes);
            double workerNodeTroubled = Math.max(workerNodeNotReadyCount, workerNodeStaleLease);
            if (workerNodeTroubled > 0) {
                lastWorkerOutageMs = System.currentTimeMillis();
            }
            boolean inOutageOrRecovery = workerNodeTroubled > 0
                    || (System.currentTimeMillis() - lastWorkerOutageMs) < NODE_OUTAGE_GRACE_MS;
            if (includePodKillSignal && inOutageOrRecovery) {
                overrides.put("node_outage_detected", 1.0);
                logger.info("Worker node outage/recovery window ({} NotReady, {} stale-lease) — per-app incident creation paused",
                        (int) workerNodeNotReadyCount, (int) workerNodeStaleLease);
            }

            // HELIOS-as-autoscaler: an app that opted-in via the pod annotation helios.io/autoscale=true
            // should, under high CPU, be SCALED UP (add capacity) rather than restarted. Flag it so the
            // orchestration reclassifies a CPU saturation on this app to AUTOSCALING + SCALE_DEPLOYMENT.
            boolean autoscaleOptIn = pods.stream().anyMatch(p -> p.getMetadata() != null
                    && p.getMetadata().getAnnotations() != null
                    && "true".equalsIgnoreCase(p.getMetadata().getAnnotations().get("helios.io/autoscale")));
            if (autoscaleOptIn) {
                overrides.put("autoscale_enabled", 1.0);
            }

            // Detect recent pod kill: inject pod_restarts_total so the AI detects the event
            // even when K8s has already recreated the pod (pod kill lasts only ~6s but
            // HELIOS polls every 30s — Prometheus would miss the brief unavailability).
            // Skipped during verification (includePodKillSignal=false): the remediation itself
            // caused the kill, so this signal would falsely keep the incident "active".
            if (includePodKillSignal) {
                double podKillSignal = detectRecentPodKillOrRestart(namespace, app, pods);
                if (podKillSignal > 0) {
                    overrides.put("pod_restarts_total", podKillSignal);
                    logger.info("pod_restarts_total override={} injected for {}/{} (pod kill detected)",
                            podKillSignal, namespace, app);
                }
            }

            // Detect ImagePullBackOff / ErrImagePull: the pod never starts so pod_restarts_total=0
            // and no Killing events are generated — the AI sees nothing and returns NO_INCIDENT.
            // Inject overrides so the AI classifies it as an incident, plus a marker key so the
            // orchestration layer can force ROLLBACK_DEPLOYMENT without relying on the AI action.
            // OOMKilled (memory test) — distinct from a generic crash: the container hit its memory
            // limit. Checked FIRST so it wins over the CrashLoopBackOff that OOM eventually produces.
            if (hasOOMKilled(pods)) {
                overrides.put("unavailable_replicas", Math.max(countNotReady(pods), 1.0));
                overrides.put("oom_killed_detected", 1.0);
                logger.info("OOMKilled detected for {}/{} — memory pressure", namespace, app);
            }
            // Pod Evicted (disk test) — ephemeral-storage / disk pressure forced the kubelet to evict.
            // The pod OBJECT (Evicted/Succeeded phase) is replaced within seconds by a healthy one, so the
            // 30s poll usually misses it -> we ALSO consult the kubelet Evicted EVENT, which persists ~1h.
            // The event check runs only during DETECTION (includePodKillSignal): during verification a stale
            // event must not keep the incident "open" after the delete-pods fix recreated a healthy pod.
            else if (hasEvictedPod(pods) || (includePodKillSignal && hasDiskEvictionEvent(namespace, app))) {
                overrides.put("unavailable_replicas", Math.max(countNotReady(pods), 1.0));
                overrides.put("disk_pressure_detected", 1.0);
                logger.info("Disk/ephemeral-storage eviction detected for {}/{}", namespace, app);
            }
            else if (hasImagePullFailure(pods)) {
                // Inject the REAL count of unavailable pods so the incident title reflects the actual
                // measurement (not a hard-coded constant) -> distinct titles per real situation.
                double unavail = Math.max(countNotReady(pods), 1.0);
                overrides.put("unavailable_replicas", unavail);
                overrides.put("pod_restarts_total", 3.0); // image never starts -> marker keeps detection on
                overrides.put("image_pull_backoff_detected", 1.0);
                logger.info("ImagePullBackOff override injected for {}/{} (unavailable={})", namespace, app, unavail);
            }

            // Detect CrashLoopBackOff (bad image / command / config). DELETE pod is futile here: the
            // ReplicaSet recreates the SAME bad spec. The ROOT-CAUSE fix is to roll back to the last
            // working revision. Inject a marker so the orchestration layer forces ROLLBACK_DEPLOYMENT
            // deterministically (independent of the AI restart-count timing).
            else if (hasCrashLoopBackOff(pods)) {
                // Use the REAL measured restart count and unavailable count so each incident's title
                // carries its actual numbers (e.g. "(7 redémarrages)") instead of a constant "(4)".
                double realRestarts = maxRestartCount(pods);
                double unavail = Math.max(countNotReady(pods), 1.0);
                overrides.put("unavailable_replicas", unavail);
                overrides.put("pod_restarts_total", realRestarts >= 1 ? realRestarts : 1.0);
                overrides.put("crash_loop_backoff_detected", 1.0);
                logger.info("CrashLoopBackOff override injected for {}/{} (restarts={}, unavailable={}) — rollback",
                        namespace, app, realRestarts, unavail);
            }
            // Config error (missing ConfigMap/Secret, bad env ref): the container cannot even be created.
            // Deterministic K8s state -> rollback to the last valid revision.
            else if (hasConfigError(pods)) {
                overrides.put("unavailable_replicas", Math.max(countNotReady(pods), 1.0));
                overrides.put("config_error_detected", 1.0);
                logger.info("Container config error (CreateContainerConfigError) for {}/{} — rollback", namespace, app);
            }
            // Init container failure: the init phase fails so the main container never starts -> rollback.
            else if (hasInitContainerFailure(pods)) {
                overrides.put("unavailable_replicas", Math.max(countNotReady(pods), 1.0));
                overrides.put("pod_restarts_total", Math.max(maxRestartCount(pods), 1.0));
                overrides.put("init_container_failure_detected", 1.0);
                logger.info("Init container failure for {}/{} — rollback", namespace, app);
            }
            // Pod stuck Pending / Unschedulable (oversized resource request): never scheduled -> rollback.
            else if (hasPendingUnschedulable(pods)) {
                overrides.put("unavailable_replicas", Math.max(countNotReady(pods), 1.0));
                overrides.put("pod_pending_unschedulable_detected", 1.0);
                logger.info("Pod Pending/Unschedulable (insufficient resources) for {}/{} — rollback", namespace, app);
            }
            // Running but persistently NOT READY (broken readiness probe): LOWEST priority pod-state signal,
            // only when none of the specific states above match -> DEPLOYMENT_UNAVAILABLE, rollback.
            else if (hasRunningNotReady(pods)) {
                overrides.put("unavailable_replicas", Math.max(countNotReady(pods), 1.0));
                overrides.put("deployment_unavailable_detected", 1.0);
                logger.info("Running-but-NotReady pod(s) for {}/{} — DEPLOYMENT_UNAVAILABLE, rollback", namespace, app);
            }

            // Active /metrics scrape (http503 + latency tests): podinfo is NOT a Prometheus scrape
            // target, so the AI never sees its HTTP signals. We scrape podinfo's own /metrics and
            // compute the RATE (delta vs the previous cycle) — the cumulative ratio is diluted by
            // thousands of healthy probe requests, but the recent burst stands out in the delta.
            double[] http = scrapeHttpDelta(namespace, app, pods); // [err5xxRatio, avgLatencySec]
            if (http[0] >= 0.05) {
                overrides.put("http_5xx_ratio", http[0]);
                overrides.put("http_error_storm_detected", 1.0);
                logger.info("HTTP 5xx storm for {}/{}: recent ratio={}", namespace, app, String.format("%.2f", http[0]));
            } else if (http[1] >= 1.0) { // recent average request latency >= 1s
                overrides.put("http_latency_p95_seconds", http[1]);
                overrides.put("http_latency_detected", 1.0);
                logger.info("HTTP latency for {}/{}: recent avg={}s", namespace, app, String.format("%.2f", http[1]));
            }

            // Active reachability probe: detect a network partition / unreachable pod.
            // cAdvisor keeps reporting metrics for a partitioned pod (node-local read) and the
            // kubelet readiness probe is node-local too, so the pod stays Ready and NO metric looks
            // abnormal. The only way to see a partition is to actively try to connect. We flag ONLY
            // when ALL Running+Ready pods fail a short TCP connect (a real partition hits every
            // replica; a transient blip rarely hits all at once) -> low false-positive risk.
            if (detectServiceUnreachable(pods)) {
                // A partition makes every replica unreachable -> use the real pod count for the title.
                double unreachable = Math.max(pods.size(), 1);
                overrides.put("unavailable_replicas", unreachable);
                overrides.put("service_unreachable_detected", 1.0);
                logger.info("Service unreachable for {}/{} — {} ready pod(s) fail TCP connect (partition?)",
                        namespace, app, unreachable);
            }

            // A recently-applied DENY NetworkPolicy is the ROOT CAUSE of partition / DNS / service
            // incidents — whether they manifest as "unreachable" (pods stay Ready) OR as NotReady/crash
            // (DNS egress denied -> readiness fails). Flag it with HIGH priority so the orchestration
            // REMOVES the policy (the real fix) instead of a futile rollback/restart.
            if (hasBlockingNetworkPolicy(namespace, app)) {
                overrides.put("unavailable_replicas", Math.max(countNotReady(pods), 1.0));
                overrides.put("blocking_network_policy_detected", 1.0);
                // A policy that allows egress but NOT port 53 is a DNS-blocking policy (dns test) ->
                // distinct DNS_FAILURE type rather than a generic SERVICE_UNAVAILABLE.
                if (isDnsBlockingPolicy(namespace, app)) {
                    overrides.put("dns_blocked", 1.0);
                    logger.info("DNS-blocking NetworkPolicy detected for {}/{} — DNS_FAILURE", namespace, app);
                } else if (isDbBlockingPolicy(namespace, app)) {
                    overrides.put("db_blocked", 1.0);
                    logger.info("DB-blocking NetworkPolicy (port 5432) detected for {}/{} — DATABASE_CONNECTIVITY", namespace, app);
                } else if (isPartitionPolicy(namespace, app)) {
                    overrides.put("network_partition", 1.0);
                    logger.info("Full-partition NetworkPolicy (ingress+egress) for {}/{} — NETWORK_PARTITION", namespace, app);
                } else {
                    logger.info("Blocking NetworkPolicy detected for {}/{} — remove-policy remediation", namespace, app);
                }
            }

            // Transient rollout guard: if NO definitive fault marker fired but a pod is freshly STARTING
            // (young, not-ready, no fault waiting-state) the deployment is just mid-rollout (e.g. a new
            // revision swapping in). The orchestration must NOT create a generic DEPLOYMENT_UNAVAILABLE
            // for this transient — it should wait one cycle until the new pod reaches its real state
            // (Running+Ready -> e.g. HTTP 5xx storm, or a fault). Flagged so analyze() can suppress.
            boolean anyFaultMarker = overrides.containsKey("image_pull_backoff_detected")
                    || overrides.containsKey("crash_loop_backoff_detected")
                    || overrides.containsKey("oom_killed_detected")
                    || overrides.containsKey("disk_pressure_detected")
                    || overrides.containsKey("config_error_detected")
                    || overrides.containsKey("init_container_failure_detected")
                    || overrides.containsKey("pod_pending_unschedulable_detected")
                    || overrides.containsKey("deployment_unavailable_detected")
                    || overrides.containsKey("http_error_storm_detected")
                    || overrides.containsKey("http_latency_detected")
                    || overrides.containsKey("service_unreachable_detected")
                    || overrides.containsKey("blocking_network_policy_detected");
            if (!anyFaultMarker && (hasYoungStartingPod(pods)
                    || (includePodKillSignal && (hasRestartingNotReadyPod(pods) || hasRecentlyCreatedPod(pods))))) {
                overrides.put("transient_rollout_detected", 1.0);
                logger.info("Transient/early-fault window (starting / restarting / freshly-rolled-out pod, no "
                        + "definitive marker yet) for {}/{} — defer classification to next cycle", namespace, app);
            }

        } catch (ApiException | IOException ex) {
            logger.debug(
                    "Failed to enrich AI prediction with Kubernetes signals for {}/{}: {}",
                    namespace,
                    app,
                    ex.getMessage()
            );
        }

        return overrides;
    }

    /**
     * Detects a recent pod kill using two complementary signals:
     *
     * 1. K8s events (reason=Killing/OOMKilling/Evicted) in the last 2 minutes —
     *    catches graceful deletes and OOMKills generated by the kubelet.
     *
     * 2. Pod age analysis: a deployment is considered to have had a recent pod kill
     *    when it has at least one "old" pod (>3 min) and at least one "new" pod (<2 min).
     *    The asymmetric thresholds avoid false positives during rolling updates and
     *    immediately after a RESTART_POD remediation (all pods become new at once,
     *    so the "old" condition is never met until the deployment stabilises).
     *
     * Returns a score > 0 to inject as pod_restarts_total override when a kill is
     * detected; 0 when the deployment looks healthy.
     */
    private double detectRecentPodKillOrRestart(String namespace, String app, List<V1Pod> pods) {
        double score = 0.0;
        OffsetDateTime now = OffsetDateTime.now();

        // --- Method 1: K8s events (kubelet Killing/OOMKilling/Evicted) ---
        try {
            OffsetDateTime cutoff = now.minusMinutes(2);
            List<CoreV1Event> events = coreV1Api.listNamespacedEvent(namespace).execute().getItems();
            long killEvents = events.stream()
                    .filter(e -> e.getInvolvedObject() != null && "Pod".equals(e.getInvolvedObject().getKind()))
                    .filter(e -> {
                        String name = e.getInvolvedObject().getName();
                        return name != null
                                && name.toLowerCase(Locale.ROOT).startsWith(app.toLowerCase(Locale.ROOT) + "-");
                    })
                    .filter(e -> {
                        String reason = e.getReason();
                        return reason != null && (
                                reason.equalsIgnoreCase("Killing") ||
                                reason.equalsIgnoreCase("OOMKilling") ||
                                reason.equalsIgnoreCase("Evicted") ||
                                reason.equalsIgnoreCase("Unhealthy")
                        );
                    })
                    .filter(e -> {
                        // lastTimestamp is more reliable; fall back to firstTimestamp
                        OffsetDateTime ts = e.getLastTimestamp() != null
                                ? e.getLastTimestamp() : e.getFirstTimestamp();
                        return ts != null && ts.isAfter(cutoff);
                    })
                    .count();
            if (killEvents > 0) {
                score = Math.max(score, killEvents * 2.0);
                logger.info("Pod kill events detected for {}/{}: {} event(s) (last 2 min)", namespace, app, killEvents);
            }
        } catch (Exception ex) {
            logger.debug("Event-based pod kill detection failed for {}/{}: {}", namespace, app, ex.getMessage());
        }

        // --- Method 2: Pod age analysis (catches force-delete with no events) ---
        // Old pod (>3 min) + brand-new pod (<2 min) = a pod was recently replaced.
        // Asymmetric thresholds ensure rolling updates and post-remediation states
        // do NOT trigger this check (all pods new simultaneously → hasOldPod=false).
        try {
            if (!pods.isEmpty()) {
                boolean hasOldPod = pods.stream()
                        .filter(p -> p.getMetadata() != null && p.getMetadata().getCreationTimestamp() != null)
                        .anyMatch(p -> p.getMetadata().getCreationTimestamp().isBefore(now.minusMinutes(3)));
                boolean hasNewPod = pods.stream()
                        .filter(p -> p.getMetadata() != null && p.getMetadata().getCreationTimestamp() != null)
                        .anyMatch(p -> p.getMetadata().getCreationTimestamp().isAfter(now.minusMinutes(2)));
                if (hasOldPod && hasNewPod) {
                    score = Math.max(score, 2.0);
                    logger.info("Pod replacement detected for {}/{}: old(>3m) and new(<2m) pod coexist",
                            namespace, app);
                }
            }
        } catch (Exception ex) {
            logger.debug("Age-based pod kill detection failed for {}/{}: {}", namespace, app, ex.getMessage());
        }

        return score;
    }

    /**
     * Returns true when every Running+Ready pod of the app fails a short TCP connect from the
     * backend — the signature of a network partition (NetworkPolicy deny, CNI issue) where
     * cAdvisor still reports metrics and the kubelet readiness probe (node-local) stays green.
     * Requires ALL ready pods to be unreachable to avoid false positives on a single transient blip.
     */
    private boolean detectServiceUnreachable(List<V1Pod> pods) {
        List<V1Pod> readyRunning = pods.stream()
                .filter(p -> p.getStatus() != null && "Running".equals(p.getStatus().getPhase()))
                .filter(this::isPodReadyForProbe)
                .filter(p -> p.getStatus().getPodIP() != null && !p.getStatus().getPodIP().isBlank())
                .collect(Collectors.toList());
        if (readyRunning.isEmpty()) {
            return false; // no ready pods -> other signals (unavailable_replicas) handle it
        }
        int unreachable = 0;
        for (V1Pod p : readyRunning) {
            int port = firstContainerPort(p);
            if (port <= 0) {
                return false; // unknown port -> cannot probe safely, avoid false positive
            }
            if (!tcpReachable(p.getStatus().getPodIP(), port, 2000)) {
                unreachable++;
            }
        }
        return unreachable == readyRunning.size();
    }

    private int firstContainerPort(V1Pod pod) {
        if (pod.getSpec() == null || pod.getSpec().getContainers() == null) {
            return -1;
        }
        return pod.getSpec().getContainers().stream()
                .filter(c -> c.getPorts() != null && !c.getPorts().isEmpty())
                .map(c -> c.getPorts().get(0).getContainerPort())
                .filter(port -> port != null)
                .findFirst()
                .orElse(-1);
    }

    private boolean isPodReadyForProbe(V1Pod pod) {
        if (pod.getStatus() == null || pod.getStatus().getConditions() == null) {
            return false;
        }
        return pod.getStatus().getConditions().stream()
                .anyMatch(c -> "Ready".equals(c.getType()) && "True".equalsIgnoreCase(c.getStatus()));
    }

    private boolean tcpReachable(String ip, int port, int timeoutMs) {
        try (java.net.Socket socket = new java.net.Socket()) {
            socket.connect(new java.net.InetSocketAddress(ip, port), timeoutMs);
            return true;
        } catch (Exception ex) {
            return false;
        }
    }

    private boolean hasImagePullFailure(List<V1Pod> pods) {
        return pods.stream().anyMatch(pod -> {
            if (pod.getStatus() == null || pod.getStatus().getContainerStatuses() == null) {
                return false;
            }
            return pod.getStatus().getContainerStatuses().stream().anyMatch(cs -> {
                if (cs.getState() == null || cs.getState().getWaiting() == null) return false;
                String reason = cs.getState().getWaiting().getReason();
                return "ImagePullBackOff".equals(reason) || "ErrImagePull".equals(reason);
            });
        });
    }

    /**
     * Detects a GENUINE crash loop using ONLY the kubelet's CrashLoopBackOff waiting reason.
     * This signal is set by the kubelet exclusively while a container is actively restarting in a
     * loop and is CLEARED as soon as it runs healthy again — so it is NOT sticky. We deliberately do
     * NOT fall back on cumulative restartCount / lastState (those linger after an old crash and would
     * mislabel a later, different incident — e.g. CPU or network — as POD_CRASH_LOOP once podinfo has
     * accumulated some churn across tests). Precise label = each test keeps its own correct type.
     */
    /** True if an exit code denotes a genuine application crash (non-zero), not a clean stop (0) nor an
     *  external kill (137=SIGKILL/OOM, 143=SIGTERM) which are pod-kill/eviction signals, not crash loops. */
    private boolean isAppCrashExit(Integer exitCode) {
        return exitCode != null && exitCode != 0 && exitCode != 137 && exitCode != 143;
    }

    private boolean hasCrashLoopBackOff(List<V1Pod> pods) {
        return pods.stream()
                // ignore a pod being torn down (its crash state is irrelevant — it's going away)
                .filter(pod -> pod.getMetadata() == null || pod.getMetadata().getDeletionTimestamp() == null)
                .anyMatch(pod -> {
                    if (pod.getStatus() == null || pod.getStatus().getContainerStatuses() == null) {
                        return false;
                    }
                    return pod.getStatus().getContainerStatuses().stream().anyMatch(cs -> {
                        if (cs.getState() == null) return false;
                        // (a) kubelet's explicit CrashLoopBackOff (the back-off WAIT phase)
                        boolean waiting = cs.getState().getWaiting() != null
                                && "CrashLoopBackOff".equals(cs.getState().getWaiting().getReason());
                        // (b) the ERROR phase BETWEEN restarts: container currently terminated with a
                        // non-zero exit AND it has already restarted >=2 times = a genuine loop (not a
                        // one-off kill). This catches the window where the kubelet hasn't set the
                        // CrashLoopBackOff waiting reason yet, so the marker (and ROLLBACK) fire reliably.
                        boolean erroring = cs.getState().getTerminated() != null
                                && cs.getState().getTerminated().getExitCode() != null
                                && cs.getState().getTerminated().getExitCode() != 0
                                && cs.getRestartCount() != null && cs.getRestartCount() >= 2;
                        // (c) the RUNNING window BETWEEN restarts: the container has just restarted and is
                        // momentarily Running (not yet Ready), so neither (a) nor (b) matches — but its
                        // LAST termination was a non-zero exit AND it has restarted >=2 times = a genuine
                        // crash loop caught early (a fast manual poll lands here). Excluding Ready pods
                        // avoids flagging a pod that crashed at startup then recovered.
                        boolean recentlyCrashed = !Boolean.TRUE.equals(cs.getReady())
                                && cs.getRestartCount() != null && cs.getRestartCount() >= 2
                                && cs.getLastState() != null && cs.getLastState().getTerminated() != null
                                && cs.getLastState().getTerminated().getExitCode() != null
                                && cs.getLastState().getTerminated().getExitCode() != 0;
                        // (d) the FIRST restart of an APPLICATION crash (exit code is a genuine error, not a
                        // kill 137/143 nor a clean stop 0). Closes the restartCount==1 gap: a fast manual
                        // poll otherwise lands between (no marker) and the ML mislabels it
                        // DEPLOYMENT_UNAVAILABLE + FORCE_RECREATE (which can't fix a bad spec). With this,
                        // a crash loop is POD_CRASH_LOOP + ROLLBACK from the very first failed restart.
                        Integer curExit = cs.getState().getTerminated() != null
                                ? cs.getState().getTerminated().getExitCode() : null;
                        Integer lastExit = (cs.getLastState() != null && cs.getLastState().getTerminated() != null)
                                ? cs.getLastState().getTerminated().getExitCode() : null;
                        boolean appCrashExit = isAppCrashExit(curExit) || isAppCrashExit(lastExit);
                        boolean earlyAppCrash = !Boolean.TRUE.equals(cs.getReady())
                                && cs.getRestartCount() != null && cs.getRestartCount() >= 1
                                && appCrashExit;
                        return waiting || erroring || recentlyCrashed || earlyAppCrash;
                    });
                });
    }

    /** Highest total container restart count across all pods (real measured value for the title). */
    private double maxRestartCount(List<V1Pod> pods) {
        int max = 0;
        for (V1Pod pod : pods) {
            if (pod.getStatus() == null || pod.getStatus().getContainerStatuses() == null) continue;
            int sum = 0;
            for (var cs : pod.getStatus().getContainerStatuses()) {
                if (cs.getRestartCount() != null) sum += cs.getRestartCount();
            }
            if (sum > max) max = sum;
        }
        return max;
    }

    /** Number of pods that are NOT ready (real measured "unavailable replicas" for the title). */
    private double countNotReady(List<V1Pod> pods) {
        return pods.stream().filter(p -> {
            // A pod being torn down (deletionTimestamp set) or already terminated (Succeeded/Failed) is
            // NOT an unavailable replica — it's going away. Counting it would falsely flag a deployment
            // as DEPLOYMENT_UNAVAILABLE during a normal rollout (e.g. while a crash-loop rollback's old
            // pod terminates), creating a spurious duplicate incident.
            if (p.getMetadata() != null && p.getMetadata().getDeletionTimestamp() != null) return false;
            if (p.getStatus() != null && p.getStatus().getPhase() != null
                    && ("Succeeded".equalsIgnoreCase(p.getStatus().getPhase())
                     || "Failed".equalsIgnoreCase(p.getStatus().getPhase()))) return false;
            if (p.getStatus() == null || p.getStatus().getContainerStatuses() == null
                    || p.getStatus().getContainerStatuses().isEmpty()) {
                return true; // no container status yet = not ready
            }
            return p.getStatus().getContainerStatuses().stream()
                    .anyMatch(cs -> !Boolean.TRUE.equals(cs.getReady()));
        }).count();
    }

    /** True if any container was last terminated by the kernel OOM killer (memory test). */
    private boolean hasOOMKilled(List<V1Pod> pods) {
        return pods.stream().anyMatch(pod -> {
            if (pod.getStatus() == null || pod.getStatus().getContainerStatuses() == null) return false;
            return pod.getStatus().getContainerStatuses().stream().anyMatch(cs -> {
                boolean cur = cs.getState() != null && cs.getState().getTerminated() != null
                        && "OOMKilled".equals(cs.getState().getTerminated().getReason());
                boolean last = cs.getLastState() != null && cs.getLastState().getTerminated() != null
                        && "OOMKilled".equals(cs.getLastState().getTerminated().getReason());
                return cur || last;
            });
        });
    }

    /**
     * True if a pod was evicted by the kubelet for disk / ephemeral-storage pressure (disk test).
     * Two forms: (1) the classic hard eviction (status.reason == "Evicted"); (2) on this cluster the
     * kubelet evicts gracefully (SIGTERM) so podinfo exits 0 and the pod shows phase=Succeeded
     * ("Completed"). A long-running SERVICE pod in a Deployment must never complete on its own, so a
     * Succeeded pod that shares the SAME pod-template-hash as a still-Running pod = an eviction (NOT a
     * rollout, whose terminated pods carry a DIFFERENT hash than the new Running pod).
     */
    private boolean hasEvictedPod(List<V1Pod> pods) {
        if (pods.stream().anyMatch(pod ->
                pod.getStatus() != null && "Evicted".equalsIgnoreCase(pod.getStatus().getReason()))) {
            return true;
        }
        java.util.Set<String> runningHashes = pods.stream()
                .filter(p -> p.getStatus() != null && "Running".equalsIgnoreCase(p.getStatus().getPhase())
                        && p.getMetadata() != null && p.getMetadata().getLabels() != null)
                .map(p -> p.getMetadata().getLabels().get("pod-template-hash"))
                .filter(java.util.Objects::nonNull)
                .collect(java.util.stream.Collectors.toSet());
        // Only when a SINGLE revision is running (no rollout in progress). During the FORCE_RECREATE
        // remediation rollout the old + new pods coexist (two hashes) and the old Succeeded pod would
        // briefly re-match its old-hash Running sibling — flagging that transient would make the recovery
        // verification fail. Restricting to one running hash = the self-healed eviction, not rollout churn.
        if (runningHashes.size() != 1) {
            return false;
        }
        return pods.stream().anyMatch(p -> p.getStatus() != null
                && "Succeeded".equalsIgnoreCase(p.getStatus().getPhase())
                && p.getMetadata() != null && p.getMetadata().getLabels() != null
                && runningHashes.contains(p.getMetadata().getLabels().get("pod-template-hash")));
    }

    /**
     * True if the kubelet emitted an "Evicted" EVENT for this app's pods because an EmptyDir /
     * ephemeral-storage volume exceeded its sizeLimit (disk test). On this cluster the evicted pod is
     * killed and replaced by a healthy one within seconds — far faster than the 30s poll — so the pod
     * OBJECT state (hasEvictedPod) usually misses it; the EVENT, however, persists ~1h. We use a 5-minute
     * window so the next poll after the eviction catches it, then it ages out (no false re-detection once
     * resolved). Consulted only during detection (see caller) so it never blocks post-fix verification.
     */
    private boolean hasDiskEvictionEvent(String namespace, String app) {
        try {
            OffsetDateTime cutoff = OffsetDateTime.now().minusMinutes(5);
            String prefix = app.toLowerCase(Locale.ROOT) + "-";
            List<CoreV1Event> events = coreV1Api.listNamespacedEvent(namespace).execute().getItems();
            return events.stream()
                    .filter(e -> e.getInvolvedObject() != null && "Pod".equals(e.getInvolvedObject().getKind()))
                    .filter(e -> {
                        String name = e.getInvolvedObject().getName();
                        return name != null && name.toLowerCase(Locale.ROOT).startsWith(prefix);
                    })
                    .filter(e -> "Evicted".equalsIgnoreCase(e.getReason()))
                    .filter(e -> {
                        String msg = e.getMessage();
                        return msg != null && (msg.contains("EmptyDir")
                                || msg.contains("ephemeral-storage")
                                || msg.toLowerCase(Locale.ROOT).contains("exceeds the limit"));
                    })
                    .anyMatch(e -> {
                        OffsetDateTime ts = e.getLastTimestamp() != null ? e.getLastTimestamp() : e.getFirstTimestamp();
                        return ts != null && ts.isAfter(cutoff);
                    });
        } catch (Exception ex) {
            logger.debug("Disk eviction event detection failed for {}/{}: {}", namespace, app, ex.getMessage());
            return false;
        }
    }

    /**
     * True if a pod is stuck PENDING because it cannot be scheduled (insufficient CPU/memory, or any
     * Unschedulable reason). Deterministic K8s state — the AI never sees a Pending pod (no metrics).
     * Root-cause fix = rollback (revert the oversized resource request).
     */
    private boolean hasPendingUnschedulable(List<V1Pod> pods) {
        return pods.stream().anyMatch(pod -> {
            if (pod.getStatus() == null || !"Pending".equalsIgnoreCase(pod.getStatus().getPhase())) return false;
            if (pod.getStatus().getConditions() == null) return false;
            return pod.getStatus().getConditions().stream().anyMatch(cond ->
                    "PodScheduled".equals(cond.getType()) && "False".equalsIgnoreCase(cond.getStatus())
                            && cond.getReason() != null && cond.getReason().contains("Unschedulable"));
        });
    }

    /** True if any pod's INIT container is failing (CrashLoopBackOff, or terminated with a non-zero code). */
    private boolean hasInitContainerFailure(List<V1Pod> pods) {
        return pods.stream().anyMatch(pod -> {
            if (pod.getStatus() == null || pod.getStatus().getInitContainerStatuses() == null) return false;
            return pod.getStatus().getInitContainerStatuses().stream().anyMatch(cs -> {
                if (cs.getState() == null) return false;
                boolean waitingBad = cs.getState().getWaiting() != null
                        && cs.getState().getWaiting().getReason() != null
                        && (cs.getState().getWaiting().getReason().contains("CrashLoopBackOff")
                         || cs.getState().getWaiting().getReason().contains("Error"));
                boolean termBad = cs.getState().getTerminated() != null
                        && cs.getState().getTerminated().getExitCode() != null
                        && cs.getState().getTerminated().getExitCode() != 0;
                return waitingBad || termBad;
            });
        });
    }

    /**
     * True if at least one pod is freshly STARTING (age < 90s, not ready) in a benign way — i.e. its
     * containers are just coming up (ContainerCreating / PodInitializing / running-not-yet-ready), NOT
     * in any fault waiting-state. This marks a transient rolling update; classification should wait.
     */
    private boolean hasYoungStartingPod(List<V1Pod> pods) {
        java.time.OffsetDateTime cutoff = java.time.OffsetDateTime.now().minusSeconds(90);
        java.util.Set<String> faultReasons = java.util.Set.of("CrashLoopBackOff", "ImagePullBackOff",
                "ErrImagePull", "CreateContainerConfigError", "CreateContainerError", "InvalidImageName");
        return pods.stream().anyMatch(pod -> {
            if (pod.getStatus() == null || pod.getMetadata() == null
                    || pod.getMetadata().getCreationTimestamp() == null
                    || pod.getMetadata().getCreationTimestamp().isBefore(cutoff)) return false; // not young
            String phase = pod.getStatus().getPhase();
            if (pod.getStatus().getContainerStatuses() == null
                    || pod.getStatus().getContainerStatuses().isEmpty()) {
                return "Pending".equalsIgnoreCase(phase); // pod accepted, containers not created yet
            }
            return pod.getStatus().getContainerStatuses().stream().anyMatch(cs -> {
                if (Boolean.TRUE.equals(cs.getReady())) return false; // ready container = not starting
                if (cs.getState() == null) return true;
                if (cs.getState().getWaiting() != null) {
                    String r = cs.getState().getWaiting().getReason();
                    return r == null || !faultReasons.contains(r); // benign waiting (e.g. ContainerCreating)
                }
                return cs.getState().getRunning() != null; // running but not yet ready = still starting
            });
        });
    }

    /**
     * True if a pod has restarted at least once and is currently NOT ready — a crash / OOM still settling,
     * where the SPECIFIC marker (crash loop / OOMKilled) is not visible at THIS exact poll (e.g. the
     * container is momentarily Running between two crashes, or K8s GC'd lastState). We defer the generic
     * DEPLOYMENT_UNAVAILABLE classification until the next poll surfaces the real type (-> POD_CRASH_LOOP /
     * MEMORY_STRESS + ROLLBACK), instead of locking an early wrong type via dedup. A true
     * DEPLOYMENT_UNAVAILABLE (broken readiness probe) has restartCount==0 and is unaffected.
     */
    private boolean hasRestartingNotReadyPod(List<V1Pod> pods) {
        return pods.stream()
                .filter(p -> p.getMetadata() == null || p.getMetadata().getDeletionTimestamp() == null)
                .anyMatch(pod -> {
                    if (pod.getStatus() == null || pod.getStatus().getContainerStatuses() == null) return false;
                    return pod.getStatus().getContainerStatuses().stream().anyMatch(cs ->
                            cs.getRestartCount() != null && cs.getRestartCount() >= 1
                                    && !Boolean.TRUE.equals(cs.getReady()));
                });
    }

    /**
     * True if any pod was (re)created very recently (a rollout just happened — e.g. the fault injection
     * patched the deployment). A generic DEPLOYMENT_UNAVAILABLE in this ~90s window is almost always the
     * rollout transient (old pod terminating / availability metric lagging), while the deterministic app
     * signal (5xx / latency scrape, OOM, crash loop) needs a cycle of READY traffic to surface. We defer
     * the DEPLOYMENT_UNAVAILABLE classification until the fresh pod has served enough to reveal its real
     * type — so http5xx -> HIGH_ERROR_RATE, latency -> LATENCY_DEGRADATION, etc. instead of a wrong early
     * DEPLOYMENT_UNAVAILABLE. A genuine bad-readiness DEPLOYMENT_UNAVAILABLE still fires after the window
     * (hasRunningNotReady requires age > 90s anyway).
     */
    private boolean hasRecentlyCreatedPod(List<V1Pod> pods) {
        java.time.OffsetDateTime cutoff = java.time.OffsetDateTime.now().minusSeconds(90);
        return pods.stream()
                .filter(p -> p.getMetadata() == null || p.getMetadata().getDeletionTimestamp() == null)
                .anyMatch(p -> p.getMetadata() != null && p.getMetadata().getCreationTimestamp() != null
                        && p.getMetadata().getCreationTimestamp().isAfter(cutoff));
    }

    /** True if a container cannot be created due to a config error (missing ConfigMap/Secret, bad env ref). */
    private boolean hasConfigError(List<V1Pod> pods) {
        return pods.stream().anyMatch(pod -> {
            if (pod.getStatus() == null || pod.getStatus().getContainerStatuses() == null) return false;
            return pod.getStatus().getContainerStatuses().stream().anyMatch(cs -> {
                if (cs.getState() == null || cs.getState().getWaiting() == null) return false;
                String r = cs.getState().getWaiting().getReason();
                return "CreateContainerConfigError".equals(r) || "CreateContainerError".equals(r)
                        || "InvalidImageName".equals(r);
            });
        });
    }

    /**
     * True if at least one pod is RUNNING but persistently NOT READY (a broken readiness probe), with NO
     * restarts — i.e. a DEPLOYMENT_UNAVAILABLE that is neither a crash loop nor an image/config error.
     * Guarded by pod age > 90s so a normal in-flight rollout (briefly NotReady) does not false-positive.
     * Root-cause fix = rollback (revert the bad probe/config).
     */
    private boolean hasRunningNotReady(List<V1Pod> pods) {
        java.time.OffsetDateTime cutoff = java.time.OffsetDateTime.now().minusSeconds(90);
        return pods.stream().anyMatch(pod -> {
            if (pod.getStatus() == null || pod.getStatus().getContainerStatuses() == null
                    || pod.getStatus().getContainerStatuses().isEmpty()) return false;
            if (!"Running".equalsIgnoreCase(pod.getStatus().getPhase())) return false;
            if (pod.getMetadata() == null || pod.getMetadata().getCreationTimestamp() == null
                    || pod.getMetadata().getCreationTimestamp().isAfter(cutoff)) return false; // too young
            return pod.getStatus().getContainerStatuses().stream().anyMatch(cs ->
                    Boolean.TRUE.equals(cs.getStarted())
                            && !Boolean.TRUE.equals(cs.getReady())
                            && cs.getState() != null && cs.getState().getRunning() != null
                            && (cs.getRestartCount() == null || cs.getRestartCount() == 0));
        });
    }

    /**
     * Active scrape of podinfo's OWN /metrics endpoint (podinfo is not a Prometheus scrape target, so
     * the AI never sees its HTTP error rate). Returns the cumulative HTTP 5xx ratio, else 0.
     */
    /**
     * Scrape podinfo's /metrics on ALL pods and return RATE-based signals as a delta vs the previous
     * cycle: [recent 5xx ratio, recent average request latency in seconds]. Rate-based because the
     * cumulative ratio is diluted by thousands of healthy probe requests over the pod's lifetime.
     */
    private double[] scrapeHttpDelta(String namespace, String app, List<V1Pod> pods) {
        double total = 0, fivexx = 0, durSum = 0, durCount = 0;
        java.util.regex.Pattern statusPat = java.util.regex.Pattern.compile("status=\"(\\d{3})\"");
        for (V1Pod pod : pods) {
            if (!isPodReadyForProbe(pod)) continue;
            String ip = pod.getStatus().getPodIP();
            int port = firstContainerPort(pod);
            if (ip == null || port <= 0) continue;
            try {
                java.net.http.HttpClient client = java.net.http.HttpClient.newBuilder()
                        .connectTimeout(java.time.Duration.ofSeconds(2)).build();
                java.net.http.HttpRequest req = java.net.http.HttpRequest.newBuilder()
                        .uri(java.net.URI.create("http://" + ip + ":" + port + "/metrics"))
                        .timeout(java.time.Duration.ofSeconds(3)).GET().build();
                java.net.http.HttpResponse<String> resp = client.send(req,
                        java.net.http.HttpResponse.BodyHandlers.ofString());
                if (resp.statusCode() != 200) continue;
                for (String line : resp.body().split("\n")) {
                    int sp = line.lastIndexOf(' ');
                    if (sp < 0) continue;
                    double val;
                    try { val = Double.parseDouble(line.substring(sp + 1).trim()); } catch (Exception e) { continue; }
                    if (line.startsWith("http_requests_total")) {
                        java.util.regex.Matcher m = statusPat.matcher(line);
                        if (m.find()) { total += val; if (Integer.parseInt(m.group(1)) >= 500) fivexx += val; }
                    } else if (line.startsWith("http_request_duration_seconds_sum")) {
                        durSum += val;
                    } else if (line.startsWith("http_request_duration_seconds_count")) {
                        durCount += val;
                    }
                }
            } catch (Exception ex) {
                logger.debug("metrics scrape failed for {}: {}", ip, ex.getMessage());
            }
        }
        String key = namespace + "/" + app;
        double[] prev = lastHttpMetrics.put(key, new double[]{fivexx, total, durSum, durCount});
        if (prev == null) return new double[]{0.0, 0.0}; // first scrape: just seed the baseline
        // COUNTER RESET: when the pod is recreated (the fault injection rolls out a new pod), podinfo's
        // /metrics counters restart from ~0. Comparing to the OLD (large) baseline gives a negative delta
        // and HIDES a real 5xx storm / latency on the fresh pod for a full cycle — long enough for a
        // transient DEPLOYMENT_UNAVAILABLE to lock the type via dedup. Detect the reset (any counter went
        // DOWN) and use a zero baseline, so the new pod's cumulative counts are used directly and the storm
        // is detected on the FIRST scrape after the rollout.
        if (fivexx < prev[0] || total < prev[1] || durCount < prev[3]) {
            prev = new double[]{0.0, 0.0, 0.0, 0.0};
        }
        double d5xx = fivexx - prev[0], dTot = total - prev[1];
        double dSum = durSum - prev[2], dCnt = durCount - prev[3];
        double ratio = dTot > 0 ? d5xx / dTot : 0.0;
        double avgLat = dCnt > 0 ? dSum / dCnt : 0.0;
        return new double[]{ratio, avgLat};
    }

    /** True if a recent egress NetworkPolicy blocks DNS (egress rules exist but none allow port 53). */
    private boolean isDnsBlockingPolicy(String namespace, String app) {
        try {
            for (V1NetworkPolicy np : networkingV1Api.listNamespacedNetworkPolicy(namespace).execute().getItems()) {
                if (np.getSpec() == null || !policySelectsApp(np, app)) continue;
                if (npBlocksDns(np)) return true;
            }
        } catch (ApiException ex) {
            logger.debug("DNS policy check failed for {}/{}: {}", namespace, app, ex.getMessage());
        }
        return false;
    }

    /** True if the NetworkPolicy podSelector targets this app (or all pods via empty selector). */
    private boolean policySelectsApp(V1NetworkPolicy np, String app) {
        if (np.getSpec() == null || np.getSpec().getPodSelector() == null) return false;
        Map<String, String> ml = np.getSpec().getPodSelector().getMatchLabels();
        return ml == null || ml.isEmpty() || app.equals(ml.get("app"));
    }

    /**
     * True if a RECENTLY-applied (&lt;30 min) NetworkPolicy that selects this app (or all pods) DENIES
     * its traffic — the deterministic root cause of a partition/DNS/service incident. Matches the
     * executor's removal logic so detection and remediation are consistent.
     */
    private boolean hasBlockingNetworkPolicy(String namespace, String app) {
        try {
            for (V1NetworkPolicy np : networkingV1Api.listNamespacedNetworkPolicy(namespace).execute().getItems()) {
                // No recency gate: a deny policy is a problem while it EXISTS (pfe has no legitimate
                // baseline policy). Detect full-deny, DNS-block (egress without 53) and partition.
                if (policySelectsApp(np, app) && (deniesTraffic(np) || npBlocksDns(np) || npBlocksDb(np))) {
                    return true;
                }
            }
        } catch (ApiException ex) {
            logger.debug("NetworkPolicy check failed for {}/{}: {}", namespace, app, ex.getMessage());
        }
        return false;
    }

    /** True if the policy declares Egress with rules but NONE allow port 53 (DNS blocked). */
    private boolean npBlocksDns(V1NetworkPolicy np) {
        if (np.getSpec() == null) return false;
        List<String> types = np.getSpec().getPolicyTypes();
        if (types == null || !types.contains("Egress")) return false;
        var egress = np.getSpec().getEgress();
        if (egress == null || egress.isEmpty()) return false; // full deny handled by deniesTraffic
        return egress.stream().noneMatch(rule ->
                rule.getPorts() != null && rule.getPorts().stream().anyMatch(p ->
                        p.getPort() != null && "53".equals(p.getPort().toString())));
    }

    /** True if a recent egress NetworkPolicy blocks the DATABASE (egress allows DNS:53 but NOT 5432). */
    private boolean isDbBlockingPolicy(String namespace, String app) {
        try {
            for (V1NetworkPolicy np : networkingV1Api.listNamespacedNetworkPolicy(namespace).execute().getItems()) {
                if (policySelectsApp(np, app) && npBlocksDb(np)) return true;
            }
        } catch (ApiException ex) {
            logger.debug("db policy check failed for {}/{}: {}", namespace, app, ex.getMessage());
        }
        return false;
    }

    /**
     * True if the policy declares Egress with rules that ALLOW DNS (port 53) but NOT the database
     * (port 5432) — i.e. name resolution works but the app cannot reach its DB (database-down test).
     * Distinct from npBlocksDns (which blocks 53) so it maps to DATABASE_CONNECTIVITY, not DNS_FAILURE.
     */
    private boolean npBlocksDb(V1NetworkPolicy np) {
        if (np.getSpec() == null) return false;
        List<String> types = np.getSpec().getPolicyTypes();
        if (types == null || !types.contains("Egress")) return false;
        var egress = np.getSpec().getEgress();
        if (egress == null || egress.isEmpty()) return false;
        boolean allows53 = egress.stream().anyMatch(rule ->
                rule.getPorts() != null && rule.getPorts().stream().anyMatch(p ->
                        p.getPort() != null && "53".equals(p.getPort().toString())));
        boolean allows5432 = egress.stream().anyMatch(rule ->
                rule.getPorts() != null && rule.getPorts().stream().anyMatch(p ->
                        p.getPort() != null && "5432".equals(p.getPort().toString())));
        return allows53 && !allows5432;
    }

    /** True if a policy selecting the app denies BOTH ingress AND egress (full partition — network test). */
    private boolean isPartitionPolicy(String namespace, String app) {
        try {
            for (V1NetworkPolicy np : networkingV1Api.listNamespacedNetworkPolicy(namespace).execute().getItems()) {
                if (np.getSpec() == null || !policySelectsApp(np, app)) continue;
                List<String> types = np.getSpec().getPolicyTypes();
                if (types == null) continue;
                boolean ingressDenied = types.contains("Ingress")
                        && (np.getSpec().getIngress() == null || np.getSpec().getIngress().isEmpty());
                boolean egressDenied = types.contains("Egress")
                        && (np.getSpec().getEgress() == null || np.getSpec().getEgress().isEmpty());
                if (ingressDenied && egressDenied) return true;
            }
        } catch (ApiException ex) {
            logger.debug("partition policy check failed for {}/{}: {}", namespace, app, ex.getMessage());
        }
        return false;
    }

    /** A policy denies traffic when it declares a policyType (Ingress/Egress) but lists no rules for it. */
    private boolean deniesTraffic(V1NetworkPolicy np) {
        List<String> types = np.getSpec().getPolicyTypes();
        if (types == null || types.isEmpty()) return false;
        boolean ingressDenied = types.contains("Ingress")
                && (np.getSpec().getIngress() == null || np.getSpec().getIngress().isEmpty());
        boolean egressDenied = types.contains("Egress")
                && (np.getSpec().getEgress() == null || np.getSpec().getEgress().isEmpty());
        return ingressDenied || egressDenied;
    }

    private boolean isNodeReady(V1Node node) {
        if (node.getStatus() == null || node.getStatus().getConditions() == null) {
            return false;
        }

        for (V1NodeCondition condition : node.getStatus().getConditions()) {
            if (!"Ready".equals(condition.getType())) {
                continue;
            }
            return "True".equalsIgnoreCase(String.valueOf(condition.getStatus()));
        }

        return false;
    }

    /**
     * Counts WORKER nodes whose kube-node-lease Lease renewTime is stale (older than LEASE_STALE_MS) —
     * the fast, reliable "node is dead" signal Kubernetes itself uses (lease renews every ~10s on a
     * healthy node). Degrades gracefully to 0 on any error (RBAC/API), so detection still falls back to
     * the slower Ready-condition flip. Only worker nodes are considered (the master is never powered off).
     */
    private double countWorkerNodesWithStaleLease(List<V1Node> nodes) {
        try {
            if (coordinationV1Api == null) {
                return 0d;
            }
            Set<String> workerNames = nodes.stream()
                    .filter(this::isWorkerNode)
                    .filter(n -> n.getMetadata() != null && n.getMetadata().getName() != null)
                    .map(n -> n.getMetadata().getName())
                    .collect(Collectors.toSet());
            if (workerNames.isEmpty()) {
                return 0d;
            }
            long nowMs = System.currentTimeMillis();
            List<V1Lease> leases = coordinationV1Api.listNamespacedLease("kube-node-lease").execute().getItems();
            return leases.stream()
                    .filter(l -> l.getMetadata() != null && workerNames.contains(l.getMetadata().getName()))
                    .filter(l -> l.getSpec() != null && l.getSpec().getRenewTime() != null)
                    .filter(l -> nowMs - l.getSpec().getRenewTime().toInstant().toEpochMilli() > LEASE_STALE_MS)
                    .count();
        } catch (Exception e) {
            logger.debug("Lease staleness check unavailable ({}) — falling back to Ready-condition detection",
                    e.getMessage());
            return 0d;
        }
    }

    private boolean isWorkerNode(V1Node node) {
        if (node.getMetadata() == null || node.getMetadata().getLabels() == null) {
            return true;
        }

        Map<String, String> labels = node.getMetadata().getLabels();
        // HA control-plane : worker1/worker2 sont devenus control-plane -> l'absence du role
        // control-plane ne suffit plus a les distinguer (les 3 noeuds sont control-plane). On
        // identifie les VRAIS workers (ceux qui hebergent les apps) par le label custom
        // node-type=worker (le master ne l'a pas). Sans ca, la coupure d'un worker n'active plus
        // la grace d'outage -> faux incidents applicatifs collateraux (ex. HIGH_ERROR_RATE).
        if ("worker".equalsIgnoreCase(labels.get("node-type"))) {
            return true;
        }
        return !labels.containsKey("node-role.kubernetes.io/control-plane")
                && !labels.containsKey("node-role.kubernetes.io/master");
    }

    private void ensureApi() throws IOException {
        if (coreV1Api != null) {
            return;
        }

        synchronized (this) {
            if (coreV1Api != null) {
                return;
            }

            ApiClient client = buildClient();
            Configuration.setDefaultApiClient(client);
            coreV1Api = new CoreV1Api(client);
            networkingV1Api = new NetworkingV1Api(client);
            coordinationV1Api = new CoordinationV1Api(client);
        }
    }

    private ApiClient buildClient() throws IOException {
        String kubeconfigPath = properties.getKubeconfigPath();
        if (kubeconfigPath != null && !kubeconfigPath.isBlank() && Files.exists(Path.of(kubeconfigPath))) {
            try (FileReader fileReader = new FileReader(kubeconfigPath)) {
                KubeConfig kubeConfig = KubeConfig.loadKubeConfig(fileReader);
                return ClientBuilder.kubeconfig(kubeConfig).build();
            }
        }
        try {
            return ClientBuilder.cluster().build();
        } catch (RuntimeException ex) {
            logger.info(
                    "In-cluster Kubernetes client configuration unavailable for signal enrichment, falling back to default client discovery"
            );
        }
        return Config.defaultClient();
    }
}
