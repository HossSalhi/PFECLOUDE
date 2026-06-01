package com.orange.pfe.backend.dto.auth;

public record LoginResponse(
        boolean requires2fa,
        String tempToken,
        String accessToken,
        String refreshToken,
        String username,
        String role,
        String qrCodeDataUri,
        String firstName,
        String lastName
) {
    public static LoginResponse pending2fa(String tempToken, String username, String qrCodeDataUri) {
        return new LoginResponse(true, tempToken, null, null, username, null, qrCodeDataUri, null, null);
    }

    public static LoginResponse authenticated(String accessToken, String refreshToken,
                                               String username, String role,
                                               String firstName, String lastName) {
        return new LoginResponse(false, null, accessToken, refreshToken, username, role, null, firstName, lastName);
    }
}
