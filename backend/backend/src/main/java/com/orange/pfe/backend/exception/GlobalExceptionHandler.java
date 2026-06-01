package com.orange.pfe.backend.exception;

import com.fasterxml.jackson.databind.exc.InvalidFormatException;
import com.orange.pfe.backend.dto.ErrorResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.ConstraintViolationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    // ── 404 NOT FOUND ──────────────────────────────────────────────────────────

    @ExceptionHandler({IncidentNotFoundException.class, HumanTaskNotFoundException.class,
                       WorkflowNotFoundException.class, ResourceNotFoundException.class})
    public ResponseEntity<ErrorResponse> notFound(RuntimeException ex, HttpServletRequest req) {
        return build(HttpStatus.NOT_FOUND, ex.getMessage(), req);
    }

    // ── 400 BAD REQUEST ────────────────────────────────────────────────────────

    @ExceptionHandler({ActionException.class, InvalidIncidentStateException.class,
                       InvalidWorkflowStateException.class, InvalidTaskDecisionException.class})
    public ResponseEntity<ErrorResponse> badRequest(RuntimeException ex, HttpServletRequest req) {
        return build(HttpStatus.BAD_REQUEST, ex.getMessage(), req);
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ErrorResponse> illegalArgument(IllegalArgumentException ex, HttpServletRequest req) {
        return build(HttpStatus.BAD_REQUEST, ex.getMessage(), req);
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorResponse> validation(MethodArgumentNotValidException ex, HttpServletRequest req) {
        String msg = ex.getBindingResult().getFieldErrors().stream()
                .map(e -> e.getField() + ": " + e.getDefaultMessage())
                .reduce("", (a, b) -> a.isEmpty() ? b : a + "; " + b);
        return build(HttpStatus.BAD_REQUEST, msg, req);
    }

    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<ErrorResponse> constraintViolation(ConstraintViolationException ex, HttpServletRequest req) {
        String msg = ex.getConstraintViolations().stream()
                .map(v -> {
                    String path = v.getPropertyPath().toString();
                    String field = path.contains(".") ? path.substring(path.lastIndexOf('.') + 1) : path;
                    return field + ": " + v.getMessage();
                })
                .reduce("", (a, b) -> a.isEmpty() ? b : a + "; " + b);
        return build(HttpStatus.BAD_REQUEST, msg, req);
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ErrorResponse> typeMismatch(MethodArgumentTypeMismatchException ex, HttpServletRequest req) {
        return build(HttpStatus.BAD_REQUEST,
                "Invalid value for parameter '" + ex.getName() + "': " + ex.getValue(), req);
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ErrorResponse> notReadable(HttpMessageNotReadableException ex, HttpServletRequest req) {
        String msg = "Invalid request body";
        if (ex.getCause() instanceof InvalidFormatException cause && !cause.getPath().isEmpty()) {
            msg = "Invalid value '" + cause.getValue() + "' for field '"
                    + cause.getPath().get(0).getFieldName() + "'";
        }
        return build(HttpStatus.BAD_REQUEST, msg, req);
    }

    // ── 401 UNAUTHORIZED ───────────────────────────────────────────────────────

    @ExceptionHandler(BadCredentialsException.class)
    public ResponseEntity<ErrorResponse> badCredentials(BadCredentialsException ex, HttpServletRequest req) {
        return build(HttpStatus.UNAUTHORIZED, "Invalid credentials", req);
    }

    // ── 403 FORBIDDEN ──────────────────────────────────────────────────────────

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<ErrorResponse> accessDenied(AccessDeniedException ex, HttpServletRequest req) {
        return build(HttpStatus.FORBIDDEN, "Access denied", req);
    }

    // ── 409 CONFLICT ───────────────────────────────────────────────────────────

    @ExceptionHandler(IllegalStateException.class)
    public ResponseEntity<ErrorResponse> illegalState(IllegalStateException ex, HttpServletRequest req) {
        return build(HttpStatus.CONFLICT, ex.getMessage(), req);
    }

    // ── 502 BAD GATEWAY ────────────────────────────────────────────────────────

    @ExceptionHandler(AIOpsIntegrationException.class)
    public ResponseEntity<ErrorResponse> aiopsIntegration(AIOpsIntegrationException ex, HttpServletRequest req) {
        return build(HttpStatus.BAD_GATEWAY, ex.getMessage(), req);
    }

    // ── 500 FALLBACK ───────────────────────────────────────────────────────────

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ErrorResponse> generic(Exception ex, HttpServletRequest req) {
        log.error("Unhandled exception on {} {}", req.getMethod(), req.getRequestURI(), ex);
        return build(HttpStatus.INTERNAL_SERVER_ERROR, "Unexpected error — contact support", req);
    }

    // ── Helper ─────────────────────────────────────────────────────────────────

    private ResponseEntity<ErrorResponse> build(HttpStatus status, String message, HttpServletRequest req) {
        ErrorResponse body = new ErrorResponse(message, status.value())
                .withPath(req.getRequestURI())
                .withRequestId(MDC.get("requestId"));
        return ResponseEntity.status(status).body(body);
    }
}
