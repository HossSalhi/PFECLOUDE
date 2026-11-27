package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TelemetryAnalysisRequest;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TelemetryAnalysisView;
import com.orange.pfe.backend.model.telemetry.TelemetryAnalysisRun;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.TeamRepository;
import com.orange.pfe.backend.repository.TelemetryAnalysisRunRepository;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryAnalysisGate;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryAnalysisService;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryDetector;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Detection;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.EntityCoverage;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Finding;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.QueryTrace;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.SourceQuality;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Trace;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryRunWriter;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import com.orange.pfe.backend.service.org.OrgAccessService;
import com.zaxxer.hikari.HikariDataSource;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.FilterType;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.orm.jpa.JpaTransactionManager;
import org.springframework.orm.jpa.LocalContainerEntityManagerFactoryBean;
import org.springframework.orm.jpa.vendor.HibernateJpaVendorAdapter;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.EnableTransactionManagement;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.server.ResponseStatusException;

import javax.sql.DataSource;
import java.io.IOException;
import java.io.InputStream;
import java.net.ServerSocket;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Intégration JPA / PostgreSQL RÉELLE de la migration V33 : un PostgreSQL 16 temporaire est démarré (binaires installés sur le
 * poste, variable {@code HELIOS_PG_BIN} pour en désigner d'autres), V33 est appliquée par le vrai Flyway, puis on vérifie que
 * l'entité correspond au schéma (validation Hibernate), l'enregistrement et la relecture d'une analyse, les déclencheurs
 * d'immuabilité (UPDATE, DELETE, TRUNCATE, y compris en mode réplica), le script de durcissement, le limiteur d'analyses
 * simultanées et l'absence de connexion de base retenue pendant les appels externes. Ignoré (et non échoué) si PostgreSQL est
 * introuvable. Ne touche JAMAIS à une base HELIOS : tout se passe dans un répertoire temporaire, sur un port libre.
 */
class TelemetryAnalysisPostgresTest {

    private static final String OWNER = "app_owner";          // simule le rôle applicatif propriétaire des tables créées par Flyway
    private static Path bin;
    private static Path root;
    private static int port;
    private static boolean started;

    // ── démarrage / arrêt du PostgreSQL temporaire ───────────────────────────────────────────────────────────────

    @BeforeAll
    static void startPostgres() throws Exception {
        bin = findBin();
        Assumptions.assumeTrue(bin != null, "PostgreSQL introuvable (définir HELIOS_PG_BIN) : test d'intégration ignoré");
        root = Files.createTempDirectory("helios-pg-it");
        try (ServerSocket s = new ServerSocket(0)) {
            port = s.getLocalPort();
        }
        run(List.of(exe("initdb"), "-D", root.resolve("data").toString(), "-U", "tester", "--auth=trust", "-E", "UTF8"), 120);
        run(List.of(exe("pg_ctl"), "-D", root.resolve("data").toString(), "-o", "-p " + port + " -c listen_addresses=127.0.0.1",
                "-l", root.resolve("pg.log").toString(), "-w", "-t", "60", "start"), 90);
        started = true;
        admin("postgres", "create role " + OWNER + " login");
    }

    @AfterAll
    static void stopPostgres() throws Exception {
        if (started) {
            run(List.of(exe("pg_ctl"), "-D", root.resolve("data").toString(), "-m", "immediate", "stop"), 60);
        }
        if (root != null) {
            try (Stream<Path> w = Files.walk(root)) {
                w.sorted(Comparator.reverseOrder()).forEach(p -> p.toFile().delete());     // supprime SEULEMENT son propre répertoire temporaire
            }
        }
    }

    private static Path findBin() {
        List<String> candidates = new ArrayList<>();
        if (System.getenv("HELIOS_PG_BIN") != null) {
            candidates.add(System.getenv("HELIOS_PG_BIN"));
        }
        for (String v : List.of("17", "16", "15", "14")) {
            candidates.add("C:/Program Files/PostgreSQL/" + v + "/bin");
            candidates.add("/usr/lib/postgresql/" + v + "/bin");
        }
        return candidates.stream().map(Path::of).filter(p -> Files.exists(p.resolve("initdb.exe")) || Files.exists(p.resolve("initdb"))).findFirst().orElse(null);
    }

    private static String exe(String name) {
        Path p = bin.resolve(name + ".exe");
        return (Files.exists(p) ? p : bin.resolve(name)).toString();
    }

    private static void run(List<String> cmd, int seconds) throws Exception {
        Path log = Files.createTempFile(root == null ? Path.of(System.getProperty("java.io.tmpdir")) : root, "cmd", ".log");
        Process p = new ProcessBuilder(cmd).redirectErrorStream(true).redirectOutput(log.toFile()).start();
        if (!p.waitFor(seconds, TimeUnit.SECONDS)) {
            p.destroyForcibly();
            throw new IllegalStateException("Délai dépassé : " + cmd.get(0));
        }
        if (p.exitValue() != 0) {
            throw new IllegalStateException("Échec " + cmd.get(0) + " : " + Files.readString(log));
        }
    }

    // ── connexions et bases ──────────────────────────────────────────────────────────────────────────────────────

    private static String url(String db) {
        return "jdbc:postgresql://127.0.0.1:" + port + "/" + db;
    }

    private static Connection connect(String db, String user) throws SQLException {
        Properties p = new Properties();
        p.setProperty("user", user);
        return DriverManager.getConnection(url(db), p);
    }

    private static void admin(String db, String... statements) throws SQLException {
        try (Connection c = connect(db, "tester"); Statement s = c.createStatement()) {
            for (String sql : statements) {
                s.execute(sql);
            }
        }
    }

    /** Nouvelle base PROPRE, propriété de app_owner, avec V33 appliquée par Flyway (baseline à 32 : seule V33 est jouée). */
    private static String newDb(String name) throws Exception {
        admin("postgres", "create database " + name + " owner " + OWNER);
        Path dir = Files.createTempDirectory(root, "mig");
        try (InputStream in = TelemetryAnalysisPostgresTest.class.getResourceAsStream("/db/migration/V33__telemetry_analysis_run.sql")) {
            Files.copy(in, dir.resolve("V33__telemetry_analysis_run.sql"), StandardCopyOption.REPLACE_EXISTING);
        }
        Flyway.configure().dataSource(url(name), OWNER, "").locations("filesystem:" + dir.toString().replace('\\', '/'))
                .baselineOnMigrate(true).baselineVersion("32").load().migrate();
        return name;
    }

    private static final String INSERT = "insert into telemetry_analysis_run (requested_at, scope, window_minutes, window_start, window_end, verdict, data_quality,"
            + " anomaly_count, high_count, findings_json, quality_json, trace_json, narrative_status, duration_ms)"
            + " values (now(), 'VM', 60, now(), now(), 'NO_ANOMALY', 'COMPLETE', 0, 0, '[]', '[]', '{}', 'OK', 5)";

    private static String errorOf(String db, String user, String sql) {
        try (Connection c = connect(db, user); Statement s = c.createStatement()) {
            s.execute(sql);
            return null;
        } catch (SQLException e) {
            return e.getSQLState() + " | " + e.getMessage();
        }
    }

    // ── mini-contexte Spring + JPA sur la base temporaire ────────────────────────────────────────────────────────

    @Configuration
    @EnableTransactionManagement
    @EnableJpaRepositories(basePackageClasses = TelemetryAnalysisRunRepository.class,
            includeFilters = @ComponentScan.Filter(type = FilterType.ASSIGNABLE_TYPE, classes = TelemetryAnalysisRunRepository.class))
    static class JpaConfig {
        static String db;
        static AuditTrailService audit = mock(AuditTrailService.class);

        @Bean(destroyMethod = "close")
        HikariDataSource dataSource() {
            HikariDataSource ds = new HikariDataSource();
            ds.setJdbcUrl(url(db));
            ds.setUsername(OWNER);
            ds.setMaximumPoolSize(3);
            ds.setMinimumIdle(0);
            ds.setConnectionTimeout(5000);
            return ds;
        }

        @Bean
        LocalContainerEntityManagerFactoryBean entityManagerFactory(DataSource ds) {
            LocalContainerEntityManagerFactoryBean f = new LocalContainerEntityManagerFactoryBean();
            f.setDataSource(ds);
            f.setPackagesToScan("com.orange.pfe.backend.model.telemetry");
            f.setJpaVendorAdapter(new HibernateJpaVendorAdapter());
            Properties p = new Properties();
            p.setProperty("hibernate.hbm2ddl.auto", "validate");     // l'entité DOIT correspondre au schéma réel créé par V33
            f.setJpaProperties(p);
            return f;
        }

        @Bean
        PlatformTransactionManager transactionManager(jakarta.persistence.EntityManagerFactory emf) {
            return new JpaTransactionManager(emf);
        }

        @Bean
        JdbcTemplate jdbcTemplate(DataSource ds) {
            return new JdbcTemplate(ds);
        }

        @Bean
        TelemetryAnalysisGate gate(JdbcTemplate jdbc) {
            return new TelemetryAnalysisGate(jdbc);
        }

        @Bean
        TelemetryRunWriter writer(TelemetryAnalysisRunRepository runs) {
            return new TelemetryRunWriter(runs, audit);
        }
    }

    private static AnnotationConfigApplicationContext context(String db) {
        JpaConfig.db = db;
        JpaConfig.audit = mock(AuditTrailService.class);
        return new AnnotationConfigApplicationContext(JpaConfig.class);
    }

    private static TelemetryAnalysisRun sampleRun(Long teamId, String verdict, String quality, int anomalies) {
        return new TelemetryAnalysisRun(LocalDateTime.of(2026, 11, 27, 12, 0), 7L, "chef.alpha", teamId, teamId == null ? null : "Réseau", "VM", 60,
                LocalDateTime.of(2026, 11, 27, 11, 0), LocalDateTime.of(2026, 11, 27, 12, 0), verdict, quality, anomalies, 1,
                "[{\"entity\":\"k8s-worker1\",\"evidence\":\"PromQL : x\"}]", "[{\"source\":\"Prometheus\"}]", "{\"rulesVersion\":\"telemetry-rules-2\"}",
                "OK", "Synthèse de test avec des accents é è à.", null, "openai/gpt-oss-20b", 1234);
    }

    // ═════════════════════ V33 / JPA ═════════════════════════════════════════════════════════════════════════════

    @Test
    void v33AppliedByFlywayMatchesTheEntityAndAnAnalysisCanBeSavedAndReadBack() throws Exception {
        String db = newDb("it_jpa");
        try (AnnotationConfigApplicationContext ctx = context(db)) {            // la validation Hibernate échouerait ici si le schéma divergeait
            TelemetryAnalysisRunRepository repo = ctx.getBean(TelemetryAnalysisRunRepository.class);
            TelemetryRunWriter writer = ctx.getBean(TelemetryRunWriter.class);
            AppUser actor = AiTestSupport.user(7, "chef.alpha", "Chef", "Alpha", UserRole.USER);

            TelemetryAnalysisRun saved = writer.persist(actor, sampleRun(3L, "ANOMALIES", "PARTIAL", 2), "Anomalies détectées");
            writer.persist(actor, sampleRun(4L, "NO_ANOMALY", "COMPLETE", 0), "Aucune anomalie");
            writer.persist(actor, sampleRun(3L, "PARTIAL_COVERAGE", "PARTIAL", 0), "Couverture partielle");

            assertThat(saved.getId()).isNotNull();
            TelemetryAnalysisRun read = repo.findById(saved.getId()).orElseThrow();
            assertThat(read.getVerdict()).isEqualTo("ANOMALIES");
            assertThat(read.getDataQuality()).isEqualTo("PARTIAL");
            assertThat(read.getTraceJson()).contains("telemetry-rules-2");
            assertThat(read.getFindingsJson()).contains("PromQL : x");
            assertThat(read.getNarrativeText()).contains("é è à");
            assertThat(read.getRequestedByUsername()).isEqualTo("chef.alpha");
            assertThat(read.getDurationMs()).isEqualTo(1234);

            assertThat(repo.findAllByOrderByRequestedAtDesc(PageRequest.of(0, 10)).getTotalElements()).isEqualTo(3);
            assertThat(repo.findByTeamIdOrderByRequestedAtDesc(3L, PageRequest.of(0, 10)).getContent()).hasSize(2);
            assertThat(repo.findByTeamIdInOrderByRequestedAtDesc(List.of(4L), PageRequest.of(0, 10)).getContent()).hasSize(1);
            assertThat(repo.findByTeamIdInOrderByRequestedAtDesc(List.of(99L), PageRequest.of(0, 10)).getContent()).isEmpty();

            ArgumentCaptor<AuditEvent> ev = ArgumentCaptor.forClass(AuditEvent.class);
            verify(JpaConfig.audit, org.mockito.Mockito.times(3)).record(ev.capture());
            assertThat(ev.getAllValues().get(0).summary()).contains("#" + saved.getId()).contains("Anomalies détectées");
        }
    }

    @Test
    void v33DoesNotTouchExistingDataAndDeclaresNoForeignKey() throws Exception {
        admin("postgres", "create database it_data owner " + OWNER);
        try (Connection c = connect("it_data", OWNER); Statement s = c.createStatement()) {
            s.execute("create table existing_table (id bigint primary key, label text)");
            s.execute("insert into existing_table values (1, 'avant'), (2, 'avant')");
        }
        Path dir = Files.createTempDirectory(root, "mig");
        try (InputStream in = getClass().getResourceAsStream("/db/migration/V33__telemetry_analysis_run.sql")) {
            Files.copy(in, dir.resolve("V33__telemetry_analysis_run.sql"));
        }
        Flyway.configure().dataSource(url("it_data"), OWNER, "").locations("filesystem:" + dir.toString().replace('\\', '/'))
                .baselineOnMigrate(true).baselineVersion("32").load().migrate();
        try (Connection c = connect("it_data", OWNER); Statement s = c.createStatement()) {
            var rs = s.executeQuery("select count(*), min(label), max(label) from existing_table");
            rs.next();
            assertThat(rs.getInt(1)).isEqualTo(2);
            assertThat(rs.getString(2)).isEqualTo("avant");
            assertThat(rs.getString(3)).isEqualTo("avant");
            var fk = s.executeQuery("select count(*) from pg_constraint where contype = 'f' and (conrelid = 'telemetry_analysis_run'::regclass or confrelid = 'telemetry_analysis_run'::regclass)");
            fk.next();
            assertThat(fk.getInt(1)).isZero();
        }
    }

    @Test
    void checkConstraintsRejectUnknownVerdictsQualitiesAndScopes() throws Exception {
        String db = newDb("it_check");
        assertThat(errorOf(db, OWNER, INSERT)).isNull();
        assertThat(errorOf(db, OWNER, INSERT.replace("'NO_ANOMALY'", "'BANANE'"))).startsWith("23514");
        assertThat(errorOf(db, OWNER, INSERT.replace("'COMPLETE'", "'PEUT-ETRE'"))).startsWith("23514");
        assertThat(errorOf(db, OWNER, INSERT.replace("'VM'", "'TOUT'"))).startsWith("23514");
        assertThat(errorOf(db, OWNER, INSERT.replace("'NO_ANOMALY'", "'PARTIAL_COVERAGE'"))).isNull();     // nouveau verdict accepté
    }

    // ═════════════════════ Immuabilité ═══════════════════════════════════════════════════════════════════════════

    @Test
    void triggersRefuseUpdateDeleteAndTruncateEvenForTheOwnerAndInReplicaMode() throws Exception {
        String db = newDb("it_trig");
        assertThat(errorOf(db, OWNER, INSERT)).isNull();

        assertThat(errorOf(db, OWNER, "update telemetry_analysis_run set verdict = 'ANOMALIES'")).startsWith("23001").contains("UPDATE interdit");
        assertThat(errorOf(db, OWNER, "delete from telemetry_analysis_run")).startsWith("23001").contains("DELETE interdit");
        assertThat(errorOf(db, OWNER, "truncate telemetry_analysis_run")).startsWith("23001").contains("TRUNCATE interdit");

        // ENABLE ALWAYS : même un superutilisateur en session_replication_role = replica (qui désactive les déclencheurs ordinaires) est bloqué
        try (Connection c = connect(db, "tester"); Statement s = c.createStatement()) {
            s.execute("set session_replication_role = replica");
            assertThatThrownBy(() -> s.execute("update telemetry_analysis_run set verdict = 'ANOMALIES'")).hasMessageContaining("UPDATE interdit");
            assertThatThrownBy(() -> s.execute("delete from telemetry_analysis_run")).hasMessageContaining("DELETE interdit");
            assertThatThrownBy(() -> s.execute("truncate telemetry_analysis_run")).hasMessageContaining("TRUNCATE interdit");
        }
        try (Connection c = connect(db, OWNER); Statement s = c.createStatement()) {
            var rs = s.executeQuery("select count(*), min(verdict) from telemetry_analysis_run");
            rs.next();
            assertThat(rs.getInt(1)).isEqualTo(1);                 // la ligne est intacte
            assertThat(rs.getString(2)).isEqualTo("NO_ANOMALY");
        }
    }

    @Test
    void withoutHardeningTheOwnerCanStillDisableTheTriggersWhichIsWhyTheHardeningScriptExists() throws Exception {
        String db = newDb("it_owner");
        assertThat(errorOf(db, OWNER, INSERT)).isNull();

        // LIMITE DOCUMENTÉE : le propriétaire (le rôle applicatif, tant que Flyway l'a créée) peut désactiver ses propres déclencheurs
        assertThat(errorOf(db, OWNER, "alter table telemetry_analysis_run disable trigger trg_telemetry_run_no_update")).isNull();
        assertThat(errorOf(db, OWNER, "update telemetry_analysis_run set verdict = 'ANOMALIES'")).isNull();
    }

    @Test
    void hardeningScriptRemovesEveryWayForTheApplicationRoleToAlterOrBypassTheHistory() throws Exception {
        String db = newDb("it_hard");
        assertThat(errorOf(db, OWNER, INSERT)).isNull();
        Path script = findHardeningScript();
        run(List.of(exe("psql"), "-h", "127.0.0.1", "-p", String.valueOf(port), "-U", "tester", "-d", db, "-v", "ON_ERROR_STOP=1",
                "-v", "app_role=" + OWNER, "-v", "owner_role=tester", "-f", script.toString()), 60);     // « tester » : le superutilisateur du cluster temporaire

        // ce que l'application peut encore faire : lire et ajouter
        assertThat(errorOf(db, OWNER, "select count(*) from telemetry_analysis_run")).isNull();
        assertThat(errorOf(db, OWNER, INSERT)).isNull();
        // ce qu'elle ne peut plus faire
        assertThat(errorOf(db, OWNER, "update telemetry_analysis_run set verdict = 'ANOMALIES'")).startsWith("42501");
        assertThat(errorOf(db, OWNER, "delete from telemetry_analysis_run")).startsWith("42501");
        assertThat(errorOf(db, OWNER, "truncate telemetry_analysis_run")).startsWith("42501");
        assertThat(errorOf(db, OWNER, "alter table telemetry_analysis_run disable trigger trg_telemetry_run_no_update")).startsWith("42501").contains("propriétaire");
        assertThat(errorOf(db, OWNER, "alter table telemetry_analysis_run disable trigger all")).startsWith("42501");
        assertThat(errorOf(db, OWNER, "drop trigger trg_telemetry_run_no_delete on telemetry_analysis_run")).startsWith("42501");
        // DROP TABLE : le propriétaire du schéma public (ici l'application, propriétaire de la base) pourrait supprimer la table :
        // c'est le déclencheur d'ÉVÉNEMENT du script qui l'en empêche, et l'application ne peut pas le désactiver
        assertThat(errorOf(db, OWNER, "drop table telemetry_analysis_run")).startsWith("23001").contains("DROP interdit");
        assertThat(errorOf(db, OWNER, "drop table if exists telemetry_analysis_run cascade")).startsWith("23001");
        assertThat(errorOf(db, OWNER, "drop schema public cascade")).startsWith("23001");
        assertThat(errorOf(db, OWNER, "alter event trigger trg_telemetry_run_no_drop disable")).startsWith("42501");
        assertThat(errorOf(db, OWNER, "drop event trigger trg_telemetry_run_no_drop")).startsWith("42501");
        assertThat(errorOf(db, OWNER, "alter table telemetry_analysis_run rename to x")).startsWith("42501");
        assertThat(errorOf(db, OWNER, "create or replace function telemetry_analysis_run_append_only() returns trigger language plpgsql as $$ begin return new; end $$")).startsWith("42501");
        // un GRANT par un non-propriétaire n'accorde rien (PostgreSQL émet un avertissement, pas une erreur) : la preuve est que l'UPDATE reste refusé
        errorOf(db, OWNER, "grant update, delete, truncate on telemetry_analysis_run to " + OWNER);
        assertThat(errorOf(db, OWNER, "update telemetry_analysis_run set verdict = 'ANOMALIES'")).startsWith("42501");
        assertThat(errorOf(db, OWNER, "delete from telemetry_analysis_run")).startsWith("42501");
        assertThat(errorOf(db, OWNER, "alter table telemetry_analysis_run owner to " + OWNER)).startsWith("42501");
        assertThat(errorOf(db, OWNER, "set session_replication_role = replica")).startsWith("42501");
        assertThat(errorOf(db, OWNER, "alter table telemetry_analysis_run drop column verdict")).startsWith("42501");
        // la table du limiteur, elle, reste utilisable par l'application
        assertThat(errorOf(db, OWNER, "insert into telemetry_analysis_inflight(user_id, token) values (1, 'x')")).isNull();
    }

    private static Path findHardeningScript() {
        for (Path p = Path.of("").toAbsolutePath(); p != null; p = p.getParent()) {
            Path f = p.resolve("infra/sql/telemetry_analysis_run_harden.sql");
            if (Files.exists(f)) {
                return f;
            }
            Path g = p.resolve("backend/backend/infra/sql/telemetry_analysis_run_harden.sql");
            if (Files.exists(g)) {
                return g;
            }
        }
        throw new IllegalStateException("Script de durcissement introuvable");
    }

    // ═════════════════════ Limiteur d'analyses simultanées ═══════════════════════════════════════════════════════

    @Test
    void theGateAllowsOneAnalysisPerUserAndCapsTheTotalAcrossReplicas() throws Exception {
        String db = newDb("it_gate");
        try (AnnotationConfigApplicationContext ctx = context(db)) {
            TelemetryAnalysisGate gate = ctx.getBean(TelemetryAnalysisGate.class);
            JdbcTemplate jdbc = ctx.getBean(JdbcTemplate.class);

            String a = gate.acquire(1);
            assertThatThrownBy(() -> gate.acquire(1)).isInstanceOf(ResponseStatusException.class)
                    .satisfies(e -> assertThat(((ResponseStatusException) e).getStatusCode().value()).isEqualTo(429))
                    .hasMessageContaining("déjà en cours");
            String b = gate.acquire(2);
            String c = gate.acquire(3);
            assertThatThrownBy(() -> gate.acquire(4)).isInstanceOf(ResponseStatusException.class).hasMessageContaining("Trop d'analyses");

            gate.release(2, "mauvais-jeton");                                         // jeton d'une autre analyse : sans effet
            assertThat(jdbc.queryForObject("select count(*) from telemetry_analysis_inflight", Integer.class)).isEqualTo(3);
            gate.release(2, b);
            assertThat(gate.acquire(4)).isNotBlank();                                 // une place s'est libérée

            // péremption : une analyse abandonnée (réplica arrêté) ne bloque pas l'utilisateur indéfiniment
            jdbc.update("update telemetry_analysis_inflight set started_at = now() - interval '5 minutes' where user_id = 1");
            assertThat(gate.acquire(1)).isNotEqualTo(a);
            gate.release(1, a);                                                       // l'ancien jeton ne libère pas la nouvelle place
            assertThat(jdbc.queryForObject("select count(*) from telemetry_analysis_inflight where user_id = 1", Integer.class)).isEqualTo(1);
            assertThat(c).isNotBlank();
        }
    }

    // ═════════════════════ Aucune connexion retenue pendant les appels externes ═════════════════════════════════

    private static Detection detection() {
        LocalDateTime t = LocalDateTime.of(2026, 11, 27, 12, 0);
        Trace trace = new Trace("telemetry-rules-2", t.minusHours(1), t, null, null, 60L, t.minusHours(1), t.minusHours(2), 3600L, Map.of("cpu", "85"),
                List.of(new QueryTrace("PROM_CPU", "Prometheus", "CPU", "q", "OK", null, 60L, t.minusHours(2), t, 6, 100)),
                List.of(new EntityCoverage("Prometheus", "k8s-worker1", "CPU", 1.0, "60 mesure(s) sur 60 attendues")));
        return new Detection("NO_ANOMALY", "COMPLETE", List.<Finding>of(), List.of(new SourceQuality("Prometheus", true, 1.0, "ok")), trace, t.minusHours(1), t, 1);
    }

    private record Wiring(TelemetryAnalysisService service, TelemetryDetector detector, AiCompletionClient client, AppUser admin, HikariDataSource pool,
                          TelemetryAnalysisGate gate, TelemetryAnalysisRunRepository runs, JdbcTemplate jdbc) {
    }

    private static Wiring wire(AnnotationConfigApplicationContext ctx) {
        TeamRepository teams = mock(TeamRepository.class);
        AiCompletionClient client = AiTestSupport.readyClient("Synthèse.");
        TelemetryDetector detector = mock(TelemetryDetector.class);
        TelemetryAnalysisGate gate = ctx.getBean(TelemetryAnalysisGate.class);
        TelemetryAnalysisRunRepository runs = ctx.getBean(TelemetryAnalysisRunRepository.class);
        TelemetryAnalysisService service = new TelemetryAnalysisService(AiTestSupport.guard(client), new OrgAccessService(mock(AuditTrailService.class)),
                detector, runs, teams, gate, ctx.getBean(TelemetryRunWriter.class));
        return new Wiring(service, detector, client, AiTestSupport.user(1, "root", "Ad", "Min", UserRole.SUPER_ADMIN), ctx.getBean(HikariDataSource.class),
                gate, runs, ctx.getBean(JdbcTemplate.class));
    }

    @Test
    void noDatabaseConnectionOrTransactionIsHeldWhileProbingPrometheusLokiAndGroq() throws Exception {
        String db = newDb("it_tx");
        try (AnnotationConfigApplicationContext ctx = context(db)) {
            Wiring w = wire(ctx);
            AtomicInteger activeDuringDetect = new AtomicInteger(-1), activeDuringAi = new AtomicInteger(-1);
            AtomicReference<Boolean> txDuringDetect = new AtomicReference<>(), txDuringAi = new AtomicReference<>();
            when(w.detector().detect(any(), anyInt(), any())).thenAnswer(i -> {
                activeDuringDetect.set(w.pool().getHikariPoolMXBean().getActiveConnections());
                txDuringDetect.set(TransactionSynchronizationManager.isActualTransactionActive());
                return detection();
            });
            when(w.client().complete(any(), any(), any())).thenAnswer(i -> {
                activeDuringAi.set(w.pool().getHikariPoolMXBean().getActiveConnections());
                txDuringAi.set(TransactionSynchronizationManager.isActualTransactionActive());
                return new AiCompletionResult.Success("Synthèse.", "test-model", java.time.Instant.now());
            });

            TelemetryAnalysisView view = w.service().analyse(w.admin(), new TelemetryAnalysisRequest(null, "ALL", 60));

            assertThat(activeDuringDetect.get()).as("connexions actives pendant Prometheus/Loki").isZero();
            assertThat(activeDuringAi.get()).as("connexions actives pendant Groq").isZero();
            assertThat(txDuringDetect.get()).isFalse();
            assertThat(txDuringAi.get()).isFalse();
            // et pourtant l'analyse est bien enregistrée, auditée, et sa place libérée
            assertThat(view.id()).isNotNull();
            assertThat(w.runs().findById(view.id())).isPresent();
            assertThat(w.jdbc().queryForObject("select count(*) from telemetry_analysis_inflight", Integer.class)).isZero();
            verify(JpaConfig.audit).record(any(AuditEvent.class));
        }
    }

    @Test
    void aSecondConcurrentAnalysisOfTheSameUserIsRefusedWhileAnotherUserIsServedAndThePlaceIsAlwaysReleased() throws Exception {
        String db = newDb("it_conc");
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try (AnnotationConfigApplicationContext ctx = context(db)) {
            Wiring w = wire(ctx);
            CountDownLatch inside = new CountDownLatch(1), proceed = new CountDownLatch(1);
            when(w.detector().detect(any(), anyInt(), any())).thenAnswer(i -> {
                inside.countDown();
                assertThat(proceed.await(20, TimeUnit.SECONDS)).isTrue();
                return detection();
            });
            AppUser other = AiTestSupport.user(2, "admin2", "Ad", "Deux", UserRole.ADMIN);

            Future<TelemetryAnalysisView> first = pool.submit(() -> w.service().analyse(w.admin(), new TelemetryAnalysisRequest(null, "ALL", 60)));
            assertThat(inside.await(10, TimeUnit.SECONDS)).isTrue();

            assertThatThrownBy(() -> w.service().analyse(w.admin(), new TelemetryAnalysisRequest(null, "ALL", 60)))
                    .isInstanceOf(ResponseStatusException.class).hasMessageContaining("déjà en cours");
            Future<TelemetryAnalysisView> second = pool.submit(() -> w.service().analyse(other, new TelemetryAnalysisRequest(null, "ALL", 60)));

            proceed.countDown();
            assertThat(first.get(20, TimeUnit.SECONDS).id()).isNotNull();
            assertThat(second.get(20, TimeUnit.SECONDS).id()).isNotNull();
            assertThat(w.jdbc().queryForObject("select count(*) from telemetry_analysis_inflight", Integer.class)).isZero();
            assertThat(w.runs().findAllByOrderByRequestedAtDesc(PageRequest.of(0, 10)).getTotalElements()).isEqualTo(2);     // le refus n'a rien enregistré
            assertThat(w.service().analyse(w.admin(), new TelemetryAnalysisRequest(null, "ALL", 60)).id()).isNotNull();       // et on peut relancer
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void anUnexpectedFailureStillReleasesThePlaceAndRecordsNothing() throws Exception {
        String db = newDb("it_fail");
        try (AnnotationConfigApplicationContext ctx = context(db)) {
            Wiring w = wire(ctx);
            when(w.detector().detect(any(), anyInt(), any())).thenThrow(new IllegalStateException("boum"));

            assertThatThrownBy(() -> w.service().analyse(w.admin(), new TelemetryAnalysisRequest(null, null, 60))).isInstanceOf(IllegalStateException.class);

            assertThat(w.jdbc().queryForObject("select count(*) from telemetry_analysis_inflight", Integer.class)).isZero();
            assertThat(w.runs().findAllByOrderByRequestedAtDesc(PageRequest.of(0, 10)).getTotalElements()).isZero();
            verify(JpaConfig.audit, never()).record(any(AuditEvent.class));
        }
    }
}
