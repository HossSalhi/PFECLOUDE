package com.orange.pfe.backend.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class DbProbeService {

    private static final Logger log = LoggerFactory.getLogger(DbProbeService.class);

    private final JdbcTemplate jdbcTemplate;

    public DbProbeService(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public String probe() {
        try {
            Integer result = jdbcTemplate.queryForObject("SELECT 1", Integer.class);
            log.info("database probe succeeded for backend service");
            return "db-ok-" + result;
        } catch (RuntimeException ex) {
            log.error("database probe failed for backend service: postgres/jdbc connectivity issue", ex);
            throw ex;
        }
    }
}
