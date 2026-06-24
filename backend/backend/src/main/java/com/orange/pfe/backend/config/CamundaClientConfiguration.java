package com.orange.pfe.backend.config;

import io.camunda.client.CamundaClient;
import io.camunda.client.CamundaClientBuilder;
import io.camunda.client.impl.basicauth.BasicAuthCredentialsProviderBuilder;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.net.URI;
import java.time.Duration;

@Configuration
public class CamundaClientConfiguration {

    @Bean(destroyMethod = "close")
    @ConditionalOnProperty(name = "camunda.enabled", havingValue = "true")
    public CamundaClient camundaClient(CamundaProperties properties) {
        CamundaClientBuilder builder = CamundaClient.newClientBuilder()
                .grpcAddress(URI.create(properties.getGrpcAddress()))
                .restAddress(URI.create(properties.getRestAddress()))
                .preferRestOverGrpc(properties.isPreferRestOverGrpc())
                .defaultRequestTimeout(Duration.ofSeconds(Math.max(10, properties.getRequestTimeoutSeconds())))
                .defaultJobPollInterval(Duration.ofSeconds(Math.max(1, properties.getJobPollIntervalSeconds())))
                .defaultJobWorkerMaxJobsActive(Math.max(1, properties.getWorkerMaxJobsActive()))
                // Multiple execution threads (default is 1): a single thread serializes ALL job handling,
                // so one slow/failing job (e.g. dropping orphan jobs) starves real remediations
                // (ROLLBACK / DELETE) -> they stay PENDING -> timeout. The verify handler BLOCKS its
                // thread with Thread.sleep (up to ~retries x delay = a few minutes) while it waits for
                // the system to stabilise; with 16 concurrent demo incidents that alone can pin every
                // thread. 24 threads keep execute + verify running in parallel under burst load so
                // execute jobs never sit ACTIVE-undrained in Zeebe.
                .numJobWorkerExecutionThreads(24)
                .defaultJobWorkerName("backend-remediator");

        if (properties.getUsername() != null && !properties.getUsername().isBlank()) {
            builder.credentialsProvider(
                    new BasicAuthCredentialsProviderBuilder()
                            .username(properties.getUsername())
                            .password(properties.getPassword())
                            .build()
            );
        }

        return builder.build();
    }
}
