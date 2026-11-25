package com.orange.pfe.backend.service.audit;

import com.orange.pfe.backend.model.audit.AuditAction;
import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/** Aucun mot de passe, token ou secret ne doit jamais atteindre le journal d'audit. */
class AuditSanitizerTest {

    @Test
    void masksSensitiveKeysWhateverTheirCase() {
        Map<String, Object> in = new HashMap<>();
        in.put("password", "hunter2");
        in.put("newPassword", "hunter3");
        in.put("Authorization", "Bearer abc");
        in.put("api-key", "k-123");
        in.put("accessToken", "t-123");
        in.put("twoFactorSecret", "JBSWY3DPEHPK3PXP");
        in.put("totp", "123456");
        in.put("passwordHash", "$2a$12$xyz");
        in.put("username", "alice");

        Map<String, Object> out = AuditSanitizer.sanitize(in);

        assertThat(out.get("username")).isEqualTo("alice");
        for (String key : List.of("password", "newPassword", "Authorization", "api-key", "accessToken",
                "twoFactorSecret", "totp", "passwordHash")) {
            assertThat(out.get(key)).as(key).isEqualTo(AuditSanitizer.MASK);
        }
    }

    @Test
    void masksJwtLikeValuesEvenUnderAHarmlessKey() {
        String jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJl";
        Map<String, Object> out = AuditSanitizer.sanitize(Map.of("note", jwt, "other", "Bearer " + jwt));
        assertThat(out.get("note")).isEqualTo(AuditSanitizer.MASK);
        assertThat(out.get("other")).isEqualTo(AuditSanitizer.MASK);
    }

    @Test
    void masksNestedValuesAndListsButKeepsOrdinaryData() {
        Map<String, Object> inner = new HashMap<>();
        inner.put("password", "p");
        inner.put("name", "équipe");
        Map<String, Object> out = AuditSanitizer.sanitize(Map.of("user", inner, "items", List.of("a", "b")));

        @SuppressWarnings("unchecked")
        Map<String, Object> user = (Map<String, Object>) out.get("user");
        assertThat(user.get("password")).isEqualTo(AuditSanitizer.MASK);
        assertThat(user.get("name")).isEqualTo("équipe");
        assertThat(out.get("items")).isEqualTo(List.of("a", "b"));
    }

    @Test
    void convertsEnumsTruncatesLongTextAndKeepsNullsOutOfTheWay() {
        Map<String, Object> in = new HashMap<>();
        in.put("action", AuditAction.CREATE);
        in.put("long", "x".repeat(5000));
        in.put("nothing", null);
        Map<String, Object> out = AuditSanitizer.sanitize(in);

        assertThat(out.get("action")).isEqualTo("CREATE");
        assertThat((String) out.get("long")).hasSize(1001).endsWith("…");
        assertThat(out.get("nothing")).isNull();
        assertThat(AuditSanitizer.sanitize(null)).isEmpty();
    }

    @Test
    void jsonNeverContainsTheSecretAndIsNullWhenThereIsNothingToStore() {
        String json = AuditSanitizer.toJson(Map.of("password", "hunter2", "name", "A"));
        assertThat(json).contains("\"name\":\"A\"").contains(AuditSanitizer.MASK).doesNotContain("hunter2");
        assertThat(AuditSanitizer.toJson(Map.of())).isNull();
        assertThat(AuditSanitizer.toJson(null)).isNull();
    }
}
