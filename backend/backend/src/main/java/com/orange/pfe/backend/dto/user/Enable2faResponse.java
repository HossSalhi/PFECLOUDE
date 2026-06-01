package com.orange.pfe.backend.dto.user;

public record Enable2faResponse(
        String secret,
        String qrCodeDataUri,
        String otpAuthUri
) {}
