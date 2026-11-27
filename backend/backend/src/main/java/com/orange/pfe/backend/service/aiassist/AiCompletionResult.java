package com.orange.pfe.backend.service.aiassist;

import java.time.Instant;

/**
 * Résultat typé d'un appel IA : un texte généré, ou une indisponibilité expliquée. Jamais d'exception vers l'appelant :
 * l'IA n'est qu'un habillage, une panne ne doit bloquer aucune page.
 */
public sealed interface AiCompletionResult {

    /** Raison d'indisponibilité, affichable telle quelle (jamais de secret, jamais de détail interne). */
    enum Reason {
        DISABLED("L'assistance IA est désactivée."),
        NOT_CONFIGURED("L'assistance IA n'est pas configurée (clé API manquante ou refusée)."),
        QUOTA("Le quota du fournisseur IA est dépassé ; réessayez plus tard."),
        TIMEOUT("Le fournisseur IA n'a pas répondu à temps."),
        UNREACHABLE("Le fournisseur IA est injoignable."),
        REJECTED("Le fournisseur IA a refusé la requête."),
        INVALID_RESPONSE("La réponse du fournisseur IA est inexploitable.");

        private final String message;

        Reason(String message) { this.message = message; }

        public String message() { return message; }
    }

    record Success(String text, String model, Instant generatedAt) implements AiCompletionResult {}

    record Unavailable(Reason reason, Instant at) implements AiCompletionResult {
        public String message() { return reason.message(); }
    }

    default boolean available() { return this instanceof Success; }
}
