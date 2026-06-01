package com.orange.pfe.backend.service.remediation;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Service;

import java.util.concurrent.TimeUnit;

@Service
public class RemediationMetricsService {

    private final Counter workflowsStarted;
    private final Counter autoRemediationsAttempted;
    private final Counter remediationsSucceeded;
    private final Counter remediationsFailed;
    private final Counter approvalsPending;
    private final Counter verificationSucceeded;
    private final Counter verificationFailed;
    private final Timer executionDuration;
    private final Timer verificationDuration;

    public RemediationMetricsService(MeterRegistry meterRegistry) {
        workflowsStarted = meterRegistry.counter("aiops.workflow.starts");
        autoRemediationsAttempted = meterRegistry.counter("aiops.remediation.auto.attempted");
        remediationsSucceeded = meterRegistry.counter("aiops.remediation.succeeded");
        remediationsFailed = meterRegistry.counter("aiops.remediation.failed");
        approvalsPending = meterRegistry.counter("aiops.approvals.pending");
        verificationSucceeded = meterRegistry.counter("aiops.remediation.verification.succeeded");
        verificationFailed = meterRegistry.counter("aiops.remediation.verification.failed");
        executionDuration = meterRegistry.timer("aiops.remediation.execution.duration");
        verificationDuration = meterRegistry.timer("aiops.remediation.verification.duration");
    }

    public void workflowStarted() {
        workflowsStarted.increment();
    }

    public void autoRemediationAttempted() {
        autoRemediationsAttempted.increment();
    }

    public void remediationSucceeded() {
        remediationsSucceeded.increment();
    }

    public void remediationFailed() {
        remediationsFailed.increment();
    }

    public void approvalPending() {
        approvalsPending.increment();
    }

    public void verification(boolean success) {
        if (success) {
            verificationSucceeded.increment();
        } else {
            verificationFailed.increment();
        }
    }

    public void executionDuration(long durationNanos) {
        executionDuration.record(durationNanos, TimeUnit.NANOSECONDS);
    }

    public void verificationDuration(long durationNanos) {
        verificationDuration.record(durationNanos, TimeUnit.NANOSECONDS);
    }
}
