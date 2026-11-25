package com.orange.pfe.backend.service.maintenance;

import com.orange.pfe.backend.config.AiopsPollingProperties;
import com.orange.pfe.backend.config.VmPowerProperties;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.ResourceResponse;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.service.KubernetesDiscoveryService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Set;

/**
 * Catalogue des ressources auxquelles une maintenance peut être associée, construit en LECTURE SEULE à partir de
 * sources HELIOS existantes : applications découvertes dans Kubernetes et VM de la configuration {@code helios.vmpower}.
 * Mis en cache 60 s. Ce service ne commande rien (aucune action sur le cluster ni sur les VM).
 *
 * <p>Validation : APPLICATION et VM sont contrôlées contre le catalogue lorsqu'il contient des entrées de ce type
 * (si la source est indisponible, la saisie est acceptée : on doit pouvoir planifier une maintenance justement quand
 * le cluster ne répond pas). NODE et SERVICE n'ont pas de source de référence fiable : saisie libre.</p>
 */
@Service
public class MaintenanceResourceCatalog {

    private static final Logger log = LoggerFactory.getLogger(MaintenanceResourceCatalog.class);
    private static final long TTL_MS = 60_000L;

    private final ObjectProvider<KubernetesDiscoveryService> discovery;
    private final ObjectProvider<VmPowerProperties> vmProperties;

    private volatile List<ResourceResponse> cache = List.of();
    private volatile long loadedAt = 0L;

    public MaintenanceResourceCatalog(ObjectProvider<KubernetesDiscoveryService> discovery,
                                      ObjectProvider<VmPowerProperties> vmProperties) {
        this.discovery = discovery;
        this.vmProperties = vmProperties;
    }

    public List<ResourceResponse> list() {
        long now = System.currentTimeMillis();
        if (now - loadedAt > TTL_MS) {
            synchronized (this) {
                if (now - loadedAt > TTL_MS) {
                    cache = load();
                    loadedAt = now;
                }
            }
        }
        return cache;
    }

    /** Valide la ressource et retourne son nom normalisé (casse du catalogue si elle y figure). */
    public String validateAndNormalize(MaintenanceResourceType type, String name) {
        String n = name == null ? "" : name.trim();
        if (n.isEmpty()) {
            throw new IllegalArgumentException("La ressource est obligatoire");
        }
        if (type == MaintenanceResourceType.NODE || type == MaintenanceResourceType.SERVICE) {
            return n;
        }
        List<ResourceResponse> known = list().stream().filter(r -> r.type() == type).toList();
        if (known.isEmpty()) {
            log.debug("Catalogue de ressources vide pour le type {} : saisie acceptée sans contrôle", type);
            return n;
        }
        return known.stream()
                .filter(r -> r.name().equalsIgnoreCase(n))
                .map(ResourceResponse::name)
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException(
                        "Ressource inconnue pour le type " + type + " : « " + n + " »"));
    }

    private List<ResourceResponse> load() {
        List<ResourceResponse> out = new ArrayList<>();
        try {
            KubernetesDiscoveryService service = discovery.getIfAvailable();
            if (service != null) {
                for (AiopsPollingProperties.Target t : service.discoverTargets()) {
                    String name = t.getNamespace() + "/" + t.getApp();
                    out.add(new ResourceResponse(MaintenanceResourceType.APPLICATION, name, "Application " + name));
                }
            }
        } catch (RuntimeException e) {
            log.warn("Catalogue de maintenance : découverte des applications indisponible ({})", e.toString());
        }
        try {
            VmPowerProperties props = vmProperties.getIfAvailable();
            Set<String> vms = props == null ? Set.of() : props.getActiveVms();
            for (String vm : vms) {
                out.add(new ResourceResponse(MaintenanceResourceType.VM, vm, "VM " + vm));
            }
        } catch (RuntimeException e) {
            log.warn("Catalogue de maintenance : liste des VM indisponible ({})", e.toString());
        }
        out.sort(Comparator.comparing(ResourceResponse::type).thenComparing(ResourceResponse::name, String.CASE_INSENSITIVE_ORDER));
        return List.copyOf(out);
    }
}
