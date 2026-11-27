package com.orange.pfe.backend.model.report;

/** Types de rapports, tous calculés à partir de données réellement présentes dans la base HELIOS. */
public enum ReportType {
    USER_ACTIVITY("Activité des utilisateurs",
            "Connexions, actions et répartition de l'activité par utilisateur (journal d'audit)."),
    MAINTENANCE("Opérations de maintenance",
            "Maintenances de la période : statuts, ressources concernées, durées planifiées et réelles."),
    TEAMS_TASKS("Équipes et tâches",
            "Tâches créées, terminées, ouvertes et en retard, par équipe et par membre."),
    PLATFORM_ACTIVITY("Activités de la plateforme",
            "Activité d'audit par module et par jour, incidents créés et actions de remédiation exécutées."),
    AUTO_REMEDIATION_CYCLE("Cycle d'auto-remédiation",
            "Cycle complet par incident : détection, décision, action, vérification, résolution ; durée de chaque phase, taux de résolution automatique, cycles bloqués."),
    PERFORMANCE("Performance sur une période",
            "Incidents créés/résolus, temps de résolution, taux de réussite des actions de remédiation.");

    private final String label;
    private final String description;

    ReportType(String label, String description) {
        this.label = label;
        this.description = description;
    }

    public String label() { return label; }
    public String description() { return description; }
}
