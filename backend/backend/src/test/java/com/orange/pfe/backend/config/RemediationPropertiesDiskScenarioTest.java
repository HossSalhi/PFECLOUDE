package com.orange.pfe.backend.config;

import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.boot.context.properties.source.MapConfigurationPropertySource;
import org.springframework.util.PropertyPlaceholderHelper;

import java.io.IOException;
import java.io.InputStream;
import java.net.URISyntaxException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import java.util.Properties;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * DISK-E2E-158-02 : le drapeau dedie du scenario est DESACTIVE par defaut, se lie depuis HOST_DISK_E2E_158_ENABLED, et reste
 * strictement independant de l'ancien drapeau global.
 */
class RemediationPropertiesDiskScenarioTest {

    private static final String PROPRIETE = "remediation.host-disk-e2e158-enabled";
    private static final String ANCIENNE = "remediation.host-disk-remediation-enabled";

    /**
     * Lit l'application.properties de PRODUCTION (src/main/resources), pas celui de src/test/resources. Sous Maven, ce
     * dernier (configuration H2 des tests) est devant dans le classpath et masque le premier : getResourceAsStream(
     * "/application.properties") lirait donc la configuration de test, qui ne contient pas ces cles. On lit le fichier a
     * cote des classes principales (celles de RemediationProperties), quel que soit l'ordre du classpath.
     */
    private static Properties fichier() throws IOException {
        var source = RemediationProperties.class.getProtectionDomain().getCodeSource();
        assertNotNull(source, "emplacement des classes principales introuvable");
        Path principal;
        try {
            principal = Path.of(source.getLocation().toURI()).resolve("application.properties");
        } catch (URISyntaxException e) {
            throw new IOException("emplacement des classes principales illisible : " + source.getLocation(), e);
        }
        assertTrue(Files.isRegularFile(principal), "application.properties principal introuvable : " + principal);

        Properties properties = new Properties();
        try (InputStream in = Files.newInputStream(principal)) {
            properties.load(in);
        }
        return properties;
    }

    private static String resolu(String valeur, Map<String, String> environnement) {
        return new PropertyPlaceholderHelper("${", "}", ":", (Character) null, true)
                .replacePlaceholders(valeur, environnement::get);
    }

    @Test
    void lesDeuxDrapeauxSontDesactivesParDefaut() {
        RemediationProperties properties = new RemediationProperties();

        assertFalse(properties.isHostDiskE2e158Enabled());
        assertFalse(properties.isHostDiskRemediationEnabled());
    }

    @Test
    void leDrapeauDedieSeLieIndependammentDeLAncien() {
        RemediationProperties seulDedie = new Binder(new MapConfigurationPropertySource(Map.of(PROPRIETE, "true")))
                .bind("remediation", RemediationProperties.class).get();
        RemediationProperties seulAncien = new Binder(new MapConfigurationPropertySource(Map.of(ANCIENNE, "true")))
                .bind("remediation", RemediationProperties.class).get();

        assertTrue(seulDedie.isHostDiskE2e158Enabled());
        assertFalse(seulDedie.isHostDiskRemediationEnabled(), "le drapeau dedie n'active pas l'ancien");
        assertTrue(seulAncien.isHostDiskRemediationEnabled());
        assertFalse(seulAncien.isHostDiskE2e158Enabled(), "l'ancien drapeau n'active pas le scenario");
    }

    @Test
    void lApplicationPropertiesLieLaVariableHostDiskE2e158EnabledAvecUnDefautFalse() throws IOException {
        Properties properties = fichier();

        assertEquals("${HOST_DISK_E2E_158_ENABLED:false}", properties.getProperty(PROPRIETE));
        assertEquals("${HOST_DISK_REMEDIATION_ENABLED:false}", properties.getProperty(ANCIENNE), "l'ancien drapeau est inchange");

        Map<String, String> aucuneVariable = new HashMap<>();
        assertEquals("false", resolu(properties.getProperty(PROPRIETE), aucuneVariable));
        assertEquals("true", resolu(properties.getProperty(PROPRIETE), Map.of("HOST_DISK_E2E_158_ENABLED", "true")));
        assertEquals("false", resolu(properties.getProperty(PROPRIETE), Map.of("HOST_DISK_REMEDIATION_ENABLED", "true")),
                "l'ancienne variable ne peut pas activer le scenario");
    }
}
