package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.human.HumanTask;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
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

import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.stream.Collectors;

/**
 * Central email notification service.
 * Operational emails (incidents, tasks, remédiation) → all active ADMIN + SUPER_ADMIN.
 * Account emails (password, block) → the affected user directly.
 */
@Service
public class EmailNotificationService {

    private static final Logger log = LoggerFactory.getLogger(EmailNotificationService.class);
    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("dd/MM/yyyy HH:mm:ss");

    @Autowired(required = false)
    private JavaMailSender mailSender;

    private final AppUserRepository userRepository;

    @Value("${notification.email.to:}")
    private String fallbackEmailTo;

    @Value("${notification.email.from:helios-noreply@orange.tn}")
    private String emailFrom;

    @Value("${notification.email.min-severity:HIGH}")
    private String minSeverity;

    @Value("${notification.email.dashboard-url:http://192.168.148.153:30880}")
    private String dashboardUrl;

    public EmailNotificationService(AppUserRepository userRepository) {
        this.userRepository = userRepository;
    }

    // ── Incidents ─────────────────────────────────────────────────────────────

    @Async
    public void sendIncidentDetected(Incident incident) {
        if (!shouldNotify(incident.getSeverity())) return;
        String subject = String.format("[HELIOS] Incident %s détecté — %s",
                incident.getSeverity(), incident.getTitle());
        String body = buildIncidentHtml(incident,
                "Nouvel incident détecté par l'IA",
                "#dc3545",
                "Un incident a été détecté automatiquement. Un workflow de remédiation a été lancé.");
        sendToOperators(subject, body);
    }

    @Async
    public void sendApprovalRequired(Incident incident) {
        String subject = String.format("[HELIOS] Approbation requise — Incident #%d", incident.getId());
        String body = buildIncidentHtml(incident,
                "Approbation humaine requise",
                "#FF7900",
                "L'IA a proposé une action de remédiation qui nécessite votre approbation avant exécution.");
        sendToOperators(subject, body);
    }

    @Async
    public void sendHumanTaskCreated(HumanTask task) {
        Incident incident = task.getWorkflowInstance().getIncident();
        String subject = String.format("[HELIOS] Tâche humaine en attente — Incident #%d", incident.getId());
        String taskInfo = String.format("""
            <table style="width:100%%;border-collapse:collapse;margin-top:16px;">
              <tr><td style="padding:8px;font-weight:bold;width:160px;">Tâche</td><td style="padding:8px;">%s</td></tr>
              <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Incident</td><td style="padding:8px;">#%d — %s</td></tr>
              <tr><td style="padding:8px;font-weight:bold;">Sévérité</td>
                  <td style="padding:8px;"><span style="background:%s;color:white;padding:2px 8px;border-radius:4px;">%s</span></td></tr>
              <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Application</td><td style="padding:8px;">%s</td></tr>
              <tr><td style="padding:8px;font-weight:bold;">Namespace</td><td style="padding:8px;">%s</td></tr>
            </table>
            <div style="margin:24px 0;text-align:center;">
              <a href="%s/tasks" style="background:#FF7900;color:white;padding:10px 28px;border-radius:4px;text-decoration:none;font-weight:bold;">
                Gérer les tâches
              </a>
            </div>
            """,
                escapeHtml(task.getTaskName()),
                incident.getId(), escapeHtml(incident.getTitle()),
                severityColor(incident.getSeverity()), incident.getSeverity() != null ? incident.getSeverity().name() : "N/A",
                incident.getApp() != null ? escapeHtml(incident.getApp()) : "N/A",
                incident.getNamespace() != null ? escapeHtml(incident.getNamespace()) : "N/A",
                dashboardUrl
        );
        String body = buildSystemHtml("Tâche humaine en attente de décision", "#FF7900",
                "<p style='color:#555;'>Une nouvelle tâche requiert votre décision (approuver, rejeter ou escalader).</p>" + taskInfo);
        sendToOperators(subject, body);
    }

