# -*- coding: utf-8 -*-
"""HELIOS CHAOS-TOOLING-1 - catalogue des 8 stimuli du contrat MVP-25.

AUCUN STIMULUS N'EST INVENTE. Chaque entree est reconstituee depuis une preuve
experimentale des campagnes PHASE2 (2026-08-26) et PHASE3D/3E (2026-08-27) :

  - POD_CRASH_LOOP : le manifeste YAML d'origine a survecu et est reproduit a l'identique
    (stimulus-inj-20260826-pod-crash-loop-0001.yaml) ;
  - les 7 autres : reconstitues depuis le champ `stimulus` de metadata.json et le champ
    `independent_proof` de ground_truth.json, qui decrivent le mecanisme au mot pres.

La provenance de chaque entree est enregistree dans `source_historique` : aucune fiche ne
peut etre ajoutee sans citer sa preuve.

SEPARATION VERITE TERRAIN / DETECTEUR
    `incident_type` est connu AVANT l'injection : c'est le protocole qui le fixe, jamais le
    detecteur. Le catalogue ne lit ni n'importe `detector_type` sous aucune forme.

RECOVERY
    Toutes les campagnes historiques ont utilise la meme action : DELETE_TEST_WORKLOAD
    (33/33 SUCCESS, recovery_verified=true). Le stimulus vit dans un namespace DEDIE, cree
    puis detruit ; supprimer le namespace est donc idempotent et suffit.
"""
from __future__ import annotations

NAMESPACE_JETON = "{{NAMESPACE}}"
NOEUD_JETON = "{{NODE}}"

# Nom du deploiement de test, repris des campagnes historiques.
DEPLOIEMENT_CIBLE = "helios-cible"
DEPLOIEMENT_CRASH = "helios-crash"
SERVICE_CIBLE = "helios-cible"

# --------------------------------------------------------------------------- manifestes
# Chaque manifeste est parametre par le namespace et le noeud ; tout le reste est fige.

_CRASH_LOOP = """# Reproduit a l'identique stimulus-inj-20260826-pod-crash-loop-0001.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: helios-crash
  namespace: {{NAMESPACE}}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: helios-crash
  template:
    metadata:
      labels:
        app: helios-crash
    spec:
      nodeSelector:
        kubernetes.io/hostname: {{NODE}}
      containers:
      - name: crash
        image: busybox:1.36
        imagePullPolicy: IfNotPresent
        command: ["sh","-c","echo demarrage; exit 1"]
        resources:
          limits:
            memory: 32Mi
            cpu: 50m
"""

_CONFIG_ERROR = """# Deployment referencant la ConfigMap inexistante config-absente
apiVersion: apps/v1
kind: Deployment
metadata:
  name: helios-cible
  namespace: {{NAMESPACE}}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: helios-cible
  template:
    metadata:
      labels:
        app: helios-cible
    spec:
      nodeSelector:
        kubernetes.io/hostname: {{NODE}}
      containers:
      - name: cible
        image: busybox:1.36
        imagePullPolicy: IfNotPresent
        command: ["sh","-c","sleep 3600"]
        envFrom:
        - configMapRef:
            name: config-absente
        resources:
          limits:
            memory: 32Mi
            cpu: 50m
"""

_IMAGE_PULL = """# Deployment sur une image inexistante : docker.io/helios/inexistante:v0
apiVersion: apps/v1
kind: Deployment
metadata:
  name: helios-cible
  namespace: {{NAMESPACE}}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: helios-cible
  template:
    metadata:
      labels:
        app: helios-cible
    spec:
      nodeSelector:
        kubernetes.io/hostname: {{NODE}}
      containers:
      - name: cible
        image: docker.io/helios/inexistante:v0
        imagePullPolicy: Always
        resources:
          limits:
            memory: 32Mi
            cpu: 50m
"""

_MEMORY_STRESS = r"""# Deployment allouant de la memoire ANONYME sous limite cgroup 48Mi -> OOMKilled
# STIMULUS-CATALOG-FIX-1 (2026-09-05) : commande et ressources RESTAUREES depuis
# PHASE2-DATASET/inj-20260826-memory-stress-0001/preuves/02-objets.json.
# L'ancienne version ecrivait dans /dev/shm (tmpfs = shmem) : le conteneur etait tue
# a l'init par runc, jamais apres demarrage -> StartError/128 sur 3/3 noeuds, jamais
# OOMKilled. La boucle ci-dessous fait croitre le HEAP DU SHELL : memoire ANONYME,
# conforme a la description et aux preuves du 26-27 aout (OOMKilled/137, 3 noeuds).
apiVersion: apps/v1
kind: Deployment
metadata:
  name: helios-cible
  namespace: {{NAMESPACE}}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: helios-cible
  template:
    metadata:
      labels:
        app: helios-cible
    spec:
      nodeSelector:
        kubernetes.io/hostname: {{NODE}}
      containers:
      - name: c
        image: busybox:1.36
        imagePullPolicy: IfNotPresent
        command:
        - sh
        - -c
        - A=; while true; do A="$A$(head -c 1048576 /dev/zero | tr '\0' 'x')"; done
        resources:
          limits:
            cpu: 100m
            memory: 48Mi
"""

