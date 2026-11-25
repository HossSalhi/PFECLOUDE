package com.orange.pfe.backend.service;

/**
 * Resultat explicite du demarrage d'un workflow Camunda.
 */
public enum CamundaLaunchOutcome {
    NOT_STARTED,
    LAUNCH_FAILED,
    CAMUNDA_STARTED,
    PROCESS_ALREADY_ATTACHED,
    DIRECT_FALLBACK_EXECUTED,
    DIRECT_FALLBACK_FAILED,
    MANUAL_FOLLOW_UP_REQUIRED
}