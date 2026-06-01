package com.orange.pfe.backend.dto;

import java.time.LocalDateTime;

public class ErrorResponse {

    private final String error;
    private final int status;
    private final LocalDateTime timestamp;
    private String path;
    private String requestId;

    public ErrorResponse(String error, int status) {
        this.error = error;
        this.status = status;
        this.timestamp = LocalDateTime.now();
    }

    public ErrorResponse withPath(String path) {
        this.path = path;
        return this;
    }

    public ErrorResponse withRequestId(String requestId) {
        this.requestId = requestId;
        return this;
    }

    public String getError()       { return error; }
    public int getStatus()         { return status; }
    public LocalDateTime getTimestamp() { return timestamp; }
    public String getPath()        { return path; }
    public String getRequestId()   { return requestId; }
}
