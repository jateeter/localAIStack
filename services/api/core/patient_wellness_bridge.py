"""
PatientWellness bridge support for the localAIStack live workflow.

This module owns the localAI side of the PatientWellness alert-decline e2e:
  localAIStack -> PE source [1955:1963] -> PatientWellness -> ALERT [3931:3939]
  -> localAIStack normalized feedback -> PE source [3941:3949].
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from config import get_settings

log = structlog.get_logger()

_SSL_VERIFY: bool | str = os.getenv("RE_SSL_VERIFY", "true").lower() not in (
    "false",
    "0",
    "no",
)

_SENSOR_TIMEOUT = httpx.Timeout(3.0)
_PUSH_TIMEOUT = httpx.Timeout(10.0)

PATIENT_WELLNESS_MACHINE_NAME = "PatientWellness"
AI_WELLNESS_COACH_MACHINE_NAME = "AI Wellness Coach"

PATIENT_WELLNESS_ASSESSMENT_SOURCE_ID = "localai_patient_wellness_assessment"
PATIENT_WELLNESS_FEEDBACK_SOURCE_ID = "localai_patient_wellness_feedback"

PATIENT_WELLNESS_INPUT_REGION = {"offset": 1955, "length": 8}
PATIENT_WELLNESS_OUTPUT_REGION = {"offset": 3931, "length": 8}
AI_WELLNESS_FEEDBACK_REGION = {"offset": 3941, "length": 8}

ALERT_DECLINE_SEQUENCE_ID = "wellness-level2-alert"
AIWELLNESS_ALERT_SEQUENCE_ID = "aiwc-alert-escalate"

PATIENT_WELLNESS_ALERT_VECTOR = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
AIWELLNESS_ALERT_FEEDBACK_VECTOR = [0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0]

_PATIENT_WELLNESS_SOURCES = [
    {
        "sensorId": PATIENT_WELLNESS_ASSESSMENT_SOURCE_ID,
        "name": "localai/patient_wellness/assessment",
        "region": PATIENT_WELLNESS_INPUT_REGION,
        "ttlMs": 300_000,
    },
    {
        "sensorId": PATIENT_WELLNESS_FEEDBACK_SOURCE_ID,
        "name": "localai/patient_wellness/feedback",
        "region": AI_WELLNESS_FEEDBACK_REGION,
        "ttlMs": 300_000,
    },
]

ALERT_DECLINE_STEPS = [
    {
        "id": "alert-mental",
        "label": "Mental Acuity Still In Norm",
        "vector": [0.9, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
    },
    {
        "id": "alert-social",
        "label": "Socialization OK with Anxiety Elevated",
        "vector": [0.9, 0.1, 0.1, 0.9, 0.1, 0.1, 0.1, 0.1],
    },
    {
        "id": "alert-nutrition",
        "label": "Nutrition OK",
        "vector": [0.9, 0.1, 0.1, 0.9, 0.9, 0.1, 0.1, 0.1],
    },
    {
        "id": "alert-decline",
        "label": "Decline Trend Confirmed",
        "vector": [0.9, 0.1, 0.1, 0.9, 0.9, 0.1, 0.9, 0.1],
    },
]

_DEFICIT_DIMENSIONS = {
    1: "anxiety",
    2: "stress",
    5: "exercise",
}


@dataclass(frozen=True)
class WellnessFeedback:
    classification: str
    wellness_level: int
    severity: str
    multiple_deficits: list[str]
    decline_trend: bool
    feedback_vector: list[float]
    feedback_actions: list[str]
    normalized_feedback: dict[str, Any]


def _pe_url() -> str:
    return get_settings().pe_url


def _safe_slice(values: list[float], offset: int, length: int) -> list[float]:
    if len(values) < offset + length:
        return []
    return [float(v) for v in values[offset : offset + length]]


def _get_existing_sensor_ids(client: httpx.Client) -> set[str]:
    resp = client.get(f"{_pe_url()}/api/sources")
    resp.raise_for_status()
    return {
        s.get("sensorId")
        for s in resp.json().get("sources", [])
        if s.get("type") == "sensor" and s.get("sensorId")
    }


def register_patient_wellness_sources() -> bool:
    """Idempotently create the PE sources used by the PatientWellness e2e flow."""
    try:
        with httpx.Client(timeout=_SENSOR_TIMEOUT, verify=_SSL_VERIFY) as client:
            existing_ids = _get_existing_sensor_ids(client)
            for source in _PATIENT_WELLNESS_SOURCES:
                sid = source["sensorId"]
                if sid in existing_ids:
                    continue
                payload = {
                    "type": "sensor",
                    "name": source["name"],
                    "region": source["region"],
                    "active": True,
                    "sensorId": sid,
                    "lastValue": [],
                    "lastUpdated": None,
                    "ttlMs": source["ttlMs"],
                }
                resp = client.post(f"{_pe_url()}/api/sources", json=payload)
                resp.raise_for_status()
                log.info(
                    "patient_wellness.source_registered",
                    sensor_id=sid,
                    region=source["region"],
                )
            return True
    except Exception as exc:
        log.warning(
            "patient_wellness.source_registration_failed",
            error=str(exc),
            pe_url=_pe_url(),
        )
        return False


def _write_sensor(client: httpx.Client, sensor_id: str, values: list[float]) -> None:
    resp = client.post(f"{_pe_url()}/api/sensors/{sensor_id}", json={"values": values})
    resp.raise_for_status()


def _trigger_push(client: httpx.Client) -> dict[str, Any]:
    resp = client.post(f"{_pe_url()}/api/push")
    resp.raise_for_status()
    return resp.json()


def evaluate_alert_decline_feedback(
    assessment_vector: list[float],
    patient_wellness_output: list[float],
) -> WellnessFeedback:
    """
    Normalize the PatientWellness ALERT result into the AIWellnessCoach feedback
    source. This is deterministic scenario support for the live e2e; it does
    not claim clinical AI reasoning beyond recognizing the encoded CES state.
    """
    is_alert = len(patient_wellness_output) > 2 and patient_wellness_output[2] >= 0.5
    decline_trend = len(assessment_vector) > 6 and assessment_vector[6] >= 0.5
    deficits = [
        name
        for idx, name in _DEFICIT_DIMENSIONS.items()
        if len(assessment_vector) > idx and assessment_vector[idx] < 0.5
    ]

    if is_alert and decline_trend and len(deficits) >= 3:
        feedback_vector = AIWELLNESS_ALERT_FEEDBACK_VECTOR
        actions = [
            "COACH_CLINICAL_ESCALATE",
            "NOTIFY_FAMILY",
            "ACT_REVISE_CARE_PLAN",
        ]
        classification = "ALERT"
        wellness_level = 2
        severity = "MEDIUM"
    else:
        feedback_vector = [0.0] * 8
        actions = []
        classification = "UNRESOLVED"
        wellness_level = 0
        severity = "UNKNOWN"

    normalized_feedback = {
        "classification": classification,
        "wellnessLevel": wellness_level,
        "severity": severity,
        "multipleDeficits": deficits,
        "declineTrend": decline_trend,
        "actions": actions,
        "recommendation": (
            "Schedule multidisciplinary care plan review, notify family, and open "
            "a care-plan revision task."
            if classification == "ALERT"
            else "No normalized PatientWellness alert feedback produced."
        ),
    }
    return WellnessFeedback(
        classification=classification,
        wellness_level=wellness_level,
        severity=severity,
        multiple_deficits=deficits,
        decline_trend=decline_trend,
        feedback_vector=feedback_vector,
        feedback_actions=actions,
        normalized_feedback=normalized_feedback,
    )


def simulate_alert_decline_workflow() -> dict[str, Any]:
    """
    Run the live PatientWellness alert-decline workflow through PE and RE, then
    write normalized feedback back through the localAI PE feedback source.
    """
    if not register_patient_wellness_sources():
        raise RuntimeError("PatientWellness PE sources could not be registered")

    push_results: list[dict[str, Any]] = []
    patient_wellness_output: list[float] = []

    with httpx.Client(timeout=_PUSH_TIMEOUT, verify=_SSL_VERIFY) as client:
        for step in ALERT_DECLINE_STEPS:
            vector = [float(v) for v in step["vector"]]
            _write_sensor(client, PATIENT_WELLNESS_ASSESSMENT_SOURCE_ID, vector)
            push = _trigger_push(client)
            ps = push.get("step", {}).get("perceptualSpace", [])
            patient_wellness_output = _safe_slice(
                ps,
                PATIENT_WELLNESS_OUTPUT_REGION["offset"],
                PATIENT_WELLNESS_OUTPUT_REGION["length"],
            )
            push_results.append(
                {
                    "stepId": step["id"],
                    "globalStep": push.get("globalStep"),
                    "patientWellnessOutput": patient_wellness_output,
                }
            )

        final_assessment = [float(v) for v in ALERT_DECLINE_STEPS[-1]["vector"]]
        feedback = evaluate_alert_decline_feedback(
            final_assessment,
            patient_wellness_output,
        )
        _write_sensor(
            client,
            PATIENT_WELLNESS_FEEDBACK_SOURCE_ID,
            feedback.feedback_vector,
        )
        feedback_push = _trigger_push(client)
        ps = feedback_push.get("step", {}).get("perceptualSpace", [])
        feedback_region = _safe_slice(
            ps,
            AI_WELLNESS_FEEDBACK_REGION["offset"],
            AI_WELLNESS_FEEDBACK_REGION["length"],
        )

    return {
        "workflow": "patient_wellness_alert_decline",
        "sourceMachine": PATIENT_WELLNESS_MACHINE_NAME,
        "sourceSequence": ALERT_DECLINE_SEQUENCE_ID,
        "feedbackMachine": AI_WELLNESS_COACH_MACHINE_NAME,
        "feedbackSequence": AIWELLNESS_ALERT_SEQUENCE_ID,
        "assessmentSourceId": PATIENT_WELLNESS_ASSESSMENT_SOURCE_ID,
        "feedbackSourceId": PATIENT_WELLNESS_FEEDBACK_SOURCE_ID,
        "assessmentRegion": PATIENT_WELLNESS_INPUT_REGION,
        "patientWellnessOutputRegion": PATIENT_WELLNESS_OUTPUT_REGION,
        "feedbackRegion": AI_WELLNESS_FEEDBACK_REGION,
        "steps": ALERT_DECLINE_STEPS,
        "pushResults": push_results,
        "patientWellnessOutput": patient_wellness_output,
        "expectedPatientWellnessOutput": PATIENT_WELLNESS_ALERT_VECTOR,
        "feedbackVector": feedback.feedback_vector,
        "feedbackRegionValue": feedback_region,
        "normalizedFeedback": feedback.normalized_feedback,
        "feedbackActions": feedback.feedback_actions,
        "finalGlobalStep": feedback_push.get("globalStep"),
    }
