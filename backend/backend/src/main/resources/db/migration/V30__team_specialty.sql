-- V30 : spécialité des équipes (une seule valeur, optionnelle).
-- Additive : une colonne nullable, une contrainte de valeurs et un index. Les équipes existantes gardent NULL
-- (équipe non spécialisée : jamais routée automatiquement). Aucun DROP, aucune réécriture de données.
-- Valeurs = enum Java ResourceScope.

ALTER TABLE team ADD COLUMN specialty VARCHAR(16);

ALTER TABLE team ADD CONSTRAINT ck_team_specialty
    CHECK (specialty IS NULL OR specialty IN ('VM', 'CLUSTER', 'APPLICATION', 'SERVICE'));

CREATE INDEX idx_team_specialty ON team(specialty, active);
