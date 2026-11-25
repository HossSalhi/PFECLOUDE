package com.orange.pfe.backend.model.report;

/** Résultat de la génération d'un rapport. Un échec est conservé (avec son message) au lieu d'être perdu. */
public enum ReportStatus {
    COMPLETED,
    FAILED
}
