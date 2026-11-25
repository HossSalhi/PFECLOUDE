package com.orange.pfe.backend.model.audit;

/** Résultat d'une opération tracée : réussie, en échec technique/métier, ou refusée (droits insuffisants). */
public enum AuditResult {
    SUCCESS,
    FAILURE,
    DENIED
}