    @Async
    public void sendHumanTaskDecided(HumanTask task, String actor) {
        Incident incident = task.getWorkflowInstance().getIncident();
        String decisionLabel = switch (task.getDecision()) {
            case APPROVED  -> "Approuvée";
            case REJECTED  -> "Rejetée";
            case ESCALATED -> "Escaladée";
            default        -> task.getDecision().name();
        };
        String decisionColor = switch (task.getDecision()) {
            case APPROVED  -> "#28a745";
            case REJECTED  -> "#6c757d";
            case ESCALATED -> "#dc3545";
            default        -> "#FF7900";
        };
        String subject = String.format("[HELIOS] Tâche %s — Incident #%d", decisionLabel, incident.getId());
        String content = String.format("""
            <p style="color:#555;">La tâche <strong>%s</strong> a été <strong style="color:%s;">%s</strong> par <strong>%s</strong>.</p>
            <table style="width:100%%;border-collapse:collapse;margin-top:16px;">
              <tr><td style="padding:8px;font-weight:bold;width:160px;">Incident</td><td style="padding:8px;">#%d — %s</td></tr>
              <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Décision</td>
                  <td style="padding:8px;"><span style="background:%s;color:white;padding:2px 8px;border-radius:4px;">%s</span></td></tr>
              <tr><td style="padding:8px;font-weight:bold;">Par</td><td style="padding:8px;">%s</td></tr>
              <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Date</td>
                  <td style="padding:8px;">%s</td></tr>
            </table>
            """,
                escapeHtml(task.getTaskName()), decisionColor, decisionLabel, escapeHtml(actor),
                incident.getId(), escapeHtml(incident.getTitle()),
                decisionColor, decisionLabel,
                escapeHtml(actor),
                task.getCompletedAt() != null ? task.getCompletedAt().format(FMT) : "N/A"
        );
        String body = buildSystemHtml("Décision tâche humaine enregistrée", decisionColor, content);
        sendToOperators(subject, body);
    }

    @Async
    public void sendIncidentResolved(Incident incident) {
        if (!shouldNotify(incident.getSeverity())) return;
        String subject = String.format("[HELIOS] Incident résolu — %s", incident.getTitle());
        String body = buildIncidentHtml(incident, "Incident résolu avec succès", "#28a745",
                "L'incident a été résolu par le système HELIOS AIOps.");
        sendToOperators(subject, body);
    }

    @Async
    public void sendRemediationFailed(Incident incident, String reason) {
        String subject = String.format("[HELIOS] Remédiation échouée — Incident #%d", incident.getId());
        String body = buildIncidentHtml(incident, "Échec de la remédiation automatique", "#dc3545",
                "La remédiation automatique a échoué. Intervention manuelle requise.<br><br>" +
                "<strong>Raison :</strong> " + escapeHtml(reason));
        sendToOperators(subject, body);
    }

    @Async
    public void sendRemediationExecuted(Incident incident, String actionType, String target) {
        String subject = String.format("[HELIOS] Remédiation automatique — Incident #%d", incident.getId());
        String content = String.format("""
            <p style="color:#555;">Une remédiation automatique a été exécutée sur l'infrastructure.</p>
            <table style="width:100%%;border-collapse:collapse;margin-top:16px;">
              <tr><td style="padding:8px;font-weight:bold;width:160px;">Incident</td><td style="padding:8px;">#%d — %s</td></tr>
              <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Action</td><td style="padding:8px;">%s</td></tr>
              <tr><td style="padding:8px;font-weight:bold;">Cible</td><td style="padding:8px;">%s</td></tr>
              <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Sévérité</td>
                  <td style="padding:8px;"><span style="background:%s;color:white;padding:2px 8px;border-radius:4px;">%s</span></td></tr>
            </table>
            """,
                incident.getId(), escapeHtml(incident.getTitle()),
                escapeHtml(actionType), escapeHtml(target),
                severityColor(incident.getSeverity()), incident.getSeverity() != null ? incident.getSeverity().name() : "N/A"
        );
        String body = buildSystemHtml("Remédiation automatique exécutée", "#17a2b8", content);
        sendToOperators(subject, body);
    }

    @Async
    public void sendFineTuningCompleted(String status, int incidents, double f1Score) {
        String subject = "[HELIOS] Fine-tuning du modèle IA terminé — " + status;
        String body = buildSystemHtml("Fine-tuning terminé", "#17a2b8",
                String.format("""
                    <table style="width:100%%;border-collapse:collapse;">
                      <tr><td style="padding:8px;font-weight:bold;">Statut</td><td style="padding:8px;">%s</td></tr>
                      <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Incidents traités</td><td style="padding:8px;">%d</td></tr>
                      <tr><td style="padding:8px;font-weight:bold;">Score F1</td><td style="padding:8px;">%.4f</td></tr>
                    </table>
                """, escapeHtml(status), incidents, f1Score));
        sendToOperators(subject, body);
    }

    // ── Compte utilisateur (envoi à l'utilisateur concerné) ──────────────────

