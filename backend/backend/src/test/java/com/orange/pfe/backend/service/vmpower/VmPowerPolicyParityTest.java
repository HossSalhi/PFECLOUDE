package com.orange.pfe.backend.service.vmpower;

import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.Entry;
import org.junit.jupiter.api.Test;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.fail;

/**
 * VMDOWN-POLICY-20261105 — PARITÉ : {@code infra/helios-vm-policy.yaml} (source de vérité) = {@link VmPowerPolicy}.
 *
 * <p>Ce test ÉCHOUE (il ne s'ignore jamais) si le YAML est introuvable ou s'il diverge du code : une table qui
 * dérive entre le dépôt, le backend et le watchdog est exactement ce que la politique veut empêcher.
 * La troisième branche (watchdog hôte) est vérifiée côté Python par {@code test_watchdog_vm_policy.py}.
 */
class VmPowerPolicyParityTest {

    static Path yamlPath() {
        Path dir = Paths.get("").toAbsolutePath();
        for (int i = 0; i < 8 && dir != null; i++, dir = dir.getParent()) {
            Path candidate = dir.resolve("infra").resolve("helios-vm-policy.yaml");
            if (Files.isRegularFile(candidate)) {
                return candidate;
            }
        }
        fail("infra/helios-vm-policy.yaml introuvable en remontant depuis " + Paths.get("").toAbsolutePath());
        return null;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> loadYaml() throws IOException {
        try (Reader reader = Files.newBufferedReader(yamlPath(), StandardCharsets.UTF_8)) {
            Object root = new Yaml(new SafeConstructor(new LoaderOptions())).load(reader);
            assertNotNull(root, "YAML vide");
            return (Map<String, Object>) root;
        }
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> yamlVms() throws IOException {
        return (List<Map<String, Object>>) loadYaml().get("vms");
    }

    @Test
    void le_yaml_et_le_java_ont_les_memes_cibles_dans_le_meme_ordre() throws IOException {
        List<Map<String, Object>> vms = yamlVms();
        List<Entry> java = VmPowerPolicy.entries();
        assertEquals(java.size(), vms.size(), "nombre de cibles YAML / Java");
        for (int i = 0; i < java.size(); i++) {
            Map<String, Object> y = vms.get(i);
            Entry e = java.get(i);
            String nom = String.valueOf(y.get("name"));
            assertEquals(e.name(), nom, "nom #" + i);
            assertEquals(e.ip(), String.valueOf(y.get("ip")), "ip de " + nom);
            assertEquals(e.mode().name(), String.valueOf(y.get("mode")), "mode de " + nom);
            assertEquals(e.vmClass().name(), String.valueOf(y.get("vm_class")), "classe de " + nom);
            assertEquals(e.incidentNamespace(), String.valueOf(y.get("incident_namespace")), "namespace de " + nom);
            assertEquals(e.incidentApp(), String.valueOf(y.get("incident_app")), "app de " + nom);
            assertEquals(e.incidentType(), String.valueOf(y.get("incident_type")), "type de " + nom);
            assertEquals(e.route().name(), String.valueOf(y.get("restart_route")), "route de " + nom);
            assertEquals(e.notifications().name(), String.valueOf(y.get("notifications")), "notifications de " + nom);
        }
    }

    @Test
    void les_quatre_cibles_automatiques_et_160_en_detection_seule() {
        assertEquals(java.util.Set.of("k8s-lb2", "k8s-worker1", "k8s-worker2", "k8s-master"),
                VmPowerPolicy.autoTargets(), "exactement .158 .155 .162 (6 nov. 2026) et .153 (consigne du 2026-11-17)");
        assertEquals(VmPowerPolicy.Mode.DETECT_ONLY, VmPowerPolicy.modeOf("k8s-monitoring"), ".160 : détection seule");
        assertEquals(VmPowerPolicy.Route.NONE, VmPowerPolicy.routeOf("k8s-monitoring"));
        assertEquals(VmPowerPolicy.Mode.AUTO, VmPowerPolicy.modeOf("k8s-master"));
        assertEquals(VmPowerPolicy.Route.CAMUNDA, VmPowerPolicy.routeOf("k8s-master"));
    }

    @Test
    void le_yaml_et_le_java_ont_les_memes_seuils() throws IOException {
        @SuppressWarnings("unchecked")
        Map<String, Object> seuils = (Map<String, Object>) loadYaml().get("thresholds");
        assertNotNull(seuils, "section thresholds absente");
        assertEquals(VmPowerPolicy.EVIDENCE_MAX_AGE_MS, ((Number) seuils.get("evidence_max_age_ms")).longValue());
        assertEquals(VmPowerPolicy.CONFIRMATION_MIN_OBSERVATIONS,
                ((Number) seuils.get("confirmation_min_observations")).intValue());
        assertEquals(VmPowerPolicy.CONFIRMATION_MIN_SPAN_MS,
                ((Number) seuils.get("confirmation_min_span_ms")).longValue());
        assertEquals(VmPowerPolicy.RESTART_COOLDOWN.toMinutes(),
                ((Number) seuils.get("restart_cooldown_minutes")).longValue());
    }

    @Test
    void le_yaml_est_ferme_par_defaut_et_versionne() throws IOException {
        Map<String, Object> racine = loadYaml();
        assertEquals("DENY", String.valueOf(racine.get("default_mode")));
        assertEquals(1, ((Number) racine.get("version")).intValue());
    }

    @Test
    void le_yaml_ne_declare_que_les_cles_connues() throws IOException {
        // Une clé inconnue (faute de frappe, champ oublié côté Java) ne doit pas passer inaperçue.
        List<String> attendues = List.of("name", "ip", "mode", "vm_class",
                "incident_namespace", "incident_app", "incident_type", "restart_route", "notifications");
        for (Map<String, Object> y : yamlVms()) {
            assertEquals(attendues.stream().sorted().toList(), y.keySet().stream().sorted().toList(),
                    "clés de " + y.get("name"));
        }
        assertEquals(List.of("adopted", "default_mode", "thresholds", "version", "vms"),
                loadYaml().keySet().stream().sorted().toList());
    }
}
