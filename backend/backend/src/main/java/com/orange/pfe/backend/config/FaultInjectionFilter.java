package com.orange.pfe.backend.config;

import com.orange.pfe.backend.model.fault.FaultScenario;
import com.orange.pfe.backend.service.FaultInjectionService;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class FaultInjectionFilter extends OncePerRequestFilter {

    private static final List<String> EXCLUDED_PATH_PREFIXES = List.of(
            "/api/test/faults",
            "/api/test/health-readiness",
            "/api/test/health-liveness",
            "/api/incidents",
            "/actuator",
            "/error"
    );

    private final FaultInjectionService faultInjectionService;

    public FaultInjectionFilter(FaultInjectionService faultInjectionService) {
        this.faultInjectionService = faultInjectionService;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return EXCLUDED_PATH_PREFIXES.stream().anyMatch(path::startsWith);
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {

        String path = request.getRequestURI();

        if (faultInjectionService.isEnabled(FaultScenario.READINESS_FAILURE) && path.startsWith("/actuator/health/readiness")) {
            response.setStatus(HttpServletResponse.SC_SERVICE_UNAVAILABLE);
            response.setContentType("application/json");
            response.getWriter().write("{\"status\":\"DOWN\",\"reason\":\"READINESS_FAILURE fault is enabled\"}");
            return;
        }

        if (faultInjectionService.isEnabled(FaultScenario.LIVENESS_FAILURE) && path.startsWith("/actuator/health/liveness")) {
            response.setStatus(HttpServletResponse.SC_SERVICE_UNAVAILABLE);
            response.setContentType("application/json");
            response.getWriter().write("{\"status\":\"DOWN\",\"reason\":\"LIVENESS_FAILURE fault is enabled\"}");
            return;
        }

        if (faultInjectionService.isEnabled(FaultScenario.HTTP_500) && shouldInjectHttp500(path)) {
            throw new RuntimeException("Injected HTTP 500 fault");
        }

        if (faultInjectionService.isEnabled(FaultScenario.HIGH_LATENCY) && shouldInjectLatency(path)) {
            try {
                Thread.sleep(5000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new ServletException("Latency injection interrupted", e);
            }
        }

        filterChain.doFilter(request, response);
    }

    private boolean shouldInjectHttp500(String path) {
        return path.startsWith("/api/");
    }

    private boolean shouldInjectLatency(String path) {
        return path.startsWith("/api/");
    }
}
