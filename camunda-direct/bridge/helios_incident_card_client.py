# Client HTTP HELIOS pour les Incident Cards.
# Configuration via HELIOS_BACKEND_URL et HELIOS_BRIDGE_API_KEY.
# Ne lance aucune commande syst?me et ne journalise aucun secret.

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse


class HeliosBackendError(RuntimeError):
    pass


BACKEND_URL = os.environ.get("HELIOS_BACKEND_URL", "").rstrip("/")
BRIDGE_API_KEY = os.environ.get("HELIOS_BRIDGE_API_KEY", "")
TIMEOUT_SECONDS = 8


def _positive_id(incident_id):
    value = int(incident_id)
    if value <= 0:
        raise ValueError("incident_id doit ?tre un entier positif")
    return value


def _post_json(path, payload):
    if not BACKEND_URL:
        raise HeliosBackendError("HELIOS_BACKEND_URL n'est pas configur?e")
    if not BRIDGE_API_KEY:
        raise HeliosBackendError("HELIOS_BRIDGE_API_KEY n'est pas configur?e")

    parsed = urlparse(BACKEND_URL)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HeliosBackendError("HELIOS_BACKEND_URL doit ?tre une URL HTTP(S) valide")

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        BACKEND_URL + path,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-HELIOS-Bridge-Key": BRIDGE_API_KEY,
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except HTTPError as exc:
        # Ne pas renvoyer le corps d'erreur ou les en-t?tes dans les logs.
        raise HeliosBackendError(
            "Backend HELIOS a r?pondu HTTP " + str(exc.code) + " pour " + path
        ) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise HeliosBackendError(
            "Backend HELIOS indisponible (" + type(exc).__name__ + ")"
        ) from None

    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HeliosBackendError("R?ponse JSON invalide du backend HELIOS") from None


def ingest_host_incident(
    incident_type, target, namespace="k8s-nodes", severity=None,
    detected_at=None, observed_at=None, detail=None, episode_key=None
):
    if not incident_type or not str(incident_type).strip():
        raise ValueError("incident_type est obligatoire")
    if not target or not str(target).strip():
        raise ValueError("target est obligatoire")

    payload = {
        "incidentType": incident_type,
        "namespace": namespace,
        "target": target,
        "severity": severity,
        "detectedAt": detected_at,
        "observedAt": observed_at,
        "detail": detail,
        "episodeKey": episode_key,
    }
    return _post_json("/api/incidents/ingest/host", payload)


def record_execution_event(incident_id, stage, action=None, detail=None, success=None):
    incident_id = _positive_id(incident_id)
    allowed = {
        "SELECTED", "REQUESTED", "ACCEPTED", "STARTED",
        "COMPLETED", "FAILED", "VERIFIED",
    }
    stage = str(stage).upper()
    if stage not in allowed:
        raise ValueError("stage d'ex?cution non reconnu")
    if stage == "COMPLETED" and not isinstance(success, bool):
        raise ValueError("COMPLETED doit porter un succ?s explicite true/false")

    return _post_json(
        "/api/incidents/{}/execution-event".format(incident_id),
        {"stage": stage, "action": action, "detail": detail, "success": success},
    )


def collect_evidence(
    incident_id, vm, phase, scenario, outputs, source="BRIDGE"
):
    incident_id = _positive_id(incident_id)
    phase = str(phase).upper()
    if phase not in {"BEFORE", "AFTER"}:
        raise ValueError("phase doit ?tre BEFORE ou AFTER")
    if not isinstance(outputs, list):
        raise ValueError("outputs doit ?tre une liste de sorties d?j? collect?es")

    return _post_json(
        "/api/incidents/{}/evidence/collect".format(incident_id),
        {
            "vm": vm,
            "phase": phase,
            "scenario": scenario,
            "source": source,
            "outputs": outputs,
        },
    )


def record_verification(
    incident_id, criteria, result, measures_json=None,
    reason=None, verified_at=None, available=None
):
    incident_id = _positive_id(incident_id)
    result = str(result).upper()
    if result not in {"PASS", "FAIL"}:
        raise ValueError("result doit ?tre PASS ou FAIL")
    if result == "PASS" and not measures_json:
        raise ValueError("Une v?rification PASS doit fournir les mesures observ?es")

    return _post_json(
        "/api/incidents/{}/verification".format(incident_id),
        {
            "criteria": criteria,
            "result": result,
            "measuresJson": measures_json,
            "reason": reason,
            "verifiedAt": verified_at,
            "available": available,
        },
    )
