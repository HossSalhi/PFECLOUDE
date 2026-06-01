package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.IncidentPatchRequest;
import com.orange.pfe.backend.dto.IncidentRequest;
import com.orange.pfe.backend.dto.IncidentResponse;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import org.springframework.data.domain.Page;

public interface IncidentService {

    IncidentResponse createIncident(IncidentRequest request);

    IncidentResponse getById(Long id);

    Page<IncidentResponse> getAll(int page, int size, IncidentStatus status);

    IncidentResponse updateStatus(Long id, IncidentStatus status);

    IncidentResponse patchIncident(Long id, IncidentPatchRequest request);

    void archiveIncident(Long id);
}
