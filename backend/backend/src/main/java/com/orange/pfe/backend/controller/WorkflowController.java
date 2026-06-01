package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.WorkflowInstanceDTO;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.service.WorkflowService;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/workflows")
public class WorkflowController {

    private final WorkflowService service;

    public WorkflowController(WorkflowService service) {
        this.service = service;
    }

    // 🔹 Récupérer un workflow par ID avec ses logs
    @GetMapping("/{id}")
    public WorkflowInstanceDTO getById(@PathVariable Long id) {
        return service.getWorkflow(id);
    }

    // 🔹 Récupérer tous les workflows avec leurs logs
    @GetMapping
    public List<WorkflowInstanceDTO> getAll() {
        return service.getAllWorkflows();
    }

    // 🔹 Terminer un workflow
    @PostMapping("/{id}/complete")
    public WorkflowInstanceDTO completeWorkflow(@PathVariable Long id) {
        WorkflowInstance wf = service.completeWorkflow(id);
        return service.getWorkflow(wf.getId());
    }

    // 🔹 Passer à l’étape suivante d’un workflow
    @PostMapping("/{id}/next-step")
    public WorkflowInstanceDTO nextStep(@PathVariable Long id) {
        WorkflowInstance wf = service.getWorkflowInstance(id);
        wf = service.nextStep(wf);
        return service.getWorkflow(wf.getId());
    }
}
