package com.orange.pfe.backend.service.aiassist;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Restauration des alias dans le texte du modèle : elle doit survivre aux habitudes typographiques de gpt-oss-20b (espace fine
 * insécable entre le mot et la lettre, casse, pluriel) sans jamais toucher un mot ordinaire.
 */
class AiNamesRestoreTest {

    private static AiNames machines() {
        AiNames n = new AiNames();
        n.alias("k8s-worker1", "Machine");
        n.alias("k8s-master", "Machine");
        n.alias("pfe/backend", "Application");
        return n;
    }

    @Test
    void anOrdinarySpaceStillWorks() {
        assertThat(machines().restore("Machine A et Machine B ; Application A")).isEqualTo("k8s-worker1 et k8s-master ; pfe/backend");
    }

    @Test
    void everyTypographicSpaceTheModelMayEmitIsAccepted() {
        AiNames n = machines();
        assertThat(n.restore("Machine A")).isEqualTo("k8s-worker1");        // espace fine insécable : le cas réellement observé
        assertThat(n.restore("Machine A")).isEqualTo("k8s-worker1");        // espace insécable
        assertThat(n.restore("Machine A")).isEqualTo("k8s-worker1");        // espace fine
        assertThat(n.restore("Machine A")).isEqualTo("k8s-worker1");        // espace tabulaire
        assertThat(n.restore("Application A redémarre")).isEqualTo("pfe/backend redémarre");
    }

    @Test
    void theWordMayBeLowerCaseOrPlural() {
        AiNames n = machines();
        assertThat(n.restore("la machine A est lente")).isEqualTo("la k8s-worker1 est lente");
        assertThat(n.restore("MACHINE B")).isEqualTo("k8s-master");
        assertThat(n.restore("Machines A")).isEqualTo("k8s-worker1");
    }

    @Test
    void nonBreakingHyphensAreNormalisedSoRealNamesStayReadable() {
        assertThat(machines().restore("Machine A (vm‑node‑exporters)")).isEqualTo("k8s-worker1 (vm-node-exporters)");
    }

    @Test
    void anOrdinaryWordIsNeverMistakenForAnAlias() {
        AiNames n = machines();
        assertThat(n.restore("la machine a plusieurs défauts")).isEqualTo("la machine a plusieurs défauts");      // « a » minuscule = verbe
        assertThat(n.restore("Machine AB inconnue")).isEqualTo("Machine AB inconnue");                          // alias inexistant
        assertThat(n.restore("Machine Alpha")).isEqualTo("Machine Alpha");                                      // la lettre doit être isolée
        assertThat(n.restore("Technicien A")).isEqualTo("Technicien A");                                        // catégorie sans alias créé
        assertThat(n.restore("Machine C")).isEqualTo("Machine C");                                              // lettre pas encore attribuée
    }

    @Test
    void peopleAreRestoredWithTheDefaultCategory() {
        AiNames n = new AiNames();
        n.alias("Alice Martin");
        n.alias("Bob Durand");

        assertThat(n.restore("Technicien A aide Technicien B ; Technicien A termine")).isEqualTo("Alice Martin aide Bob Durand ; Alice Martin termine");
    }

    @Test
    void longerAliasesAreReplacedBeforeShorterOnes() {
        AiNames n = new AiNames();
        for (int i = 0; i < 27; i++) {
            n.alias("p" + i);                                   // le 27e alias est « Technicien AA »
        }

        assertThat(n.restore("Technicien AA et Technicien A")).isEqualTo("p26 et p0");
    }

    @Test
    void aRealNameContainingRegexCharactersIsInsertedLiterally() {
        AiNames n = new AiNames();
        n.alias("équipe $1 (test) \\ x", "Machine");

        assertThat(n.restore("Machine A")).isEqualTo("équipe $1 (test) \\ x");
    }

    @Test
    void emptyAndNullTextsAreSafe() {
        AiNames n = machines();
        assertThat(n.restore(null)).isNull();
        assertThat(n.restore("")).isEmpty();
        assertThat(new AiNames().restore("Machine A")).isEqualTo("Machine A");
    }

    @Test
    void theSystemPromptAsksForExactAliasesAndForbidsGroupedNames() {
        assertThat(AiAssistGuard.SYSTEM).contains("recopie son nom exact").contains("espace ordinaire").contains("jamais « Machines A, B »")
                .contains("n'inventes aucune donnée");
    }
}