_NETWORK_PARTITION = r"""# Pod appelant en boucle 192.168.148.250 (IP libre du LAN)
# STIMULUS-CATALOG-FIX-2 (2026-09-05) : commande RESTAUREE depuis
# PHASE2-DATASET/inj-20260826-network-partition-0001/preuves/02-objets.json,
# concordante sur 14/14 repertoires historiques.
# L'ancienne version portait -T 5, sleep 5 et surtout `|| echo 'wget: download timed
# out'` : cette sortie SYNTHETIQUE etait emise meme si wget echouait autrement, si bien
# que la ligne de log cessait de prouver l'etat reseau reel. Elle est supprimee : seul
# wget produit desormais son propre message.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: helios-cible
  namespace: {{NAMESPACE}}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: helios-cible
  template:
    metadata:
      labels:
        app: helios-cible
    spec:
      nodeSelector:
        kubernetes.io/hostname: {{NODE}}
      containers:
      - name: c
        image: busybox:1.36
        imagePullPolicy: IfNotPresent
        # Aucune regle iptables, aucun tc, aucune modification reseau du cluster :
        # le pod appelle une IP LIBRE du LAN. La partition est vue DEPUIS le pod,
        # sans toucher au reseau des autres charges.
        command:
        - sh
        - -c
        - while true; do wget -T 3 -q -O- http://192.168.148.250/ ; sleep 2; done
        resources:
          limits:
            cpu: 50m
            memory: 32Mi
"""

_PENDING = """# Deployment demandant 900Gi de memoire : impossible a ordonnancer
apiVersion: apps/v1
kind: Deployment
metadata:
  name: helios-cible
  namespace: {{NAMESPACE}}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: helios-cible
  template:
    metadata:
      labels:
        app: helios-cible
    spec:
      nodeSelector:
        kubernetes.io/hostname: {{NODE}}
      containers:
      - name: cible
        image: busybox:1.36
        imagePullPolicy: IfNotPresent
        command: ["sh","-c","sleep 3600"]
        resources:
          requests:
            memory: 900Gi
"""

_DEPLOYMENT_UNAVAILABLE = """# Deployment 2 replicas dont la readinessProbe echoue toujours
apiVersion: apps/v1
kind: Deployment
metadata:
  name: helios-cible
  namespace: {{NAMESPACE}}
spec:
  replicas: 2
  selector:
    matchLabels:
      app: helios-cible
  template:
    metadata:
      labels:
        app: helios-cible
    spec:
      nodeSelector:
        kubernetes.io/hostname: {{NODE}}
      containers:
      - name: cible
        image: busybox:1.36
        imagePullPolicy: IfNotPresent
        command: ["sh","-c","sleep 3600"]
        readinessProbe:
          exec:
            command: ["sh","-c","exit 1"]
          initialDelaySeconds: 5
          periodSeconds: 5
        resources:
          limits:
            memory: 32Mi
            cpu: 50m
"""

_SERVICE_UNAVAILABLE = """# Service dont le selecteur ne correspond a AUCUN pod : 0 endpoint.
# Cas particulier : le stimulus porte sur le Service / les Endpoints, pas sur un Pod.
# Aucun Deployment n'est cree - c'est precisement ce qui produit 0 endpoint.
apiVersion: v1
kind: Service
metadata:
  name: helios-cible
  namespace: {{NAMESPACE}}
  labels:
    helios/stimulus: service-unavailable
spec:
  selector:
    app: helios-cible-inexistant
  ports:
  - name: http
    port: 80
    targetPort: 8080
"""

# ------------------------------------------------------------------------- catalogue
# `preuve_attendue` decrit la verification INDEPENDANTE du detecteur, reprise mot pour mot
# des champs independent_proof des campagnes historiques.

