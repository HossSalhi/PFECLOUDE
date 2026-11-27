package com.orange.pfe.backend.db;

import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ScriptUtils;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * V31 (équipe sur la maintenance + statut SUSPENDED) appliquée sur les schémas de V25 et V26 contenant déjà des
 * maintenances. Limite assumée : H2 n'est pas PostgreSQL ; l'exécution sur PostgreSQL est faite à part.
 */
class MaintenanceSuspensionMigrationTest {

    private static Connection database(String name) throws SQLException {
        DriverManagerDataSource ds = new DriverManagerDataSource(
                "jdbc:h2:mem:" + name + ";MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1", "sa", "");
        Connection c = ds.getConnection();
        try (Statement st = c.createStatement()) {
            st.execute("CREATE TABLE app_user (id BIGSERIAL PRIMARY KEY)");
        }
        for (String m : new String[]{"V25__teams_and_tasks.sql", "V26__maintenance.sql"}) {
            ScriptUtils.executeSqlScript(c, new ClassPathResource("db/migration/" + m));
        }
        return c;
    }

    private static void migrate(Connection c) {
        ScriptUtils.executeSqlScript(c, new ClassPathResource("db/migration/V31__maintenance_team_and_suspension.sql"));
    }

    private static long scalar(Connection c, String sql) throws SQLException {
        try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery(sql)) {
            rs.next();
            return rs.getLong(1);
        }
    }

    private static final String INSERT = "INSERT INTO maintenance (title, resource_type, resource_name, start_at, end_at, status%s) "
            + "VALUES ('%s', 'VM', 'k8s-worker1', TIMESTAMP '2026-12-01 22:00:00', TIMESTAMP '2026-12-02 02:00:00', '%s'%s)";

    @Test
    void existingMaintenancesOfEveryStatusAreKeptUntouchedWithoutTeam() throws SQLException {
        try (Connection c = database("v31_existing"); Statement st = c.createStatement()) {
            for (String s : new String[]{"PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"}) {
                st.execute(String.format(INSERT, "", "ancienne-" + s, s, ""));
            }
            migrate(c);

            assertThat(scalar(c, "SELECT COUNT(*) FROM maintenance")).isEqualTo(4);
            assertThat(scalar(c, "SELECT COUNT(*) FROM maintenance WHERE team_id IS NULL AND suspended_at IS NULL "
                    + "AND suspend_reason IS NULL AND suspend_incident_id IS NULL")).isEqualTo(4);
            assertThat(scalar(c, "SELECT COUNT(*) FROM maintenance WHERE title = 'ancienne-IN_PROGRESS' AND status = 'IN_PROGRESS'"))
                    .isEqualTo(1);
        }
    }

    @Test
    void acceptsTheSuspendedStatusWithItsSuspensionDetails() throws SQLException {
        try (Connection c = database("v31_suspended"); Statement st = c.createStatement()) {
            migrate(c);
            st.execute(String.format(INSERT, ", suspended_at, suspend_reason, suspend_incident_id", "suspendue", "SUSPENDED",
                    ", TIMESTAMP '2026-12-02 00:00:00', 'Incident CPU', 42"));

            assertThat(scalar(c, "SELECT suspend_incident_id FROM maintenance WHERE status = 'SUSPENDED'")).isEqualTo(42);
        }
    }

    @Test
    void rejectsSuspensionDetailsOnAMaintenanceThatIsNotSuspended() throws SQLException {
        try (Connection c = database("v31_invariant"); Statement st = c.createStatement()) {
            migrate(c);

            assertThatThrownBy(() -> st.execute(String.format(INSERT, ", suspend_incident_id", "incoherente", "IN_PROGRESS", ", 7")))
                    .isInstanceOf(SQLException.class);
        }
    }

    @Test
    void stillRejectsAnUnknownStatus() throws SQLException {
        try (Connection c = database("v31_status"); Statement st = c.createStatement()) {
            migrate(c);

            assertThatThrownBy(() -> st.execute(String.format(INSERT, "", "inconnue", "PAUSED", "")))
                    .isInstanceOf(SQLException.class);
        }
    }

    @Test
    void linksAMaintenanceToATeamAndDetachesItWhenTheTeamIsDeleted() throws SQLException {
        try (Connection c = database("v31_team"); Statement st = c.createStatement()) {
            migrate(c);
            st.execute("INSERT INTO team (name, name_key) VALUES ('Infra', 'infra')");
            st.execute(String.format(INSERT, ", team_id", "avec equipe", "PLANNED", ", (SELECT id FROM team WHERE name_key = 'infra')"));
            assertThat(scalar(c, "SELECT COUNT(*) FROM maintenance WHERE team_id IS NOT NULL")).isEqualTo(1);

            st.execute("DELETE FROM team WHERE name_key = 'infra'");

            assertThat(scalar(c, "SELECT COUNT(*) FROM maintenance")).isEqualTo(1);
            assertThat(scalar(c, "SELECT COUNT(*) FROM maintenance WHERE team_id IS NULL")).isEqualTo(1);
        }
    }
}