    @Async
    public void sendPasswordChangedConfirmation(AppUser user) {
        String displayName = displayName(user);
        String subject = "[HELIOS] Votre mot de passe a été modifié";
        String body = buildSystemHtml("Mot de passe modifié", "#343a40",
                String.format("""
                    <p style="color:#555;">Bonjour <strong>%s</strong>,</p>
                    <p style="color:#555;">Votre mot de passe a été modifié avec succès le <strong>%s</strong>.</p>
                    <p style="color:#888;">Si vous n'êtes pas à l'origine de cette modification, contactez immédiatement votre administrateur.</p>
                    """,
                        escapeHtml(displayName),
                        java.time.LocalDateTime.now().format(FMT)));
        sendTo(user.getEmail(), subject, body);
    }

    @Async
    public void sendEmailChangedConfirmation(AppUser user, String oldEmail) {
        String displayName = displayName(user);
        String subject = "[HELIOS] Votre adresse email a été modifiée";
        String content = String.format("""
                <p style="color:#555;">Bonjour <strong>%s</strong>,</p>
                <p style="color:#555;">Votre adresse email a été modifiée :</p>
                <table style="width:100%%;border-collapse:collapse;margin-top:12px;">
                  <tr><td style="padding:8px;font-weight:bold;">Ancien email</td><td style="padding:8px;">%s</td></tr>
                  <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Nouvel email</td><td style="padding:8px;">%s</td></tr>
                </table>
                <p style="color:#888;margin-top:12px;">Si vous n'êtes pas à l'origine de cette modification, contactez votre administrateur.</p>
                """,
                escapeHtml(displayName), escapeHtml(oldEmail), escapeHtml(user.getEmail()));
        String body = buildSystemHtml("Email modifié", "#343a40", content);
        // Notify both old and new email
        sendTo(oldEmail, subject, body);
        if (!oldEmail.equalsIgnoreCase(user.getEmail())) {
            sendTo(user.getEmail(), subject, body);
        }
    }

    @Async
    public void sendAccountCreatedByAdmin(AppUser user, String rawPassword, String createdBy) {
        String displayName = displayName(user);
        String subject = "[HELIOS] Votre compte a été créé";
        String body = buildSystemHtml("Compte créé par un administrateur", "#FF7900",
                String.format("""
                    <p style="color:#555;">Bonjour <strong>%s</strong>,</p>
                    <p style="color:#555;">Un compte a été créé pour vous sur la plateforme HELIOS AIOps par <strong>%s</strong>.</p>
                    <table style="width:100%%;border-collapse:collapse;margin-top:16px;">
                      <tr><td style="padding:8px;font-weight:bold;width:160px;">Identifiant</td><td style="padding:8px;">%s</td></tr>
                      <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Mot de passe</td><td style="padding:8px;font-family:monospace;">%s</td></tr>
                      <tr><td style="padding:8px;font-weight:bold;">Rôle</td><td style="padding:8px;">%s</td></tr>
                    </table>
                    <p style="color:#dc3545;font-size:13px;margin-top:12px;">Veuillez changer votre mot de passe dès la première connexion.</p>
                    <div style="margin:24px 0;text-align:center;">
                      <a href="%s/login" style="background:#FF7900;color:white;padding:10px 28px;border-radius:4px;text-decoration:none;font-weight:bold;">
                        Se connecter
                      </a>
                    </div>
                    """,
                        escapeHtml(displayName), escapeHtml(createdBy),
                        escapeHtml(user.getUsername()), escapeHtml(rawPassword),
                        user.getRole().name(), dashboardUrl));
        sendTo(user.getEmail(), subject, body);
    }

    @Async
    public void sendAdminPasswordReset(AppUser user, String newPassword, String resetBy) {
        String displayName = displayName(user);
        String subject = "[HELIOS] Votre mot de passe a été réinitialisé par un administrateur";
        String body = buildSystemHtml("Réinitialisation du mot de passe", "#343a40",
                String.format("""
                    <p style="color:#555;">Bonjour <strong>%s</strong>,</p>
                    <p style="color:#555;">Votre mot de passe a été réinitialisé par l'administrateur <strong>%s</strong>.</p>
                    <table style="width:100%%;border-collapse:collapse;margin-top:16px;">
                      <tr><td style="padding:8px;font-weight:bold;">Nouveau mot de passe</td>
                          <td style="padding:8px;font-family:monospace;">%s</td></tr>
                    </table>
                    <p style="color:#dc3545;font-size:13px;margin-top:12px;">Changez ce mot de passe immédiatement après connexion.</p>
                    <div style="margin:24px 0;text-align:center;">
                      <a href="%s/login" style="background:#343a40;color:white;padding:10px 28px;border-radius:4px;text-decoration:none;font-weight:bold;">
                        Se connecter
                      </a>
                    </div>
                    """,
                        escapeHtml(displayName), escapeHtml(resetBy),
                        escapeHtml(newPassword), dashboardUrl));
        sendTo(user.getEmail(), subject, body);
    }

