"""
Live e2e test for the PatientWellness alert-decline localAIStack workflow.

Run locally:
  RE_SSL_VERIFY=false \
  PE_URL=https://localhost:3004 \
  RE_URL=https://localhost:5001 \
  pytest services/api/tests/e2e/test_patient_wellness_workflow.py --live -v
"""

from __future__ import annotations

import os

import httpx
import pytest

_PATIENT_WELLNESS_MACHINE = "PatientWellness"
_AI_WELLNESS_COACH_MACHINE = "AI Wellness Coach"
_ASSESSMENT_SOURCE = "localai_patient_wellness_assessment"
_FEEDBACK_SOURCE = "localai_patient_wellness_feedback"
_EXPECTED_ALERT = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_EXPECTED_FEEDBACK = [0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0]


def _ssl_verify(url: str) -> bool:
    configured = os.getenv("RE_SSL_VERIFY")
    if configured is not None:
        return configured.lower() not in ("false", "0", "no")
    return not url.startswith(
        ("https://localhost", "https://127.0.0.1", "https://host.docker.internal")
    )


def _close_enough(actual: list[float], expected: list[float]) -> bool:
    return len(actual) == len(expected) and all(
        abs(float(a) - float(e)) < 0.001 for a, e in zip(actual, expected, strict=False)
    )


@pytest.mark.live
def test_patient_wellness_alert_decline_feedback_flow(
    live_api: str,
    live_pe: str,
    live_re: str,
) -> None:
    machines = httpx.get(
        f"{live_re}/api/machines",
        timeout=10,
        verify=_ssl_verify(live_re),
    )
    machines.raise_for_status()
    names = {m.get("name") for m in machines.json().get("machines", [])}
    missing = {
        _PATIENT_WELLNESS_MACHINE,
        _AI_WELLNESS_COACH_MACHINE,
    } - names
    assert not missing, (
        "PatientWellness e2e requires the shared machine corpus loaded into RE. "
        f"Missing: {sorted(missing)}"
    )

    response = httpx.post(
        f"{live_api}/patient-wellness/alert-decline/simulate",
        timeout=60,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["workflow"] == "patient_wellness_alert_decline"
    assert body["sourceSequence"] == "wellness-level2-alert"
    assert body["feedbackSequence"] == "aiwc-alert-escalate"
    assert body["assessmentSourceId"] == _ASSESSMENT_SOURCE
    assert body["feedbackSourceId"] == _FEEDBACK_SOURCE
    assert _close_enough(body["patientWellnessOutput"], _EXPECTED_ALERT)
    assert _close_enough(body["feedbackVector"], _EXPECTED_FEEDBACK)
    assert _close_enough(body["feedbackRegionValue"], _EXPECTED_FEEDBACK)

    feedback = body["normalizedFeedback"]
    assert feedback["classification"] == "ALERT"
    assert feedback["wellnessLevel"] == 2
    assert feedback["severity"] == "MEDIUM"
    assert feedback["multipleDeficits"] == ["anxiety", "stress", "exercise"]
    assert feedback["declineTrend"] is True
    assert feedback["actions"] == [
        "COACH_CLINICAL_ESCALATE",
        "NOTIFY_FAMILY",
        "ACT_REVISE_CARE_PLAN",
    ]

    sources = httpx.get(
        f"{live_pe}/api/sources",
        timeout=10,
        verify=_ssl_verify(live_pe),
    )
    sources.raise_for_status()
    source_ids = {
        source.get("sensorId")
        for source in sources.json().get("sources", [])
        if source.get("type") == "sensor"
    }
    assert {_ASSESSMENT_SOURCE, _FEEDBACK_SOURCE} <= source_ids
