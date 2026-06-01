package com.orange.pfe.backend.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.Stream;

@ConfigurationProperties(prefix = "aiops.polling")
public class AiopsPollingProperties {

    /** Représente une cible de surveillance (namespace + application). */
    public static class Target {
        private String namespace;
        private String app;

        public Target() {}

        public Target(String namespace, String app) {
            this.namespace = namespace;
            this.app = app;
        }

        public String getNamespace() { return namespace; }
        public void setNamespace(String namespace) { this.namespace = namespace; }

        public String getApp() { return app; }
        public void setApp(String app) { this.app = app; }

        @Override
        public boolean equals(Object o) {
            if (this == o) return true;
            if (!(o instanceof Target t)) return false;
            return Objects.equals(namespace, t.namespace) && Objects.equals(app, t.app);
        }

        @Override
        public int hashCode() { return Objects.hash(namespace, app); }

        @Override
        public String toString() { return namespace + "/" + app; }
    }

    private boolean enabled = true;
    private long intervalMillis = 120_000L;
    private int windowMinutes = 5;

    /** Découverte automatique via label Kubernetes aiops.io/monitor=true */
    private boolean discoveryEnabled = true;

    /**
     * Watch Kubernetes en temps réel : déclenche l'analyse IA immédiatement
     * quand un nouveau Deployment est créé ou mis à jour dans le cluster.
     */
    private boolean watchEnabled = true;

    /**
     * Délai (secondes) entre la détection d'un nouveau Deployment et le premier
     * appel IA — laisse le temps aux pods de démarrer avant d'analyser.
     */
    private int watchInitialDelaySeconds = 45;

    /**
     * Délai minimum (secondes) entre deux analyses consécutives du même Deployment
     * déclenchées par le watcher (debounce sur MODIFIED events).
     */
    private int watchDebounceSeconds = 120;

    /**
     * Liste de cibles statiques (namespace+app).
     * Rétro-compatible : si vide, on replie sur namespace + apps.
     */
    private List<Target> targets = new ArrayList<>();

    /** Rétro-compatibilité : namespace unique pour la propriété apps */
    private String namespace = "pfe";

    /** Rétro-compatibilité : liste d'apps dans le namespace unique */
    private Set<String> apps = new LinkedHashSet<>(Set.of("backend"));

    /**
     * Retourne la liste effective des cibles statiques :
     * - si targets est renseigné → on utilise targets
     * - sinon on construit à partir de namespace + apps (ancien comportement)
     */
    public List<Target> getEffectiveTargets() {
        if (!targets.isEmpty()) {
            return List.copyOf(targets);
        }
        return apps.stream()
                .map(app -> new Target(namespace, app))
                .collect(Collectors.toList());
    }

    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }

    public long getIntervalMillis() { return intervalMillis; }
    public void setIntervalMillis(long intervalMillis) { this.intervalMillis = intervalMillis; }

    public int getWindowMinutes() { return windowMinutes; }
    public void setWindowMinutes(int windowMinutes) { this.windowMinutes = windowMinutes; }

    public boolean isDiscoveryEnabled() { return discoveryEnabled; }
    public void setDiscoveryEnabled(boolean discoveryEnabled) { this.discoveryEnabled = discoveryEnabled; }

    public List<Target> getTargets() { return targets; }
    public void setTargets(List<Target> targets) { this.targets = targets; }

    public String getNamespace() { return namespace; }
    public void setNamespace(String namespace) { this.namespace = namespace; }

    public Set<String> getApps() { return apps; }
    public void setApps(Set<String> apps) { this.apps = apps; }

    public boolean isWatchEnabled() { return watchEnabled; }
    public void setWatchEnabled(boolean watchEnabled) { this.watchEnabled = watchEnabled; }

    public int getWatchInitialDelaySeconds() { return watchInitialDelaySeconds; }
    public void setWatchInitialDelaySeconds(int watchInitialDelaySeconds) {
        this.watchInitialDelaySeconds = watchInitialDelaySeconds;
    }

    public int getWatchDebounceSeconds() { return watchDebounceSeconds; }
    public void setWatchDebounceSeconds(int watchDebounceSeconds) {
        this.watchDebounceSeconds = watchDebounceSeconds;
    }
}
