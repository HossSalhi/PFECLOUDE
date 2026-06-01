package com.orange.pfe.backend.service;

import jakarta.mail.MessagingException;
import jakarta.mail.internet.MimeMessage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.mail.javamail.MimeMessageHelper;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

@Service
public class UserAuthEmailService {

    private static final Logger log = LoggerFactory.getLogger(UserAuthEmailService.class);

    @Autowired(required = false)
    private JavaMailSender mailSender;

    @Value("${notification.email.from:aiops-noreply@orange.com}")
    private String emailFrom;

    @Value("${notification.email.dashboard-url:http://192.168.148.153:30880}")
    private String dashboardUrl;

    @Async
    public void sendActivationEmail(String toEmail, String username, String firstName, String activationToken) {
        String activationLink = dashboardUrl + "/activate?token=" + activationToken;
        String displayName = (firstName != null && !firstName.isBlank()) ? firstName : username;

        String subject = "[AIOps Orange] Activez votre compte";
        String body = buildHtml(
                "Activez votre compte",
                "#ff7900",
                String.format("""
                    <p style="color:#555;">Bonjour <strong>%s</strong>,</p>
                    <p style="color:#555;">Votre compte a été créé sur la plateforme AIOps Orange. Cliquez sur le bouton ci-dessous pour activer votre compte.</p>
                    <p style="color:#888;font-size:12px;">Ce lien est valable 24 heures.</p>
                    <div style="margin:24px 0;text-align:center;">
                      <a href="%s" style="background:#ff7900;color:white;padding:12px 32px;border-radius:4px;text-decoration:none;font-weight:bold;font-size:15px;">
                        Activer mon compte
                      </a>
                    </div>
                    <p style="color:#aaa;font-size:11px;">Ou copiez ce lien : <br>%s</p>
                    """,
                        escapeHtml(displayName), activationLink, activationLink)
        );

        if (send(toEmail, subject, body)) {
            log.info("Activation email sent to {} (user={})", toEmail, username);
        } else {
            log.warn("Activation email NOT sent — token for {} : {}", username, activationToken);
        }
    }

    @Async
    public void sendPasswordResetEmail(String toEmail, String username, String firstName, String resetToken) {
        String resetLink = dashboardUrl + "/reset-password?token=" + resetToken;
        String displayName = (firstName != null && !firstName.isBlank()) ? firstName : username;

        String subject = "[AIOps Orange] Réinitialisation de votre mot de passe";
        String body = buildHtml(
                "Réinitialisation du mot de passe",
                "#343a40",
                String.format("""
                    <p style="color:#555;">Bonjour <strong>%s</strong>,</p>
                    <p style="color:#555;">Vous avez demandé une réinitialisation de mot de passe. Cliquez sur le bouton ci-dessous.</p>
                    <p style="color:#888;font-size:12px;">Ce lien expire dans <strong>1 heure</strong>. Si vous n'avez pas fait cette demande, ignorez cet email.</p>
                    <div style="margin:24px 0;text-align:center;">
                      <a href="%s" style="background:#343a40;color:white;padding:12px 32px;border-radius:4px;text-decoration:none;font-weight:bold;font-size:15px;">
                        Réinitialiser mon mot de passe
                      </a>
                    </div>
                    <p style="color:#aaa;font-size:11px;">Ou copiez ce lien : <br>%s</p>
                    """,
                        escapeHtml(displayName), resetLink, resetLink)
        );

        if (send(toEmail, subject, body)) {
            log.info("Password reset email sent to {} (user={})", toEmail, username);
        } else {
            log.warn("Password reset email NOT sent — token for {} : {}", username, resetToken);
        }
    }

    @Async
    public void sendWelcomeEmail(String toEmail, String username, String firstName) {
        String displayName = (firstName != null && !firstName.isBlank()) ? firstName : username;
        String subject = "[AIOps Orange] Bienvenue sur la plateforme";
        String body = buildHtml(
                "Bienvenue sur AIOps Orange",
                "#ff7900",
                String.format("""
                    <p style="color:#555;">Bonjour <strong>%s</strong>,</p>
                    <p style="color:#555;">Votre compte a été activé avec succès. Vous pouvez désormais vous connecter à la plateforme AIOps Orange.</p>
                    <div style="margin:24px 0;text-align:center;">
                      <a href="%s/login" style="background:#ff7900;color:white;padding:12px 32px;border-radius:4px;text-decoration:none;font-weight:bold;font-size:15px;">
                        Se connecter
                      </a>
                    </div>
                    """,
                        escapeHtml(displayName), dashboardUrl)
        );
        send(toEmail, subject, body);
    }

    // ── Private ──────────────────────────────────────────────────────────────

    private boolean send(String to, String subject, String htmlBody) {
        if (mailSender == null) {
            log.debug("JavaMailSender not configured — skipping email to {}", to);
            return false;
        }
        try {
            MimeMessage msg = mailSender.createMimeMessage();
            MimeMessageHelper helper = new MimeMessageHelper(msg, true, "UTF-8");
            helper.setFrom(emailFrom);
            helper.setTo(to);
            helper.setSubject(subject);
            helper.setText(htmlBody, true);
            mailSender.send(msg);
            return true;
        } catch (MessagingException e) {
            log.warn("Failed to send email to {} — {}", to, e.getMessage());
            return false;
        } catch (Exception e) {
            log.warn("Email error for {} — {}", to, e.getMessage());
            return false;
        }
    }

    private String buildHtml(String title, String headerColor, String content) {
        return String.format("""
            <!DOCTYPE html>
            <html><head><meta charset="UTF-8"></head>
            <body style="font-family:Arial,sans-serif;background:#f4f4f4;margin:0;padding:20px;">
              <div style="max-width:560px;margin:0 auto;background:white;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
                <div style="background:%s;padding:20px 24px;">
                  <h2 style="color:white;margin:0;font-size:18px;">%s</h2>
                  <p style="color:rgba(255,255,255,0.85);margin:4px 0 0;font-size:13px;">Plateforme AIOps — Orange</p>
                </div>
                <div style="padding:24px;">%s</div>
                <div style="background:#f8f9fa;padding:12px 24px;border-top:1px solid #dee2e6;">
                  <p style="color:#999;font-size:11px;margin:0;text-align:center;">
                    Cet email a été envoyé automatiquement — ne pas répondre.
                  </p>
                </div>
              </div>
            </body></html>
            """, headerColor, escapeHtml(title), content);
    }

    private String escapeHtml(String text) {
        if (text == null) return "";
        return text.replace("&", "&amp;").replace("<", "&lt;")
                   .replace(">", "&gt;").replace("\"", "&quot;");
    }
}