CATALOGUE = {
    "POD_CRASH_LOOP": {
        "severite": "warning",
        "target_kind": "Deployment",
        "target_name": DEPLOIEMENT_CRASH,
        "manifeste": _CRASH_LOOP,
        "fichier": "stimulus-pod-crash-loop.yaml",
        "source_historique": "manifeste conserve : stimulus-inj-20260826-pod-crash-loop-0001.yaml",
        "stimulus_documente": ("Deployment busybox:1.36 commande sh -c exit 1, 1 replica "
                               "epingle par nodeSelector, limites 32Mi/50m, image en cache"),
        "preuve_attendue": ("kubectl API server : waiting.reason=CrashLoopBackOff, "
                            "restartCount>0, lastState.terminated.exitCode=1"),
        "verification_kubectl": (
            "get pod -n {namespace} -l app=helios-crash -o "
            "jsonpath={{.items[*].status.containerStatuses[*].state.waiting.reason}}"),
        "attendu": "CrashLoopBackOff",
    },
    "POD_CONFIG_ERROR": {
        "severite": "warning",
        "target_kind": "Deployment",
        "target_name": DEPLOIEMENT_CIBLE,
        "manifeste": _CONFIG_ERROR,
        "fichier": "stimulus-pod-config-error.yaml",
        "source_historique": "metadata.json PHASE2 + independent_proof ground_truth.json",
        "stimulus_documente": "Deployment referencant la ConfigMap inexistante config-absente",
        "preuve_attendue": ("kubectl API server : waiting.reason=CreateContainerConfigError "
                            "restartCount=0"),
        "verification_kubectl": (
            "get pod -n {namespace} -l app=helios-cible -o "
            "jsonpath={{.items[*].status.containerStatuses[*].state.waiting.reason}}"),
        "attendu": "CreateContainerConfigError",
    },
    "POD_IMAGE_PULL_BACKOFF": {
        "severite": "warning",
        "target_kind": "Deployment",
        "target_name": DEPLOIEMENT_CIBLE,
        "manifeste": _IMAGE_PULL,
        "fichier": "stimulus-pod-image-pull-backoff.yaml",
        "source_historique": "metadata.json PHASE2 + independent_proof ground_truth.json",
        "stimulus_documente": "Deployment sur une image inexistante docker.io/helios/inexistante:v0",
        "preuve_attendue": "kubectl : waiting.reason=ImagePullBackOff restartCount=0",
        "verification_kubectl": (
            "get pod -n {namespace} -l app=helios-cible -o "
            "jsonpath={{.items[*].status.containerStatuses[*].state.waiting.reason}}"),
        "attendu": "ImagePullBackOff",
    },
    "MEMORY_STRESS": {
        "severite": "warning",
        "target_kind": "Deployment",
        "target_name": DEPLOIEMENT_CIBLE,
        "manifeste": _MEMORY_STRESS,
        "fichier": "stimulus-memory-stress.yaml",
        "source_historique": "metadata.json PHASE2 + independent_proof ground_truth.json",
        "stimulus_documente": ("Deployment allouant de la memoire ANONYME sous limite cgroup "
                               "48Mi -> OOMKilled"),
        "preuve_attendue": ("kubectl : lastState.terminated.reason=OOMKilled restartCount>0. "
                            "Le pod est RECREE : l'ancrage se fait sur le deploiement et le "
                            "namespace, jamais sur le nom exact du pod."),
        "verification_kubectl": (
            "get pod -n {namespace} -l app=helios-cible -o "
            "jsonpath={{.items[*].status.containerStatuses[*].lastState.terminated.reason}}"),
        "attendu": "OOMKilled",
    },
    "NETWORK_PARTITION": {
        "severite": "warning",
        "target_kind": "Deployment",
        "target_name": DEPLOIEMENT_CIBLE,
        "manifeste": _NETWORK_PARTITION,
        "fichier": "stimulus-network-partition.yaml",
        "source_historique": "metadata.json PHASE2 + independent_proof ground_truth.json",
        "stimulus_documente": ("Pod appelant en boucle 192.168.148.250 (IP LIBRE du LAN) : "
                               "No route to host. Aucune regle iptables ni tc : la partition "
                               "est vue DEPUIS le pod, le reseau du cluster n'est pas touche."),
        "preuve_attendue": "kubectl logs : 'wget: download timed out' repete",
        "verification_kubectl": "logs -n {namespace} -l app=helios-cible --tail=20",
        "attendu": "download timed out",
    },
    "POD_PENDING_INSUFFICIENT_RESOURCES": {
        "severite": "warning",
        "target_kind": "Deployment",
        "target_name": DEPLOIEMENT_CIBLE,
        "manifeste": _PENDING,
        "fichier": "stimulus-pod-pending-insufficient-resources.yaml",
        "source_historique": "metadata.json PHASE2 + independent_proof ground_truth.json",
        "stimulus_documente": "Deployment demandant 900Gi de memoire, impossible a ordonnancer",
        "preuve_attendue": "kubectl : status.phase=Pending condition.reason=Unschedulable",
        "verification_kubectl": (
            "get pod -n {namespace} -l app=helios-cible -o "
            "jsonpath={{.items[*].status.conditions[*].reason}}"),
        "attendu": "Unschedulable",
    },
    "DEPLOYMENT_UNAVAILABLE": {
        "severite": "warning",
        "target_kind": "Deployment",
        "target_name": DEPLOIEMENT_CIBLE,
        "manifeste": _DEPLOYMENT_UNAVAILABLE,
        "fichier": "stimulus-deployment-unavailable.yaml",
        "source_historique": "metadata.json PHASE2 + independent_proof ground_truth.json",
        "stimulus_documente": "Deployment 2 replicas dont la readinessProbe echoue toujours",
        "preuve_attendue": "kubectl : unavailableReplicas=2 availableReplicas=0 sur 2 desires",
        "verification_kubectl": (
            "get deployment helios-cible -n {namespace} -o "
            "jsonpath={{.status.unavailableReplicas}}"),
        "attendu": "2",
    },
    "SERVICE_UNAVAILABLE": {
        "severite": "warning",
        # Cas particulier : la cible est un Service, pas un Deployment (phase 12 du lot).
        "target_kind": "Service",
        "target_name": SERVICE_CIBLE,
        "manifeste": _SERVICE_UNAVAILABLE,
        "fichier": "stimulus-service-unavailable.yaml",
        "source_historique": "metadata.json PHASE2 + independent_proof ground_truth.json",
        "stimulus_documente": ("Service dont le selecteur ne correspond a aucun pod : "
                               "0 endpoint. AUCUN Deployment n'est cree - c'est l'absence "
                               "de pod correspondant qui produit le stimulus."),
        "preuve_attendue": ("kubectl : Service helios-cible subsets='<vide>' - aucun "
                            "endpoint sain. La cible est le couple Service/Endpoints."),
        "verification_kubectl": "get endpoints helios-cible -n {namespace} -o jsonpath={{.subsets}}",
        "attendu": "",
    },
}

