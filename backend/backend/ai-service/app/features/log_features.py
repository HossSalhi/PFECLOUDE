ERROR_TERMS = (
    "error",
    "exception",
    "failed",
    "failure",
    "timeout",
    "connection refused",
    "oom",
)

WARNING_TERMS = ("warn", "warning")
RESTART_TERMS = ("crash", "restart", "backoff", "crashloop")
DB_TERMS = ("postgres", "jdbc", "database")

# Prefixes that identify infrastructure/framework noise lines — these contain column
# names like "failed_reason" or SQL keywords that would generate false positives when
# matched against ERROR_TERMS. Skip them for semantic signal extraction.
_NOISE_PREFIXES = (
    "hibernate:",
    "hql:",
    "sql:",
)

# Substrings that, wherever they appear, flag a line as infrastructure noise.
# Camunda job-poller lines contain "failed to activate jobs" but are normal polling
# retries, not application-level errors.
# Spring's TaskUtils$LoggingErrorHandler wraps any scheduled-task exception and logs it
# as ERROR — the original exception is already counted on its own Hibernate/JDBC logger line.
_NOISE_FRAGMENTS = (
    "io.camunda.client",
    "camunda.client.job",
    "taskutils$loggingerrorhandler",
    "o.s.s.s.taskutils",
    # AIOps polling scheduler WARNs ("polling failed") are infra retry messages,
    # not application-level errors in the service being monitored.
    "aiopspollingscheduler",
    "camundaworkflowrecoveryservice",
    # Hibernate ORM pool init lines contain "jdbc:postgresql://..." and "postgres"
    # which match DB_TERMS — these are startup diagnostics, not DB errors.
    "o.hibernate.orm.connections",
    "hhh10001005",
    # HikariCP startup/shutdown lifecycle messages — NOT pool timeout/exhaustion errors.
    # Pool exhaustion errors ("connection is not available", "request timed out") from
    # HikariCP ARE meaningful signals and must NOT be filtered.
    "hikaripool-1 - start",
    "hikaripool-1 - shutdown",
    "hikaripool-1 - added connection",
    "hikariconfig",
)


def _is_noise_line(text: str) -> bool:
    """Return True for framework/ORM debug lines that should not count as errors."""
    for prefix in _NOISE_PREFIXES:
        idx = text.find(prefix)
        if idx != -1 and idx < 60:  # prefix appears near the start (after timestamps)
            return True
    for fragment in _NOISE_FRAGMENTS:
        if fragment in text:
            return True
    return False


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def extract_log_features(lines: list[str]) -> dict[str, float]:
    if not lines:
        return {
            "log_line_count": 0.0,
            "error_line_count": 0.0,
            "warning_line_count": 0.0,
            "restart_keyword_hits": 0.0,
            "db_keyword_hits": 0.0,
            "unique_log_templates": 0.0,
            "error_ratio": 0.0,
        }

    lowered = [line.lower() for line in lines]
    semantic = [line for line in lowered if not _is_noise_line(line)]
    error_count = sum(1 for line in semantic if _contains_any(line, ERROR_TERMS))
    warning_count = sum(1 for line in semantic if _contains_any(line, WARNING_TERMS))
    restart_hits = sum(1 for line in semantic if _contains_any(line, RESTART_TERMS))
    db_hits = sum(1 for line in semantic if _contains_any(line, DB_TERMS))

    # Lightweight template approximation to reduce cardinality noise.
    unique_templates = len({line[:120] for line in lowered})
    error_ratio = error_count / float(len(lowered))

    return {
        "log_line_count": float(len(lowered)),
        "error_line_count": float(error_count),
        "warning_line_count": float(warning_count),
        "restart_keyword_hits": float(restart_hits),
        "db_keyword_hits": float(db_hits),
        "unique_log_templates": float(unique_templates),
        "error_ratio": float(error_ratio),
    }


def score_log_risk(features: dict[str, float]) -> float:
    log_count = features.get("log_line_count", 0.0)
    error_ratio = features.get("error_ratio", 0.0)
    restart_hits = features.get("restart_keyword_hits", 0.0)

    density_signal = min(log_count / 200.0, 1.0)
    restart_signal = min(restart_hits / 5.0, 1.0)
    score = (0.55 * error_ratio) + (0.25 * restart_signal) + (0.20 * density_signal)

    return max(0.0, min(1.0, score))
