package com.orange.pfe.backend.service.remediation;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Locale;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * DISK-SAFETY — verrouille l'action {@code HOST_RECLAIM_DISK}.
 *
 * L'action host DISK doit rester STRICTEMENT bornée : vacuum du journal systemd avec rétention
 * (+ rapports df pour mesure avant/après). Elle ne doit JAMAIS :
 *   - lancer le garbage collector d'images ({@code crictl rmi}) — le cluster est air-gap, une image
 *     supprimée est irrécupérable ;
 *   - supprimer des fichiers de façon générale ({@code rm -rf}, {@code find ... -delete}…) ;
 *   - toucher aux données applicatives / PVC / montages persistants.
 *
 * Ce test garde contre toute régression qui remplacerait la commande bornée par une commande
 * destructrice. Aucun appel réseau / Bridge / hyperviseur n'est effectué.
 */
class HostDiskReclaimCommandSafetyTest {

    private static final String CMD = KubernetesRemediationExecutor.HOST_DISK_RECLAIM_COMMAND;

    @Test
    void reclaimActionIsBoundedJournalVacuumWithRetention() {
        assertTrue(CMD.contains("journalctl --vacuum-size=200M"),
                "l'action DISK doit être un vacuum journald borné avec rétention explicite : " + CMD);
        // Mesure avant/après côté hôte (df espace + inodes) sans supprimer de données.
        assertTrue(CMD.contains("df -hT /") && CMD.contains("df -ih /"),
                "l'action DISK doit relever l'espace ET les inodes (mesure) : " + CMD);
    }

    @Test
    void reclaimActionForbidsImageGcGeneralDeletionAndDataAccess() {
        String c = CMD.toLowerCase(Locale.ROOT);
        for (String forbidden : List.of(
                "crictl", "rmi", "docker", "ctr ",      // garbage collector d'images (air-gap)
                "rm -rf", "rm -r ", "rm -f", "unlink",   // suppression de fichiers
                "find / ", "-delete", "mkfs", "dd ",     // opérations destructrices
                "truncate", "> /", ":(){",               // vidage / fork bomb
                "/var/lib/postgresql", "/var/lib/etcd",  // données applicatives / état cluster
                "/var/lib/containerd", "pvc", "/mnt")) {
            assertFalse(c.contains(forbidden),
                    "l'action DISK ne doit JAMAIS contenir « " + forbidden + " » : " + CMD);
        }
    }
}
