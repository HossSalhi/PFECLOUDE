package com.orange.pfe.backend.dto.user;

import com.orange.pfe.backend.model.user.UserRole;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

public record UserRequest(
        @NotBlank @Size(min = 3, max = 50) String username,
        @Email @NotBlank String email,
        @Size(min = 8) String password,
        @NotNull UserRole role,
        @Size(max = 100) String firstName,
        @Size(max = 100) String lastName
) {}
