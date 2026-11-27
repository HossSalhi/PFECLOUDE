package com.orange.pfe.backend.db;

import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ScriptUtils;

import java.sql.Connection;
import java.sql.SQLException;
import java.sql.Statement;
import java.sql.ResultSet;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * V30 (spécialité des équipes) appliquée sur le schéma de V25 avec des équipes déjà présentes.
 * Limite assumée : H2 n'est pas PostgreSQL ; l'exécution réelle de Flyway sur PostgreSQL reste à faire.
 */
class TeamSpecialtyMigrationTest {

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

    private static long scalar(Connection c, String sql) throws SQLException {
        try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery(sql)) {
            rs.next();
            return rs.getLong(1);
        }
    }

    @Test
    void existingTeamsKeepANullSpecialtyAfterTheMigration() throws SQLException {
        try (Connection c = database("v30_existing"); Statement st = c.createStatement()) {
            st.execute("INSERT INTO team (name, name_key) VALUES ('Ancienne', 'ancienne')");
            ScriptUtils.executeSqlScript(c, new ClassPathResource("db/migration/V30__team_specialty.sql"));

            assertThat(scalar(c, "SELECT COUNT(*) FROM team WHERE specialty IS NULL")).isEqualTo(1);
            assertThat(scalar(c, "SELECT COUNT(*) FROM team")).isEqualTo(1);
        }
    }

    @Test
    void acceptsTheFourScopesAndNull() throws SQLException {
        try (Connection c = database("v30_values"); Statement st = c.createStatement()) {
            ScriptUtils.executeSqlScript(c, new ClassPathResource("db/migration/V30__team_specialty.sql"));
            st.execute("INSERT INTO team (name, name_key, specialty) VALUES ('a', 'a', 'VM')");
            st.execute("INSERT INTO team (name, name_key, specialty) VALUES ('b', 'b', 'CLUSTER')");
            st.execute("INSERT INTO team (name, name_key, specialty) VALUES ('c', 'c', 'APPLICATION')");
            st.execute("INSERT INTO team (name, name_key, specialty) VALUES ('d', 'd', 'SERVICE')");
            st.execute("INSERT INTO team (name, name_key) VALUES ('e', 'e')");

            assertThat(scalar(c, "SELECT COUNT(*) FROM team")).isEqualTo(5);
        }
    }

    @Test
    void rejectsAnUnknownSpecialty() throws SQLException {
        try (Connection c = database("v30_invalid"); Statement st = c.createStatement()) {
            ScriptUtils.executeSqlScript(c, new ClassPathResource("db/migration/V30__team_specialty.sql"));

            assertThatThrownBy(() -> st.execute("INSERT INTO team (name, name_key, specialty) VALUES ('x', 'x', 'NODE')"))
                    .isInstanceOf(SQLException.class);
        }
    }
}
