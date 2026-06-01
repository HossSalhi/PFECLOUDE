package com.orange.pfe.backend.dto.auth;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

public record TotpVerifyRequest(
        @NotBlank String tempToken,
        @NotBlank @Pattern(regexp = "\\d{6}", message = "Code TOTP must be 6 digits") String code
) {}
