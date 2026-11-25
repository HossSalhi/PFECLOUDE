package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.audit.AuditLogEntry;
import org.hibernate.annotations.Immutable;
import org.junit.jupiter.api.Test;
import org.springframework.data.repository.Repository;

import java.lang.reflect.Method;
import java.util.Arrays;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Le journal d'audit est en AJOUT SEUL : le contrat du repository et de l'entité interdit, par construction,
 * toute modification ou suppression applicative.
 */
class AuditLogRepositoryContractTest {

    @Test
    void repositoryExposesOnlyAppendAndRead() {
        assertThat(AuditLogRepository.class.getInterfaces()).containsExactly(Repository.class);
        assertThat(Arrays.stream(AuditLogRepository.class.getDeclaredMethods()).map(Method::getName))
                .containsExactlyInAnyOrder("save", "findById", "count");
    }

    @Test
    void searchRepositoryHasNoWriteMethod() {
        assertThat(Arrays.stream(AuditLogSearchRepository.class.getDeclaredMethods())
                .filter(m -> java.lang.reflect.Modifier.isPublic(m.getModifiers()))
                .map(Method::getName))
                .allMatch(n -> n.startsWith("search"));
    }

    @Test
    void entityIsImmutableWithoutSetters() {
        assertThat(AuditLogEntry.class.isAnnotationPresent(Immutable.class)).isTrue();
        assertThat(Arrays.stream(AuditLogEntry.class.getMethods()).map(Method::getName))
                .noneMatch(n -> n.startsWith("set"));
    }
}
