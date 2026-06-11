import re

ERROR_TERMS = (
    "error",
    "exception",
    "failed",
    "failure",
    "timeout",
    "connection refused",
    "oom",
    # Java stack traces
    "caused by:",
    "at com.",
    "at org.",
    "at java.",
    "stacktrace:",
    "stackoverflowerror",
    "outofmemoryerror",
    "nullpointerexception",
    "illegalstateexception",
    "runtimeexception",
    # K8s / container signals
    "oomkilled",
    "crashloopbackoff",
    "errimagepull",
    "imagepullbackoff",
    "backoff pulling image",
    "liveness probe failed",
    "readiness probe failed",
    "container died",
    # Network / connectivity
    "connection reset",
    "broken pipe",
    "no route to host",
    "network unreachable",
    "dial tcp",
    # Security / auth
    "unauthorized",
    "forbidden",
    "certificate",
    "tls handshake",
    # Application-level
    "panic:",
    "fatal:",
    "segfault",
    "killed",
)

WARNING_TERMS = (
    "warn",
    "warning",
    "deprecated",
    "retrying",
    "retry",
    "slow query",
    "high memory",
    "high cpu",
    "throttl",
)

RESTART_TERMS = ("crash", "restart", "backoff", "crashloop", "restarting", "exited with code")
DB_TERMS = ("postgres", "jdbc", "database", "datasource", "hikari", "connection pool", "deadlock", "transaction")

# Critical patterns that alone indicate a serious problem (bypass error_ratio threshold)
CRITICAL_SINGLE_TERMS = (
    "outofmemoryerror",
    "oomkilled",
    "crashloopbackoff",
    "panic:",
    "fatal:",
    "killed",
    "segfault",
    "stackoverflowerror",
)

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


# Pre-compiled alternation regexes — a single C-level scan per line beats an N-term
# Python `in` loop. extract_log_features runs on up to ~5000 Loki lines per predict,
# so this is on the hot path.
_ERROR_RE = re.compile("|".join(re.escape(t) for t in ERROR_TERMS))
_WARNING_RE = re.compile("|".join(re.escape(t) for t in WARNING_TERMS))
_RESTART_RE = re.compile("|".join(re.escape(t) for t in RESTART_TERMS))
_DB_RE = re.compile("|".join(re.escape(t) for t in DB_TERMS))
_CRITICAL_RE = re.compile("|".join(re.escape(t) for t in CRITICAL_SINGLE_TERMS))
_NOISE_FRAGMENT_RE = re.compile("|".join(re.escape(t) for t in _NOISE_FRAGMENTS))


def _is_noise_line(text: str) -> bool:
    """Return True for framework/ORM debug lines that should not count as errors."""
    for prefix in _NOISE_PREFIXES:
        idx = text.find(prefix)
        if idx != -1 and idx < 60:  # prefix appears near the start (after timestamps)
            return True
    return _NOISE_FRAGMENT_RE.search(text) is not None


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
            "critical_term_hits": 0.0,
            "stack_trace_depth": 0.0,
        }

    lowered = [line.lower() for line in lines]
    semantic = [line for line in lowered if not _is_noise_line(line)]
    error_count = sum(1 for line in semantic if _ERROR_RE.search(line))
    warning_count = sum(1 for line in semantic if _WARNING_RE.search(line))
    restart_hits = sum(1 for line in semantic if _RESTART_RE.search(line))
    db_hits = sum(1 for line in semantic if _DB_RE.search(line))

    # Critical single terms — a single occurrence is already significant
    critical_hits = sum(1 for line in semantic if _CRITICAL_RE.search(line))

    # Stack trace depth: consecutive "at " lines indicate exception propagation depth
    stack_depth = sum(
        1 for line in lowered
        if line.lstrip().startswith("at ") or "caused by:" in line
    )

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
        "critical_term_hits": float(critical_hits),
        "stack_trace_depth": float(min(stack_depth, 50)),
    }


def score_log_risk(features: dict[str, float]) -> float:
    log_count = features.get("log_line_count", 0.0)
    error_ratio = features.get("error_ratio", 0.0)
    restart_hits = features.get("restart_keyword_hits", 0.0)
    critical_hits = features.get("critical_term_hits", 0.0)
    stack_depth = features.get("stack_trace_depth", 0.0)

    density_signal = min(log_count / 200.0, 1.0)
    restart_signal = min(restart_hits / 5.0, 1.0)
    critical_signal = min(critical_hits / 2.0, 1.0)   # even 1 OOMKilled = 0.5
    stack_signal = min(stack_depth / 20.0, 1.0)

    score = (
        (0.40 * error_ratio)
        + (0.20 * restart_signal)
        + (0.20 * critical_signal)
        + (0.10 * stack_signal)
        + (0.10 * density_signal)
    )

    return max(0.0, min(1.0, score))
