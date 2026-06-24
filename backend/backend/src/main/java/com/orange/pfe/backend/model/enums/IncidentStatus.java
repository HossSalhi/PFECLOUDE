package com.orange.pfe.backend.model.enums;

public enum IncidentStatus {
    NEW,
    OPEN,
    IN_PROGRESS,
    RESOLVED,
    CLOSED,
    ARCHIVED,
    WAITING_FOR_APPROVAL,
    RESOLUTION_FAILED // remédiation tentée mais impossible à résoudre automatiquement

}