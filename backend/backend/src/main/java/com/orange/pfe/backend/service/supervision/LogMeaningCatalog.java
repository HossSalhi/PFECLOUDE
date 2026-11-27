package com.orange.pfe.backend.service.supervision;

import java.util.List;
import java.util.regex.Pattern;

/**
 * Catalogue DÉTERMINISTE « message de log → signification » : des règles écrites à la main, sans IA. La première règle
 * qui correspond l'emporte ; sans correspondance, le message est classé « AUTRE ».
 */
public final class LogMeaningCatalog {

    private LogMeaningCatalog() {
    }

    public record Meaning(String category, String label, String meaning, String advice) {}

    private record Rule(Pattern pattern, Meaning meaning) {}

    public static final Meaning OTHER = new Meaning("AUTRE", "Erreur non cataloguée",
            "Message d'erreur qui ne correspond à aucune règle connue.", "Lire le message complet et le contexte de la machine concernée.");

    private static Rule rule(String regex, String category, String label, String meaning, String advice) {
        return new Rule(Pattern.compile(regex, Pattern.CASE_INSENSITIVE), new Meaning(category, label, meaning, advice));
    }

    private static final List<Rule> RULES = List.of(
            rule("oomkilled|out of memory|oom-kill|killed process", "MEMOIRE", "Mémoire épuisée",
                    "Un processus ou un conteneur a été arrêté car la mémoire de la machine ou son quota était épuisé.",
                    "Regarder la mémoire de la machine et les limites du conteneur ; relancer et surveiller."),
            rule("no space left on device|disk pressure|evict", "DISQUE", "Disque plein ou pression disque",
                    "Le disque est presque plein : des écritures échouent ou des pods sont évincés.",
                    "Libérer de l'espace (journaux, sauvegardes anciennes) et vérifier le seuil d'alerte disque."),
            rule("took too long to execute|slow fdatasync|failed to send out heartbeat|lost leader|leader changed|etcdserver: (request timed out|leader)",
                    "ETCD", "etcd lent ou instable",
                    "Le magasin du cluster (etcd) répond lentement : le disque ou le processeur de la machine est saturé.",
                    "Vérifier la charge disque et CPU de l'hôte ; éviter tout arrêt de machine tant que cela dure."),
            rule("imagepullbackoff|errimagepull|failed to pull image", "IMAGE", "Image introuvable",
                    "Le cluster n'arrive pas à télécharger l'image d'un conteneur.",
                    "Vérifier le nom de l'image, le registre et l'accès réseau de la machine."),
            rule("crashloopbackoff|back-off restarting failed container", "CRASH", "Conteneur qui redémarre en boucle",
                    "Un conteneur démarre puis s'arrête aussitôt, de façon répétée.",
                    "Lire les journaux du conteneur et le dernier événement du pod."),
            rule("liveness probe failed|readiness probe failed|probe failed", "SONDE", "Sonde de santé en échec",
                    "L'application ne répond pas à son test de santé : elle est lente, bloquée ou en démarrage.",
                    "Vérifier l'application concernée et le délai de la sonde."),
            rule("x509|certificate (has )?(expired|is not valid|invalid)", "CERTIFICAT", "Problème de certificat",
                    "Un certificat est expiré, invalide ou non reconnu.",
                    "Contrôler la date d'expiration et la chaîne de certification."),
            rule("no such host|server misbehaving|dns", "DNS", "Résolution de nom en échec",
                    "Un nom de machine n'a pas pu être résolu en adresse.",
                    "Vérifier le service DNS et la configuration de la machine."),
            rule("connection refused", "RESEAU", "Connexion refusée",
                    "Le service visé n'écoute pas : il est arrêté ou en redémarrage.",
                    "Vérifier que le service cible tourne et écoute sur le bon port."),
            rule("i/o timeout|connection timed out|no route to host|network is unreachable|dial tcp|failed to (connect|dial)", "RESEAU", "Réseau injoignable ou lent",
                    "Un composant n'arrive pas à joindre un autre (réseau coupé, machine éteinte ou service surchargé).",
                    "Vérifier la machine ou le service cible, puis le réseau entre les deux."),
            rule("deadline exceeded|context canceled|timed out", "DELAI", "Délai dépassé",
                    "Une opération a pris trop de temps et a été abandonnée.",
                    "Chercher une surcharge (CPU, disque) sur le composant qui répond."),
            rule("failed password|invalid user|authentication failure|unauthorized|forbidden|permission denied|invalid credentials", "ACCES", "Accès refusé ou authentification en échec",
                    "Une connexion ou un appel a été refusé (identifiants ou droits incorrects).",
                    "Vérifier s'il s'agit d'une erreur de configuration ou d'une tentative d'accès non voulue."),
            rule("segfault|core dumped|panic|fatal", "CRASH", "Arrêt brutal d'un processus",
                    "Un programme s'est arrêté de façon anormale.",
                    "Retrouver le processus concerné et son dernier démarrage.")
    );

    /** Signification d'une ligne de log (jamais null). */
    public static Meaning classify(String line) {
        if (line == null) {
            return OTHER;
        }
        for (Rule r : RULES) {
            if (r.pattern().matcher(line).find()) {
                return r.meaning();
            }
        }
        return OTHER;
    }

    /** Toutes les règles, pour l'affichage du catalogue (la règle « AUTRE » en dernier). */
    public static List<Meaning> all() {
        List<Meaning> out = new java.util.ArrayList<>(RULES.stream().map(Rule::meaning).toList());
        out.add(OTHER);
        return out;
    }
}