# Action de recovery, identique pour les 8 classes. Historique : DELETE_TEST_WORKLOAD,
# 33/33 SUCCESS et recovery_verified=true sur les campagnes PHASE3D/3E.
RECOVERY = {
    "action": "DELETE_TEST_WORKLOAD",
    # Supprimer le namespace dedie retire d'un coup le Deployment, le Service et les pods.
    # Idempotent : re-executer sur un namespace absent n'est pas une erreur.
    "commande": "delete namespace {namespace} --ignore-not-found=true --wait=true",
    "verification": [
        "get namespace {namespace} --ignore-not-found=true",      # doit rendre vide
        "get pods -n {namespace} --no-headers",                   # doit rendre vide
        "get nodes --no-headers",                                 # 3/3 Ready attendus
    ],
    "preuve_attendue": ("kubectl hors detecteur : pods cible restants=0, ns restant=0, "
                        "noeuds 3/3 Ready"),
}

# Les 3 noeuds du cluster. Tous portent le control-plane et etcd : les stimuli de ce
# catalogue sont exclusivement au niveau POD, dans un namespace dedie cree puis detruit.
# Aucun stimulus n'agit sur l'hote, le reseau du cluster, etcd ou le control-plane.
NOEUDS = ("k8s-master", "k8s-worker1", "k8s-worker2")


def rendre_manifeste(incident_type, namespace, noeud):
    """Rend le manifeste d'un stimulus pour un namespace et un noeud donnes."""
    if incident_type not in CATALOGUE:
        raise KeyError("classe hors catalogue : %s" % incident_type)
    return (CATALOGUE[incident_type]["manifeste"]
            .replace(NAMESPACE_JETON, namespace)
            .replace(NOEUD_JETON, noeud))


def noeud_pour(position, passe, noeuds=NOEUDS):
    """Rotation deterministe des noeuds (addendum ML5).

    noeud = noeuds[(position + passe) mod 3]. Le decalage par la passe evite qu'une
    meme classe retombe sur le meme noeud a chaque passe : sans lui, le noeud
    redeviendrait un identifiant de classe, exactement le confondant mesure en ML5.
    """
    return noeuds[(position + passe) % len(noeuds)]


def verifier_catalogue():
    """Controles internes. Retourne la liste des problemes ; vide = conforme."""
    problemes = []
    for classe, fiche in CATALOGUE.items():
        for champ in ("severite", "target_kind", "target_name", "manifeste", "fichier",
                      "source_historique", "stimulus_documente", "preuve_attendue",
                      "verification_kubectl", "attendu"):
            if champ not in fiche:
                problemes.append("%s : champ %s manquant" % (classe, champ))
        m = fiche.get("manifeste", "")
        if NAMESPACE_JETON not in m:
            problemes.append("%s : manifeste sans jeton de namespace" % classe)
        # Le Service n'a pas de nodeSelector : il ne s'ordonnance pas.
        if fiche.get("target_kind") == "Deployment" and NOEUD_JETON not in m:
            problemes.append("%s : Deployment sans jeton de noeud" % classe)
        if "detector" in m.lower() or "detector" in fiche["preuve_attendue"].lower():
            problemes.append("%s : reference au detecteur dans le stimulus ou la preuve" % classe)
    return problemes
