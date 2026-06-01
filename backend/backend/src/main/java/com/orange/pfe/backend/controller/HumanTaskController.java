package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.HumanTaskResponse;
import com.orange.pfe.backend.model.human.TaskDecision;
import com.orange.pfe.backend.service.HumanTaskService;
import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController
@RequestMapping("/api/tasks")
public class HumanTaskController {

    private final HumanTaskService service;

    public HumanTaskController(HumanTaskService service) {
        this.service = service;
    }

    @PostMapping("/{id}/approve")
    public HumanTaskResponse approve(@PathVariable Long id) {
        return service.decideTask(id, TaskDecision.APPROVED);
    }

    @PostMapping("/{id}/reject")
    public HumanTaskResponse reject(@PathVariable Long id) {
        return service.decideTask(id, TaskDecision.REJECTED);
    }

    @PostMapping("/{id}/escalate")
    public HumanTaskResponse escalate(@PathVariable Long id) {
        return service.decideTask(id, TaskDecision.ESCALATED);
    }

    @GetMapping
    public List<HumanTaskResponse> getAllTasks() {
        return service.getAllTasks();
    }

    @GetMapping("/pending")
    public List<HumanTaskResponse> getPendingTasks() {
        return service.getPendingTasks();
    }
}
