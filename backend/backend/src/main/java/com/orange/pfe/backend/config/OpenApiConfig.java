package com.orange.pfe.backend.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import org.springdoc.core.models.GroupedOpenApi;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class OpenApiConfig {

    // Configuration générale de Swagger / OpenAPI
    @Bean
    public OpenAPI customOpenAPI() {
        return new OpenAPI()
                .info(new Info()
                        .title("Incident Management API")
                        .version("1.0")
                        .description("API professionnelle pour gérer les incidents, alertes, tâches et workflows"));
    }

    // Groupe unique pour afficher tous les endpoints commençant par /api
    @Bean
    public GroupedOpenApi allApis() {
        return GroupedOpenApi.builder()
                .group("all-apis")
                .pathsToMatch("/api/**")  // <-- ici on prend tout ce qui commence par /api
                .build();
    }
}