    @Async
    public void sendAccountBlocked(AppUser user, String reason, String blockedBy) {
        String displayName = displayName(user);
        String subject = "[HELIOS] Votre compte a été bloqué";
        String body = buildSystemHtml("Compte bloqué", "#dc3545",
                String.format("""
                    <p style="color:#555;">Bonjour <strong>%s</strong>,</p>
                    <p style="color:#555;">Votre compte a été <strong style="color:#dc3545;">bloqué</strong> par <strong>%s</strong>.</p>
                    <table style="width:100%%;border-collapse:collapse;margin-top:16px;">
                      <tr><td style="padding:8px;font-weight:bold;">Raison</td><td style="padding:8px;">%s</td></tr>
                      <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Date</td>
                          <td style="padding:8px;">%s</td></tr>
                    </table>
                    <p style="color:#888;margin-top:12px;">Contactez votre administrateur pour plus d'informations.</p>
                    """,
                        escapeHtml(displayName), escapeHtml(blockedBy),
                        escapeHtml(reason), java.time.LocalDateTime.now().format(FMT)));
        sendTo(user.getEmail(), subject, body);
    }

    @Async
    public void sendAccountUnblocked(AppUser user) {
        String displayName = displayName(user);
        String subject = "[HELIOS] Votre compte a été débloqué";
        String body = buildSystemHtml("Compte débloqué", "#28a745",
                String.format("""
                    <p style="color:#555;">Bonjour <strong>%s</strong>,</p>
                    <p style="color:#555;">Votre compte a été <strong style="color:#28a745;">débloqué</strong>. Vous pouvez à nouveau vous connecter.</p>
                    <div style="margin:24px 0;text-align:center;">
                      <a href="%s/login" style="background:#28a745;color:white;padding:10px 28px;border-radius:4px;text-decoration:none;font-weight:bold;">
                        Se connecter
                      </a>
                    </div>
                    """,
                        escapeHtml(displayName), dashboardUrl));
        sendTo(user.getEmail(), subject, body);
    }

    // ── Routing ───────────────────────────────────────────────────────────────

    /** Returns emails of all enabled ADMIN + SUPER_ADMIN users. Falls back to static config. */
    private List<String> getOperationalRecipients() {
        List<String> recipients = userRepository.findByRole(UserRole.ADMIN).stream()
                .filter(AppUser::isEnabled)
                .map(AppUser::getEmail)
                .collect(Collectors.toList());

        userRepository.findByRole(UserRole.SUPER_ADMIN).stream()
                .filter(AppUser::isEnabled)
                .map(AppUser::getEmail)
                .forEach(recipients::add);

        if (recipients.isEmpty() && fallbackEmailTo != null && !fallbackEmailTo.isBlank()) {
            for (String e : fallbackEmailTo.split(",")) {
                String trimmed = e.trim();
                if (!trimmed.isBlank()) recipients.add(trimmed);
            }
        }
        return recipients;
    }

    private void sendToOperators(String subject, String htmlBody) {
        List<String> recipients = getOperationalRecipients();
        if (recipients.isEmpty()) {
            log.debug("No operational recipients configured — skipping: {}", subject);
            return;
        }
        for (String to : recipients) {
            sendTo(to, subject, htmlBody);
        }
    }

    private void sendTo(String to, String subject, String htmlBody) {
        if (mailSender == null) {
            log.debug("JavaMailSender not configured — skipping email to {}", to);
            return;
        }
        if (to == null || to.isBlank()) return;
        try {
            MimeMessage msg = mailSender.createMimeMessage();
            MimeMessageHelper helper = new MimeMessageHelper(msg, true, "UTF-8");
            helper.setFrom(emailFrom);
            helper.setTo(to);
            helper.setSubject(subject);
            helper.setText(htmlBody, true);
            mailSender.send(msg);
            log.info("Email sent → {} | {}", to, subject);
        } catch (MessagingException e) {
            log.warn("Email failed for {} — {}", to, e.getMessage());
        } catch (Exception e) {
            log.warn("Email error for {} — {}", to, e.getMessage());
        }
    }

    // ── HTML builders ─────────────────────────────────────────────────────────

