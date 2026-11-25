package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.core.IncidentTerminalEvidence;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;

import java.time.LocalDateTime;

/** LOT HELIOS-INCIDENT-IDENTITY-CARD-01 : preuve terminal exposée au frontend. */
public record TerminalEvidenceResponse(
        Long id,
        Long incidentId,
        String vm,
        TerminalEvidencePhase phase,
        LocalDateTime capturedAt,
        String command,
        Integer exitCode,
        String stdout,
        String stderr,
        String measuresJson,
        boolean available,
        String source
) {
    public static TerminalEvidenceResponse from(IncidentTerminalEvidence e) {
        return new TerminalEvidenceResponse(
                e.getId(), e.getIncidentId(), e.getVm(), e.getPhase(), e.getCapturedAt(),
                e.getCommand(), e.getExitCode(), e.getStdout(), e.getStderr(),
                e.getMeasuresJson(), e.isAvailable(), e.getSource());
    }
}
