package com.orange.pfe.backend.service.supervision;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class LogMeaningCatalogTest {

    private static String cat(String line) {
        return LogMeaningCatalog.classify(line).category();
    }

    @Test
    void knownMessagesAreClassifiedWithAMeaningAndAnAdvice() {
        assertThat(cat("dial tcp 10.0.0.1:6443: i/o timeout")).isEqualTo("RESEAU");
        assertThat(cat("dial tcp 10.0.0.1:6443: connect: connection refused")).isEqualTo("RESEAU");
        assertThat(cat("Out of memory: Killed process 4242 (java)")).isEqualTo("MEMOIRE");
        assertThat(cat("write /var/lib/x: no space left on device")).isEqualTo("DISQUE");
        assertThat(cat("etcdserver: request timed out, leader changed")).isEqualTo("ETCD");
        assertThat(cat("Back-off pulling image: ErrImagePull")).isEqualTo("IMAGE");
        assertThat(cat("Back-off restarting failed container (CrashLoopBackOff)")).isEqualTo("CRASH");
        assertThat(cat("Readiness probe failed: HTTP probe failed with statuscode: 503")).isEqualTo("SONDE");
        assertThat(cat("x509: certificate has expired or is not yet valid")).isEqualTo("CERTIFICAT");
        assertThat(cat("lookup foo: no such host")).isEqualTo("DNS");
        assertThat(cat("context deadline exceeded")).isEqualTo("DELAI");
        assertThat(cat("sshd[10]: Failed password for invalid user admin")).isEqualTo("ACCES");
        var m = LogMeaningCatalog.classify("connection refused");
        assertThat(m.meaning()).isNotBlank();
        assertThat(m.advice()).isNotBlank();
    }

    @Test
    void anUnknownOrEmptyMessageFallsBackToOther() {
        assertThat(cat("quelque chose d'inédit")).isEqualTo("AUTRE");
        assertThat(cat("")).isEqualTo("AUTRE");
        assertThat(cat(null)).isEqualTo("AUTRE");
    }

    @Test
    void theCatalogueListsEveryRuleAndEndsWithOther() {
        var all = LogMeaningCatalog.all();

        assertThat(all.size()).isGreaterThan(10);
        assertThat(all.get(all.size() - 1).category()).isEqualTo("AUTRE");
        assertThat(all).allSatisfy(x -> {
            assertThat(x.label()).isNotBlank();
            assertThat(x.meaning()).isNotBlank();
        });
    }
}
