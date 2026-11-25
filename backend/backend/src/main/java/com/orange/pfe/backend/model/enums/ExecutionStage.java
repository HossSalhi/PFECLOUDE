package com.orange.pfe.backend.model.enums;

/**
 * Étapes d'exécution d'une remédiation, enregistrées dans la chronologie de la carte.
 * LOT HELIOS-INCIDENT-CARD-EVENT-INTEGRATION-01. Un HTTP 200 / une soumission ne valent PAS « réussi » :
 * le succès est porté par {@code COMPLETED}+success explicite, et la résolution par {@code VERIFIED}.
 */
public enum ExecutionStage {
    SELECTED,   // procédure déterministe sélectionnée
    REQUESTED,  // opération demandée à l'exécuteur/Bridge
    ACCEPTED,   // opération acceptée (validée) par l'exécuteur
    STARTED,    // opération démarrée
    COMPLETED,  // opération terminée (voir le drapeau success)
    FAILED,     // opération échouée
    VERIFIED    // résultat vérifié (voir IncidentVerification)
}