    private String buildIncidentHtml(Incident incident, String title, String color, String message) {
        String createdAt = incident.getCreatedAt() != null ? incident.getCreatedAt().format(FMT) : "N/A";
        String details = String.format("""
            <table style="width:100%%;border-collapse:collapse;margin-top:16px;">
              <tr><td style="padding:8px;font-weight:bold;width:140px;">ID</td><td style="padding:8px;">#%d</td></tr>
              <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Titre</td><td style="padding:8px;">%s</td></tr>
              <tr><td style="padding:8px;font-weight:bold;">Sévérité</td>
                  <td style="padding:8px;"><span style="background:%s;color:white;padding:2px 8px;border-radius:4px;">%s</span></td></tr>
              <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Statut</td><td style="padding:8px;">%s</td></tr>
              <tr><td style="padding:8px;font-weight:bold;">Source</td><td style="padding:8px;">%s</td></tr>
              <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Namespace</td><td style="padding:8px;">%s</td></tr>
              <tr><td style="padding:8px;font-weight:bold;">Application</td><td style="padding:8px;">%s</td></tr>
              <tr style="background:#f8f9fa;"><td style="padding:8px;font-weight:bold;">Détecté le</td><td style="padding:8px;">%s</td></tr>
            </table>
            """,
                incident.getId(), escapeHtml(incident.getTitle()),
                severityColor(incident.getSeverity()), incident.getSeverity() != null ? incident.getSeverity().name() : "N/A",
                incident.getStatus() != null ? incident.getStatus().name() : "N/A",
                incident.getSource() != null ? incident.getSource().name() : "N/A",
                incident.getNamespace() != null ? escapeHtml(incident.getNamespace()) : "N/A",
                incident.getApp() != null ? escapeHtml(incident.getApp()) : "N/A",
                createdAt
        );
        return buildSystemHtml(title, color, "<p style='color:#555;'>" + message + "</p>" + details);
    }

    private String buildSystemHtml(String title, String headerColor, String content) {
        return String.format("""
            <!DOCTYPE html>
            <html><head><meta charset="UTF-8"></head>
            <body style="font-family:Arial,sans-serif;background:#f4f4f4;margin:0;padding:20px;">
              <div style="max-width:600px;margin:0 auto;background:white;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1);">
                <div style="background:%s;padding:20px 24px;">
                  <h2 style="color:white;margin:0;font-size:18px;">%s</h2>
                  <p style="color:rgba(255,255,255,.85);margin:4px 0 0;font-size:13px;">HELIOS AIOps Platform — Orange Tunisie</p>
                </div>
                <div style="padding:24px;">
                  %s
                  <div style="margin-top:24px;text-align:center;">
                    <a href="%s" style="background:#FF7900;color:white;padding:10px 24px;border-radius:4px;text-decoration:none;font-weight:bold;">
                      Ouvrir le Dashboard
                    </a>
                  </div>
                </div>
                <div style="background:#f8f9fa;padding:12px 24px;border-top:1px solid #dee2e6;">
                  <p style="color:#999;font-size:11px;margin:0;text-align:center;">
                    Cet email a été envoyé automatiquement par HELIOS — ne pas répondre.
                  </p>
                </div>
              </div>
            </body></html>
            """, headerColor, escapeHtml(title), content, dashboardUrl);
    }

    // ── Utilities ─────────────────────────────────────────────────────────────

    private boolean shouldNotify(IncidentSeverity severity) {
        if (severity == null) return false;
        return switch (minSeverity.toUpperCase()) {
            case "CRITICAL" -> severity == IncidentSeverity.CRITICAL;
            case "MEDIUM"   -> severity != IncidentSeverity.LOW;
            case "LOW"      -> true;
            default         -> severity == IncidentSeverity.CRITICAL || severity == IncidentSeverity.HIGH;
        };
    }

    private String severityColor(IncidentSeverity s) {
        if (s == null) return "#6c757d";
        return switch (s) {
            case CRITICAL -> "#dc3545";
            case HIGH     -> "#FF7900";
            case MEDIUM   -> "#ffc107";
            case LOW      -> "#28a745";
        };
    }

    private String displayName(AppUser user) {
        if (user.getFirstName() != null && !user.getFirstName().isBlank()) {
            return user.getFirstName() + (user.getLastName() != null ? " " + user.getLastName() : "");
        }
        return user.getUsername();
    }

    private String escapeHtml(String text) {
        if (text == null) return "";
        return text.replace("&", "&amp;").replace("<", "&lt;")
                   .replace(">", "&gt;").replace("\"", "&quot;");
    }
}
