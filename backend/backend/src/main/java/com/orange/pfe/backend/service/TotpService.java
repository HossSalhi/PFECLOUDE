package com.orange.pfe.backend.service;

import dev.samstevens.totp.code.*;
import dev.samstevens.totp.exceptions.QrGenerationException;
import dev.samstevens.totp.qr.QrData;
import dev.samstevens.totp.qr.QrGenerator;
import dev.samstevens.totp.qr.ZxingPngQrGenerator;
import dev.samstevens.totp.secret.DefaultSecretGenerator;
import dev.samstevens.totp.secret.SecretGenerator;
import dev.samstevens.totp.time.SystemTimeProvider;
import dev.samstevens.totp.time.TimeProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import static dev.samstevens.totp.util.Utils.getDataUriForImage;

@Service
public class TotpService {

    private static final Logger log = LoggerFactory.getLogger(TotpService.class);

    @Value("${totp.issuer:AIOps-Orange}")
    private String issuer;

    private final SecretGenerator secretGenerator = new DefaultSecretGenerator(32);
    private final QrGenerator qrGenerator = new ZxingPngQrGenerator();
    private final TimeProvider timeProvider = new SystemTimeProvider();

    public String generateSecret() {
        return secretGenerator.generate();
    }

    /**
     * Returns a data URI (base64 PNG) for the QR code to scan with Google Authenticator.
     */
    public String generateQrCodeDataUri(String username, String secret) {
        QrData data = new QrData.Builder()
                .label(username)
                .secret(secret)
                .issuer(issuer)
                .algorithm(HashingAlgorithm.SHA1)
                .digits(6)
                .period(30)
                .build();
        try {
            byte[] imageData = qrGenerator.generate(data);
            return getDataUriForImage(imageData, qrGenerator.getImageMimeType());
        } catch (QrGenerationException e) {
            log.warn("QR generation failed for {}: {}", username, e.getMessage());
            return "";
        }
    }

    /**
     * Returns the otpauth:// URI for manual entry in authenticator apps.
     */
    public String getOtpAuthUri(String username, String secret) {
        QrData data = new QrData.Builder()
                .label(username)
                .secret(secret)
                .issuer(issuer)
                .algorithm(HashingAlgorithm.SHA1)
                .digits(6)
                .period(30)
                .build();
        return data.getUri();
    }

    /**
     * Validates a 6-digit TOTP code against the user's secret.
     * Accepts ±1 time window (30s tolerance).
     */
    public boolean verifyCode(String secret, String code) {
        CodeVerifier verifier = new DefaultCodeVerifier(
                new DefaultCodeGenerator(HashingAlgorithm.SHA1),
                timeProvider
        );
        try {
            return verifier.isValidCode(secret, code);
        } catch (Exception e) {
            log.debug("TOTP verification error: {}", e.getMessage());
            return false;
        }
    }
}
