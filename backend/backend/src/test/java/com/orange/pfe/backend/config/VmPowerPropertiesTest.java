package com.orange.pfe.backend.config;

import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.boot.context.properties.source.MapConfigurationPropertySource;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * VMDOWN-POLICY-20261105 — les réglages de la politique sont FERMÉS par défaut et se lisent correctement depuis les
 * variables d'environnement : une mauvaise liaison ne doit jamais activer silencieusement le mode réel.
 */
class VmPowerPropertiesTest {

    private static VmPowerProperties bind(Map<String, String> source) {
        return new Binder(new MapConfigurationPropertySource(source))
                .bind("helios.vmpower", VmPowerProperties.class)
                .orElseGet(VmPowerProperties::new);
    }

    @Test
    void defauts_sans_aucune_variable_tout_est_ferme() {
        VmPowerProperties p = new VmPowerProperties();
        assertFalse(p.isEnabled(), "aucune surveillance de puissance par défaut");
        assertTrue(p.isDryRun(), "mode ombre par défaut");
        assertTrue(p.getActiveVms().isEmpty(), "aucune VM activée par défaut");
        assertEquals(35_000, p.getWatchdogRestartTimeoutMs(), "doit dépasser l'attente de réponse du watchdog (25 s)");
        assertTrue(p.getWatchdogRestartTimeoutMs() > 25_000);
    }

    @Test
    void liaison_depuis_les_variables_telles_que_posees_par_application_properties() {
        // application.properties : helios.vmpower.* = ${HELIOS_VMPOWER_*:défaut} ; ici les valeurs résolues
        VmPowerProperties off = bind(Map.of(
                "helios.vmpower.enabled", "false",
                "helios.vmpower.dry-run", "true",
                "helios.vmpower.active-vms", ""));
        assertFalse(off.isEnabled());
        assertTrue(off.isDryRun());
        assertTrue(off.getActiveVms().isEmpty(), "chaîne vide => aucune VM activée");

        VmPowerProperties on = bind(Map.of(
                "helios.vmpower.enabled", "true",
                "helios.vmpower.dry-run", "false",
                "helios.vmpower.active-vms", "k8s-worker1, k8s-lb2"));
        assertTrue(on.isEnabled());
        assertFalse(on.isDryRun());
        assertEquals(Set.of("k8s-worker1", "k8s-lb2"), on.getActiveVms());
    }

    @Test
    void valeur_absente_ou_invalide_ne_bascule_jamais_en_mode_reel() {
        VmPowerProperties onlyEnabled = bind(Map.of("helios.vmpower.enabled", "true"));
        assertTrue(onlyEnabled.isEnabled());
        assertTrue(onlyEnabled.isDryRun(), "enabled=true seul reste en mode ombre");
        assertTrue(onlyEnabled.getActiveVms().isEmpty(), "et n'active aucune VM");
    }

    @Test
    void application_properties_porte_les_defauts_surs() throws IOException {
        String props = Files.readString(Path.of("src/main/resources/application.properties"), StandardCharsets.UTF_8);
        assertTrue(props.contains("helios.vmpower.enabled=${HELIOS_VMPOWER_ENABLED:false}"));
        assertTrue(props.contains("helios.vmpower.dry-run=${HELIOS_VMPOWER_DRYRUN:true}"));
        assertTrue(props.contains("helios.vmpower.active-vms=${HELIOS_VMPOWER_ACTIVE_VMS:}"));
    }
}
