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
 * V32 (origine incident des tâches d'équipe) appliquée sur le schéma de V25 contenant déjà des tâches.
 * Limite assumée : H2 n'est pas PostgreSQL ; l'exécution sur PostgreSQL est faite à part.
 */
class TeamTaskSourceMigrationTest {

    private static Connection database(String name) throws SQLException {
        DriverManagerDataSource ds = new DriverManagerDataSource(
                "jdbc:h2:mem:" + name + ";MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1", "sa", "");
        Connection c = ds.getConnection();
        try (Statement st = c.createStatement()) {
            st.execute("CREATE TABLE app_user (id BIGSERIAL PRIMARY KEY)");
        }
        ScriptUtils.executeSqlScript(c, new ClassPathResource("db/migration/V25__teams_and_tasks.sql"));
        return c;
    }

    private static void migrate(Connection c) {
        ScriptUtils.executeSqlScript(c, new ClassPathResource("db/migration/V32__team_task_source_incident.sql"));
    }

    private static long scalar(Connection c, String sql) throws SQLException {
        try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery(sql)) {
            rs.next();
            return rs.getLong(1);
        }
    }

    @Test
    void existingTasksAreKeptAsManualTasks() throws SQLException {
        try (Connection c = database("v32_existing"); Statement st = c.createStatement()) {
            st.execute("INSERT INTO team_task (title) VALUES ('ancienne 1')");
            st.execute("INSERT INTO team_task (title) VALUES ('ancienne 2')");
            migrate(c);

            assertThat(scalar(c, "SELECT COUNT(*) FROM team_task")).isEqualTo(2);
            assertThat(scalar(c, "SELECT COUNT(*) FROM team_task WHERE source_incident_id IS NULL AND source_event IS NULL")).isEqualTo(2);
        }
    }

    @Test
    void manyManualTasksCanCoexistBecauseNullsAreDistinct() throws SQLException {
        try (Connection c = database("v32_nulls"); Statement st = c.createStatement()) {
            migrate(c);
            st.execute("INSERT INTO team_task (title) VALUES ('a')");
            st.execute("INSERT INTO team_task (title) VALUES ('b')");

            assertThat(scalar(c, "SELECT COUNT(*) FROM team_task")).isEqualTo(2);
        }
    }

    @Test
    void anIncidentCanProduceOneTaskPerStageButNeverTwoOfTheSame() throws SQLException {
        try (Connection c = database("v32_unique"); Statement st = c.createStatement()) {
            migrate(c);
            st.execute("INSERT INTO team_task (title, source_incident_id, source_event) VALUES ('d', 7, 'DECISION')");
            st.execute("INSERT INTO team_task (title, source_incident_id, source_event) VALUES ('f', 7, 'FOLLOW_UP')");
            st.execute("INSERT INTO team_task (title, source_incident_id, source_event) VALUES ('r', 7, 'RESUME')");
            st.execute("INSERT INTO team_task (title, source_incident_id, source_event) VALUES ('autre', 8, 'DECISION')");

            assertThatThrownBy(() -> st.execute("INSERT INTO team_task (title, source_incident_id, source_event) VALUES ('doublon', 7, 'DECISION')"))
                    .isInstanceOf(SQLException.class);
        }
    }

    @Test
    void rejectsAnIncompleteOrUnknownSource() throws SQLException {
        try (Connection c = database("v32_check"); Statement st = c.createStatement()) {
            migrate(c);

            assertThatThrownBy(() -> st.execute("INSERT INTO team_task (title, source_incident_id) VALUES ('sans etape', 1)"))
                    .isInstanceOf(SQLException.class);
            assertThatThrownBy(() -> st.execute("INSERT INTO team_task (title, source_event) VALUES ('sans incident', 'DECISION')"))
                    .isInstanceOf(SQLException.class);
            assertThatThrownBy(() -> st.execute("INSERT INTO team_task (title, source_incident_id, source_event) VALUES ('x', 1, 'AUTRE')"))
                    .isInstanceOf(SQLException.class);
        }
    }
}
