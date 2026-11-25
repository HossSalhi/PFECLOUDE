package com.orange.pfe.backend.db;

import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.core.io.Resource;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ScriptUtils;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Vérifie les migrations V24 à V27 (audit, équipes/tâches, maintenance, rapports) :
 * numérotation Flyway cohérente, exécution sans erreur sur H2 en mode PostgreSQL, contraintes d'intégrité, et
 * — point important — la SUPPRESSION PHYSIQUE d'un utilisateur reste possible (ON DELETE SET NULL / CASCADE).
 *
 * <p>Limite assumée : H2 n'est pas PostgreSQL. Ce test attrape les erreurs de syntaxe et de logique relationnelle ;
 * il ne remplace pas une exécution réelle de Flyway sur la base PostgreSQL (CloudNativePG).</p>
 */
class OrgMigrationsSyntaxTest {

    private static final List<String> NEW_MIGRATIONS = List.of(
            "V24__audit_log.sql", "V25__teams_and_tasks.sql", "V26__maintenance.sql", "V27__reports.sql");

    private static Connection freshDatabase(String name) throws SQLException {
        DriverManagerDataSource ds = new DriverManagerDataSource(
                "jdbc:h2:mem:" + name + ";MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1", "sa", "");
        Connection c = ds.getConnection();
        try (Statement st = c.createStatement()) {
            st.execute("CREATE TABLE app_user (id BIGSERIAL PRIMARY KEY)");   // cible des clés étrangères (V8 en réel)
        }
        for (String migration : NEW_MIGRATIONS) {
            ScriptUtils.executeSqlScript(c, new ClassPathResource("db/migration/" + migration));
        }
        return c;
    }

    private static boolean tableExists(Connection c, String table) throws SQLException {
        try (ResultSet rs = c.getMetaData().getTables(null, null, table, null)) {
            return rs.next();
        }
    }

