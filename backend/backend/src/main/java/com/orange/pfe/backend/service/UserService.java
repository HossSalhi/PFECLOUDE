package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.user.Enable2faResponse;
import com.orange.pfe.backend.dto.user.UserRequest;
import com.orange.pfe.backend.dto.user.UserResponse;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;

import java.util.List;

public interface UserService {

    List<UserResponse> findAll();

    UserResponse findById(Long id);

    UserResponse create(UserRequest request, String createdBy);

    UserResponse update(Long id, UserRequest request, String updatedBy);

    void delete(Long id);

    UserResponse setEnabled(Long id, boolean enabled);

    UserResponse setRole(Long id, UserRole role);

    /** Block a user immediately — all active JWTs stop working on the next request */
    UserResponse blockUser(Long id, String reason, String blockedBy);

    /** Unblock a user — clears block fields and re-enables the account */
    UserResponse unblockUser(Long id);

    /** Generates a new TOTP secret and returns the QR code — does NOT activate 2FA yet */
    Enable2faResponse setup2fa(Long id);

    /** Confirms 2FA setup by verifying the first code */
    UserResponse confirm2fa(Long id, String code);

    /** Disables 2FA and wipes the stored secret */
    UserResponse disable2fa(Long id);

    /** Admin password reset (no current-password check) */
    void adminResetPassword(Long id, String newPassword, String resetBy);

    /** User self change-password (requires current password) */
    void changePassword(AppUser user, String currentPassword, String newPassword);

    /** User self change-email (requires current password for verification) */
    void changeEmail(AppUser user, String currentPassword, String newEmail);
}
