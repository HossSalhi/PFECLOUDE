package com.orange.pfe.backend.service.supervision;

import com.orange.pfe.backend.config.InfraHealthProperties;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.GrafanaConfig;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.GrafanaDashboard;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/**
 * Dashboards Grafana proposés dans HELIOS. L'intégration (iframe) est DÉSACTIVÉE par défaut : Grafana refuse d'être
 * affiché dans une autre page tant que {@code allow_embedding} n'est pas activé sur sa VM, ce qui est un réglage
 * d'infrastructure à faire par un administrateur (voir {@code docs/SUPERVISION-GRAFANA.md}). Cette classe ne contacte
 * pas Grafana : elle compose seulement des adresses à partir de la configuration.
 *
 * <p>Propriété {@code helios.supervision.grafana.dashboards} : {@code Titre|uid;Titre|uid}. Un uid n'est accepté que
 * s'il ne contient que lettres, chiffres, « _ » et « - » (jamais de caractère pouvant fausser l'adresse).</p>
 */
@Service
public class GrafanaConfigService {

    private static final Pattern UID = Pattern.compile("^[A-Za-z0-9_-]{1,40}$");

    private final InfraHealthProperties infra;

    @Value("${helios.supervision.grafana.embed-enabled:false}")
    private boolean embedEnabled;

    @Value("${helios.supervision.grafana.dashboards:}")
    private String dashboards;

    public GrafanaConfigService(InfraHealthProperties infra) {
        this.infra = infra;
    }

    public GrafanaConfig config() {
        List<GrafanaDashboard> out = new ArrayList<>();
        String base = infra.getGrafanaUrl() == null ? "" : infra.getGrafanaUrl().replaceAll("/+$", "");
        boolean httpBase = base.matches("(?i)^https?://.+");
        if (dashboards != null && !dashboards.isBlank()) {
            for (String entry : dashboards.split(";")) {
                String[] p = entry.split("\\|", 2);
                if (p.length == 2 && UID.matcher(p[1].trim()).matches() && !p[0].isBlank()) {
                    String uid = p[1].trim();
                    out.add(new GrafanaDashboard(p[0].trim(), uid, httpBase ? base + "/d/" + uid + "?orgId=1&kiosk" : null));
                }
            }
        }
        return new GrafanaConfig(embedEnabled && httpBase, out);
    }
}