    private static long scalar(Connection c, String sql) throws SQLException {
        try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery(sql)) {
            rs.next();
            return rs.getLong(1);
        }
    }

    @Test
    void flywayVersionsAreUniqueAndContiguousUpToV27() throws Exception {
        Resource[] resources = new PathMatchingResourcePatternResolver().getResources("classpath*:db/migration/V*__*.sql");
        Pattern p = Pattern.compile("V(\\d+)__.+\\.sql");
        List<Integer> versions = new ArrayList<>();
        for (Resource r : resources) {
            Matcher m = p.matcher(String.valueOf(r.getFilename()));
            assertThat(m.matches()).as(r.getFilename()).isTrue();
            versions.add(Integer.parseInt(m.group(1)));
        }
        assertThat(versions).doesNotHaveDuplicates();
        assertThat(new TreeSet<>(versions)).contains(24, 25, 26, 27);
        int max = new TreeSet<>(versions).last();
        for (int v = 1; v <= max; v++) {
            assertThat(versions).as("version V" + v).contains(v);
        }
    }

    @Test
    void migrationsRunAndCreateEveryTable() throws Exception {
        try (Connection c = freshDatabase("mig1")) {
            for (String table : List.of("audit_log", "team", "team_member", "team_task", "maintenance",
                    "maintenance_history", "report_schedule", "report")) {
                assertThat(tableExists(c, table)).as(table).isTrue();
            }
        }
    }

    @Test
    void deletingAUserStillWorksAndDoesNotLoseTheBusinessRecords() throws Exception {
        try (Connection c = freshDatabase("mig2"); Statement st = c.createStatement()) {
            st.execute("INSERT INTO app_user (id) VALUES (1)");
            st.execute("INSERT INTO app_user (id) VALUES (2)");
            st.execute("INSERT INTO team (name, name_key, lead_id, created_by_id) VALUES ('T', 't', 1, 1)");
            st.execute("INSERT INTO team_member (team_id, user_id) VALUES (1, 1)");
            st.execute("INSERT INTO team_member (team_id, user_id) VALUES (1, 2)");
            st.execute("INSERT INTO team_task (title, team_id, assignee_id, created_by_id) VALUES ('x', 1, 1, 1)");
            st.execute("INSERT INTO maintenance (title, resource_type, resource_name, start_at, end_at, created_by_id, responsible_id) "
                    + "VALUES ('m', 'NODE', 'n1', TIMESTAMP '2026-12-01 10:00:00', TIMESTAMP '2026-12-01 12:00:00', 1, 1)");
            st.execute("INSERT INTO report_schedule (report_type, frequency, created_by_id) VALUES ('PERFORMANCE', 'DAILY', 1)");
            st.execute("INSERT INTO report (report_type, title, period_start, period_end, generated_by_id) "
                    + "VALUES ('PERFORMANCE', 'r', TIMESTAMP '2026-11-01 00:00:00', TIMESTAMP '2026-11-02 00:00:00', 1)");
            st.execute("INSERT INTO audit_log (module, action, actor_user_id, actor_username) VALUES ('AUTH', 'LOGIN', 1, 'u1')");

            st.execute("DELETE FROM app_user WHERE id = 1");   // suppression physique, comme UserService.delete

            assertThat(scalar(c, "SELECT COUNT(*) FROM team WHERE lead_id IS NULL AND created_by_id IS NULL")).isEqualTo(1);
            assertThat(scalar(c, "SELECT COUNT(*) FROM team_member WHERE user_id = 1")).isZero();     // CASCADE
            assertThat(scalar(c, "SELECT COUNT(*) FROM team_member WHERE user_id = 2")).isEqualTo(1);
            assertThat(scalar(c, "SELECT COUNT(*) FROM team_task WHERE assignee_id IS NULL AND created_by_id IS NULL")).isEqualTo(1);
            assertThat(scalar(c, "SELECT COUNT(*) FROM maintenance WHERE responsible_id IS NULL")).isEqualTo(1);
            assertThat(scalar(c, "SELECT COUNT(*) FROM report WHERE generated_by_id IS NULL")).isEqualTo(1);
            // la trace d'audit survit, avec le nom de l'acteur
            assertThat(scalar(c, "SELECT COUNT(*) FROM audit_log WHERE actor_username = 'u1'")).isEqualTo(1);
        }
    }

    @Test
    void integrityConstraintsRejectInconsistentRows() throws Exception {
        try (Connection c = freshDatabase("mig3"); Statement st = c.createStatement()) {
            st.execute("INSERT INTO team (name, name_key) VALUES ('Réseau', 'réseau')");
            assertThatThrownBy(() -> st.execute("INSERT INTO team (name, name_key) VALUES ('RÉSEAU', 'réseau')"))
                    .as("nom d'équipe unique").isInstanceOf(SQLException.class);

            st.execute("INSERT INTO app_user (id) VALUES (5)");
            st.execute("INSERT INTO team_member (team_id, user_id) VALUES (1, 5)");
            assertThatThrownBy(() -> st.execute("INSERT INTO team_member (team_id, user_id) VALUES (1, 5)"))
                    .as("membre en double").isInstanceOf(SQLException.class);

            assertThatThrownBy(() -> st.execute("INSERT INTO team_task (title, status) VALUES ('t', 'BIDON')"))
                    .as("statut de tâche").isInstanceOf(SQLException.class);
            assertThatThrownBy(() -> st.execute("INSERT INTO team_task (title, priority) VALUES ('t', 'URGENTISSIME')"))
                    .as("priorité de tâche").isInstanceOf(SQLException.class);
            assertThatThrownBy(() -> st.execute("INSERT INTO maintenance (title, resource_type, resource_name, start_at, end_at) "
                    + "VALUES ('m', 'NODE', 'n', TIMESTAMP '2026-12-01 12:00:00', TIMESTAMP '2026-12-01 10:00:00')"))
                    .as("fin avant début").isInstanceOf(SQLException.class);
            assertThatThrownBy(() -> st.execute("INSERT INTO maintenance (title, resource_type, resource_name, start_at, end_at, status) "
                    + "VALUES ('m', 'NODE', 'n', TIMESTAMP '2026-12-01 10:00:00', TIMESTAMP '2026-12-01 12:00:00', 'FINI')"))
                    .as("statut de maintenance").isInstanceOf(SQLException.class);
            assertThatThrownBy(() -> st.execute("INSERT INTO maintenance (title, resource_type, resource_name, start_at, end_at) "
                    + "VALUES ('m', 'GALAXIE', 'n', TIMESTAMP '2026-12-01 10:00:00', TIMESTAMP '2026-12-01 12:00:00')"))
                    .as("type de ressource").isInstanceOf(SQLException.class);

            st.execute("INSERT INTO report_schedule (report_type, frequency) VALUES ('PERFORMANCE', 'DAILY')");
            assertThatThrownBy(() -> st.execute("INSERT INTO report_schedule (report_type, frequency) VALUES ('PERFORMANCE', 'DAILY')"))
                    .as("planning unique par type et fréquence").isInstanceOf(SQLException.class);
            st.execute("INSERT INTO report (report_type, title, period_start, period_end, schedule_id, trigger_type) "
                    + "VALUES ('PERFORMANCE', 'r', TIMESTAMP '2026-11-01 00:00:00', TIMESTAMP '2026-11-02 00:00:00', 1, 'SCHEDULED')");
            assertThatThrownBy(() -> st.execute("INSERT INTO report (report_type, title, period_start, period_end, schedule_id, trigger_type) "
                    + "VALUES ('PERFORMANCE', 'r2', TIMESTAMP '2026-11-01 00:00:00', TIMESTAMP '2026-11-02 00:00:00', 1, 'SCHEDULED')"))
                    .as("un rapport par planning et par période").isInstanceOf(SQLException.class);
            // deux rapports manuels (planning nul) sur la même période restent permis
            st.execute("INSERT INTO report (report_type, title, period_start, period_end) VALUES ('PERFORMANCE', 'm1', TIMESTAMP '2026-11-01 00:00:00', TIMESTAMP '2026-11-02 00:00:00')");
            st.execute("INSERT INTO report (report_type, title, period_start, period_end) VALUES ('PERFORMANCE', 'm2', TIMESTAMP '2026-11-01 00:00:00', TIMESTAMP '2026-11-02 00:00:00')");
        }
    }
}
