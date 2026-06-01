package com.orange.pfe.backend.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "camunda")
public class CamundaProperties {

    private boolean enabled;
    private String restAddress = "http://192.168.148.161:8088";
    private String grpcAddress = "http://192.168.148.161:26500";
    private boolean preferRestOverGrpc = true;
    private int requestTimeoutSeconds = 60;
    private int jobPollIntervalSeconds = 2;
    private int workerMaxJobsActive = 4;
    private String username;
    private String password;
    private String processId = "incident-remediation";
    private boolean autoDeployBpmn = true;

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public String getRestAddress() {
        return restAddress;
    }

    public void setRestAddress(String restAddress) {
        this.restAddress = restAddress;
    }

    public String getGrpcAddress() {
        return grpcAddress;
    }

    public void setGrpcAddress(String grpcAddress) {
        this.grpcAddress = grpcAddress;
    }

    public boolean isPreferRestOverGrpc() {
        return preferRestOverGrpc;
    }

    public void setPreferRestOverGrpc(boolean preferRestOverGrpc) {
        this.preferRestOverGrpc = preferRestOverGrpc;
    }

    public int getRequestTimeoutSeconds() {
        return requestTimeoutSeconds;
    }

    public void setRequestTimeoutSeconds(int requestTimeoutSeconds) {
        this.requestTimeoutSeconds = requestTimeoutSeconds;
    }

    public int getJobPollIntervalSeconds() {
        return jobPollIntervalSeconds;
    }

    public void setJobPollIntervalSeconds(int jobPollIntervalSeconds) {
        this.jobPollIntervalSeconds = jobPollIntervalSeconds;
    }

    public int getWorkerMaxJobsActive() {
        return workerMaxJobsActive;
    }

    public void setWorkerMaxJobsActive(int workerMaxJobsActive) {
        this.workerMaxJobsActive = workerMaxJobsActive;
    }

    public String getUsername() {
        return username;
    }

    public void setUsername(String username) {
        this.username = username;
    }

    public String getPassword() {
        return password;
    }

    public void setPassword(String password) {
        this.password = password;
    }

    public String getProcessId() {
        return processId;
    }

    public void setProcessId(String processId) {
        this.processId = processId;
    }

    public boolean isAutoDeployBpmn() {
        return autoDeployBpmn;
    }

    public void setAutoDeployBpmn(boolean autoDeployBpmn) {
        this.autoDeployBpmn = autoDeployBpmn;
    }
}
