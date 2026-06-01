package com.orange.pfe.backend.model.fault;

public enum FaultScenario {
    HTTP_500,
    HIGH_LATENCY,
    DB_POOL_EXHAUSTION,
    CPU_STRESS,
    MEMORY_STRESS,
    READINESS_FAILURE,
    LIVENESS_FAILURE,
    CRASH_ON_DEMAND,
    CRASH_ON_STARTUP
}