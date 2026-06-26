"""
Live stack e2e tests — require PE + RE + localAI API all running.

These tests exercise the full personal health pipeline:

  simulate_health_push → PE sensors → PE /api/push → RE /api/perceive
  → personal_health_baseline machine fires → perceptualSpace[190:194]
  → /health reports re.health_state
  → POST /chat with health_context=true → response contains health hint
  → POST /graphql updateProcessState (from simulate script)

Phase 4 additions (CareKit + health carry):
  push_carekit_signal → PE sensors [194:197] → PE /api/push → RE
  → medication_adherence machine fires → perceptualSpace[198:202]
  → session_health_context carry persists health state at [202:206]

Run locally (all services must be running):
  # Start PE (port 3004) and RE (port 3000) from RealityEngine_AI
  # Start localAI stack: docker compose up -d  OR  uvicorn main:app ...
  pytest services/api/tests/e2e/test_health_pipeline.py --live -v

Override default URLs:
  LOCALAI_API_URL=http://localhost:4000
  PE_URL=http://localhost:3004
  RE_URL=http://localhost:3000
"""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest

from tests.e2e.conftest import poll_until

# Band normalization constants — mirror reality_bridge.py
_HR_LOW_BPM     = 60.0
_HR_HIGH_BPM    = 100.0
_HRV_OK_MS      = 30.0
_SLEEP_OK_HOURS = 6.5


def _ssl_verify(url: str) -> bool:
    configured = os.getenv("RE_SSL_VERIFY")
    if configured is not None:
        return configured.lower() not in ("false", "0", "no")
    return not url.startswith(
        ("https://localhost", "https://127.0.0.1", "https://host.docker.internal")
    )

_HEALTH_SENSORS = [
    {"sensorId": "localai_health_hr_ok",    "region": {"offset": 186, "length": 1}, "ttlMs": 300_000},
    {"sensorId": "localai_health_hrv_ok",   "region": {"offset": 187, "length": 1}, "ttlMs": 900_000},
    {"sensorId": "localai_health_sleep_ok", "region": {"offset": 188, "length": 1}, "ttlMs": 86_400_000},
]

_HEALTH_MACHINE_NAME = "localai/personal_health_baseline"

_SCENARIOS = {
    "thriving":  (72.0, 45.0, 7.5),
    "balanced":  (75.0, 38.0, 5.5),
    "watch":     (70.0, 18.0, 6.0),
    "attention": (105.0, 25.0, 7.0),
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _band(hr: float, hrv: float, sleep: float) -> tuple[float, float, float]:
    return (
        1.0 if _HR_LOW_BPM <= hr <= _HR_HIGH_BPM else 0.0,
        1.0 if hrv   >= _HRV_OK_MS               else 0.0,
        1.0 if sleep >= _SLEEP_OK_HOURS           else 0.0,
    )


def _ensure_health_sensors(pe_url: str) -> None:
    """Idempotently register health sensors in the PE."""
    r = httpx.get(f"{pe_url}/api/sources", timeout=5, verify=_ssl_verify(pe_url))
    r.raise_for_status()
    existing = {s.get("sensorId") for s in r.json().get("sources", []) if s.get("type") == "sensor"}
    for sensor in _HEALTH_SENSORS:
        if sensor["sensorId"] in existing:
            continue
        httpx.post(
            f"{pe_url}/api/sources",
            json={
                "type": "sensor",
                "name": f"localai/health/{sensor['sensorId'].replace('localai_health_', '')}",
                "region": sensor["region"],
                "active": True,
                "sensorId": sensor["sensorId"],
                "lastValue": [],
                "lastUpdated": None,
                "ttlMs": sensor["ttlMs"],
            },
            timeout=5,
            verify=_ssl_verify(pe_url),
        ).raise_for_status()


def _push_health_scenario(pe_url: str, scenario: str) -> str | None:
    """Write band values to PE and trigger a push. Return decoded health state."""
    hr, hrv, sleep = _SCENARIOS[scenario]
    hr_ok, hrv_ok, sleep_ok = _band(hr, hrv, sleep)
    for sid, val in [
        ("localai_health_hr_ok",    hr_ok),
        ("localai_health_hrv_ok",   hrv_ok),
        ("localai_health_sleep_ok", sleep_ok),
    ]:
        httpx.post(
            f"{pe_url}/api/sensors/{sid}",
            json={"values": [val]},
            timeout=5,
            verify=_ssl_verify(pe_url),
        ).raise_for_status()

    push_r = httpx.post(f"{pe_url}/api/push", timeout=10, verify=_ssl_verify(pe_url))
    push_r.raise_for_status()
    ps = push_r.json().get("step", {}).get("perceptualSpace", [])
    return _decode_state(ps)


def _decode_state(ps: list) -> str | None:
    def s(i: int) -> float: return ps[i] if len(ps) > i else 0.0  # noqa: E731
    if s(190) >= 0.5:
        return "thriving"
    if s(191) >= 0.5:
        return "balanced"
    if s(192) >= 0.5:
        return "watch"
    if s(193) >= 0.5:
        return "attention"
    return None


def _health_state_from_api(api_url: str) -> str | None:
    """Read the current health state from the localAI /health endpoint."""
    try:
        r = httpx.get(f"{api_url}/health", timeout=5)
        return r.json().get("services", {}).get("re", {}).get("health_state")
    except Exception:
        return None


# ── Sensor registration ───────────────────────────────────────────────────────


@pytest.mark.live
def test_health_sensors_register_in_pe(live_pe: str) -> None:
    """All three health sensors must be successfully registered in the PE."""
    _ensure_health_sensors(live_pe)
    r = httpx.get(f"{live_pe}/api/sources", timeout=5, verify=_ssl_verify(live_pe))
    r.raise_for_status()
    existing = {s.get("sensorId") for s in r.json().get("sources", []) if s.get("type") == "sensor"}
    for sensor in _HEALTH_SENSORS:
        assert sensor["sensorId"] in existing, (
            f"Sensor {sensor['sensorId']} not found in PE after registration"
        )


# ── Machine import ────────────────────────────────────────────────────────────


@pytest.mark.live
def test_health_machine_imported_in_re(live_api: str, live_re: str) -> None:
    """
    The startup lifespan should have imported personal_health_baseline into the RE.
    Verify it's present by listing machines.
    """
    r = httpx.get(f"{live_re}/api/machines", timeout=5, verify=_ssl_verify(live_re))
    r.raise_for_status()
    names = {m.get("name") for m in r.json().get("machines", [])}
    assert _HEALTH_MACHINE_NAME in names, (
        f"{_HEALTH_MACHINE_NAME!r} not found in RE. "
        f"Did the localAI API start and run import_health_machines()? "
        f"Found: {sorted(names)}"
    )


# ── Push → RE → decode ───────────────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.parametrize("scenario", ["thriving", "balanced", "watch", "attention"])
def test_health_push_fires_correct_re_state(live_pe: str, scenario: str) -> None:
    """
    Push a health scenario to the PE, verify the RE fires the expected state.
    Tests the core PE→RE→CES pipeline for each health classification.
    """
    _ensure_health_sensors(live_pe)
    state = _push_health_scenario(live_pe, scenario)
    assert state == scenario, (
        f"Pushed '{scenario}' scenario but RE decoded state as {state!r}. "
        f"Check personal_health_baseline.json and sensor region alignment."
    )


# ── /health bridge status ─────────────────────────────────────────────────────


@pytest.mark.live
def test_health_endpoint_reports_re_ok_and_health_state(
    live_api: str, live_re: str, live_pe: str
) -> None:
    """After pushing thriving scenario, /health should report re.status=ok and re.health_state=thriving."""
    _ensure_health_sensors(live_pe)
    _push_health_scenario(live_pe, "thriving")

    # Poll /health until re.health_state reflects the push (may lag by 1 RE step)
    def _check():
        r = httpx.get(f"{live_api}/health", timeout=5)
        re_status = r.json().get("services", {}).get("re", {})
        return re_status.get("status") == "ok" and re_status.get("health_state") == "thriving"

    assert poll_until(_check, timeout=30, label="re.health_state=thriving"), (
        f"Timed out waiting for /health to report re.health_state=thriving. "
        f"Last response: {httpx.get(f'{live_api}/health').json()}"
    )


@pytest.mark.live
def test_health_endpoint_pe_status_ok_when_running(live_api: str, live_pe: str) -> None:
    r = httpx.get(f"{live_api}/health", timeout=10)
    pe = r.json()["services"]["pe"]
    assert pe["status"] == "ok", f"Expected pe.status=ok, got: {pe}"
    assert pe.get("sensor_count", 0) >= 0


# ── Chat health context injection ─────────────────────────────────────────────


@pytest.mark.live
def test_chat_with_health_context_enabled_includes_health_hint(
    live_api: str, live_pe: str
) -> None:
    """
    After pushing a 'watch' scenario, a chat request with health_context=true
    should receive a response whose system context mentions HRV or recovery.

    Note: this test requires Ollama to be running (the /chat endpoint invokes the LLM).
    It is skipped if the Ollama service is reported as an error in /health.
    """
    health_r = httpx.get(f"{live_api}/health", timeout=5)
    if "error" in health_r.json().get("services", {}).get("ollama", "error"):
        pytest.skip("Ollama not available — skipping chat test")

    _ensure_health_sensors(live_pe)
    _push_health_scenario(live_pe, "watch")

    r = httpx.post(
        f"{live_api}/chat",
        json={
            "messages": [{"role": "user", "content": "How should I approach my workout today?"}],
            "health_context": True,
        },
        timeout=60,
    )
    assert r.status_code == 200, f"Chat returned {r.status_code}: {r.text[:500]}"
    content = r.json().get("content", "").lower()
    # The health hint for 'watch' mentions HRV/recovery — check for broad indicators
    health_terms = ("hrv", "recovery", "rest", "lighter", "mindful", "health")
    assert any(t in content for t in health_terms), (
        f"Expected health context in chat response, but found none of {health_terms}.\n"
        f"Response: {content[:300]}"
    )


@pytest.mark.live
def test_chat_with_health_context_false_has_no_injection(
    live_api: str, live_pe: str
) -> None:
    """
    With health_context=False, the LLM system prompt must NOT contain a health hint
    regardless of the current RE state. We can't easily verify the system prompt
    in the response, but we can verify the request does not fail and returns content.
    """
    health_r = httpx.get(f"{live_api}/health", timeout=5)
    if "error" in health_r.json().get("services", {}).get("ollama", "error"):
        pytest.skip("Ollama not available — skipping chat test")

    r = httpx.post(
        f"{live_api}/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "health_context": False,
        },
        timeout=60,
    )
    assert r.status_code == 200


# ── Agent health_search tool ──────────────────────────────────────────────────


@pytest.mark.live
def test_agent_health_search_tool_responds(live_api: str) -> None:
    """
    The agent graph has a health_search tool. When the agent is asked a health
    question, it should invoke health_search and return a grounded answer.

    Requires: Ollama running + health_docs collection populated
    (run scripts/ingest_health_docs.py first).
    """
    health_r = httpx.get(f"{live_api}/health", timeout=5)
    if "error" in health_r.json().get("services", {}).get("ollama", "error"):
        pytest.skip("Ollama not available")

    r = httpx.post(
        f"{live_api}/graph/agent",
        json={"messages": [{"role": "user", "content": "What does low HRV mean for recovery?"}]},
        timeout=120,
    )
    assert r.status_code == 200, f"Agent returned {r.status_code}: {r.text[:500]}"
    response = r.json()
    content = response.get("content", response.get("output", "")).lower()
    assert len(content) > 50, f"Agent response too short: {content!r}"


# ── GraphQL trigger ───────────────────────────────────────────────────────────


@pytest.mark.live
def test_health_graphql_trigger_is_recorded(live_api: str, live_pe: str) -> None:
    """
    After pushing a health scenario with the simulate script's GraphQL trigger,
    the event must appear in /graphql/events.
    """
    _ensure_health_sensors(live_pe)

    # Send GraphQL trigger directly (mirrors simulate_health_push.py)
    mutation = """
    mutation UpdateProcessState($input: UpdateProcessStateInput!) {
      updateProcessState(input: $input) {
        processState { id name status }
      }
    }
    """
    variables = {
        "input": {
            "id":            "personal-health-baseline",
            "name":          "Personal Health Baseline",
            "status":        "thriving",
            "ragStatusCode": "GREEN",
            "sourceMachine": _HEALTH_MACHINE_NAME,
            "sourceSequence": "health-thriving",
            "context": json.dumps({"scenario": "e2e_test", "state": "thriving"}),
        }
    }
    r = httpx.post(
        f"{live_api}/graphql",
        json={"query": mutation, "variables": variables},
        timeout=10,
    )
    assert r.status_code == 200, f"GraphQL returned {r.status_code}: {r.text}"

    # Verify it appears in the ring buffer
    events_r = httpx.get(f"{live_api}/graphql/events", timeout=5)
    events = events_r.json().get("events", [])
    assert any(
        e.get("id") == "personal-health-baseline"
        for e in events
    ), f"Event not found in /graphql/events. Events: {events}"


# ── Full pipeline e2e ─────────────────────────────────────────────────────────


@pytest.mark.live
def test_full_health_pipeline_cycle(live_pe: str, live_api: str, live_re: str) -> None:
    """
    Full pipeline smoke test: cycle through all four health states and verify
    each one is correctly reflected in the localAI /health endpoint.

    Step sequence per state:
      1. Write band values to PE sensors.
      2. POST /api/push → RE evaluates personal_health_baseline.
      3. Poll /health until re.health_state matches expected state.
    """
    _ensure_health_sensors(live_pe)

    for scenario in ("thriving", "balanced", "watch", "attention"):
        state = _push_health_scenario(live_pe, scenario)
        assert state == scenario, (
            f"PE→RE push returned {state!r}, expected {scenario!r}"
        )

        def _health_matches(expected: str = scenario) -> bool:
            return _health_state_from_api(live_api) == expected

        reached = poll_until(_health_matches, timeout=20, label=f"health_state={scenario}")
        assert reached, (
            f"Timed out: /health never reported re.health_state={scenario!r}. "
            f"Last: {_health_state_from_api(live_api)!r}"
        )
        time.sleep(0.5)  # brief pause before next state transition


# ── Phase 4: CareKit sensors ──────────────────────────────────────────────────

_CAREKIT_SENSORS = [
    {"sensorId": "localai_carekit_med_adherence",   "offset": 194, "ttlMs": 3_600_000},
    {"sensorId": "localai_carekit_task_completion", "offset": 195, "ttlMs": 86_400_000},
    {"sensorId": "localai_carekit_symptom_ok",      "offset": 196, "ttlMs": 86_400_000},
]
_CAREKIT_MACHINE_NAME = "localai/medication_adherence"
_HEALTH_CARRY_MACHINE_NAME = "localai/session_health_context"

_CAREKIT_SCENARIOS = {
    "adherent": (1.0, 1.0, 1.0),   # med=1.0, task=1.0, symptom_ok=1.0
    "partial":  (1.0, 0.2, 1.0),   # med=1.0, task=0.2 → partial-task
    "lapsed":   (0.2, 0.5, 1.0),   # med=0.2, symptom_ok=1.0 → lapsed
    "concern":  (0.0, 0.3, 0.0),   # med=0.0, symptom_ok=0.0 → concern
}


def _ensure_carekit_sensors(pe_url: str) -> None:
    r = httpx.get(f"{pe_url}/api/sources", timeout=5, verify=_ssl_verify(pe_url))
    r.raise_for_status()
    existing = {s.get("sensorId") for s in r.json().get("sources", []) if s.get("type") == "sensor"}
    for sensor in _CAREKIT_SENSORS:
        if sensor["sensorId"] in existing:
            continue
        httpx.post(
            f"{pe_url}/api/sources",
            json={
                "type": "sensor",
                "name": f"localai/carekit/{sensor['sensorId'].replace('localai_carekit_', '')}",
                "region": {"offset": sensor["offset"], "length": 1},
                "active": True,
                "sensorId": sensor["sensorId"],
                "lastValue": [],
                "lastUpdated": None,
                "ttlMs": sensor["ttlMs"],
            },
            timeout=5,
            verify=_ssl_verify(pe_url),
        ).raise_for_status()


def _push_carekit_scenario(pe_url: str, scenario: str) -> str | None:
    med, task, symp = _CAREKIT_SCENARIOS[scenario]
    for sid, val in [
        ("localai_carekit_med_adherence",   med),
        ("localai_carekit_task_completion", task),
        ("localai_carekit_symptom_ok",      symp),
    ]:
        httpx.post(
            f"{pe_url}/api/sensors/{sid}",
            json={"values": [val]},
            timeout=5,
            verify=_ssl_verify(pe_url),
        ).raise_for_status()

    push_r = httpx.post(f"{pe_url}/api/push", timeout=10, verify=_ssl_verify(pe_url))
    push_r.raise_for_status()
    ps = push_r.json().get("step", {}).get("perceptualSpace", [])
    return _decode_carekit_state(ps)


def _decode_carekit_state(ps: list) -> str | None:
    def s(i: int) -> float: return ps[i] if len(ps) > i else 0.0  # noqa: E731
    if s(198) >= 0.5:
        return "adherent"
    if s(199) >= 0.5:
        return "partial"
    if s(200) >= 0.5:
        return "lapsed"
    if s(201) >= 0.5:
        return "concern"
    return None


def _decode_health_carry(ps: list) -> str | None:
    def s(i: int) -> float: return ps[i] if len(ps) > i else 0.0  # noqa: E731
    if s(202) >= 0.5:
        return "thriving"
    if s(203) >= 0.5:
        return "balanced"
    if s(204) >= 0.5:
        return "watch"
    if s(205) >= 0.5:
        return "attention"
    return None


@pytest.mark.live
def test_carekit_sensors_register_in_pe(live_pe: str) -> None:
    """All three CareKit sensors must be successfully registered in the PE."""
    _ensure_carekit_sensors(live_pe)
    r = httpx.get(f"{live_pe}/api/sources", timeout=5, verify=_ssl_verify(live_pe))
    r.raise_for_status()
    existing = {s.get("sensorId") for s in r.json().get("sources", []) if s.get("type") == "sensor"}
    for sensor in _CAREKIT_SENSORS:
        assert sensor["sensorId"] in existing, (
            f"CareKit sensor {sensor['sensorId']} not found in PE after registration"
        )


@pytest.mark.live
def test_carekit_machine_imported_in_re(live_api: str, live_re: str) -> None:
    """medication_adherence must be imported into the RE by the startup lifespan."""
    r = httpx.get(f"{live_re}/api/machines", timeout=5, verify=_ssl_verify(live_re))
    r.raise_for_status()
    names = {m.get("name") for m in r.json().get("machines", [])}
    assert _CAREKIT_MACHINE_NAME in names, (
        f"{_CAREKIT_MACHINE_NAME!r} not found in RE. "
        f"Did import_carekit_machine() run? Found: {sorted(names)}"
    )


@pytest.mark.live
@pytest.mark.parametrize("scenario", ["adherent", "partial", "lapsed", "concern"])
def test_carekit_push_fires_correct_re_state(live_pe: str, scenario: str) -> None:
    """
    Push a CareKit scenario to the PE, verify the RE fires the expected state.
    Tests the core PE→RE→CES pipeline for each CareKit classification.
    """
    _ensure_carekit_sensors(live_pe)
    state = _push_carekit_scenario(live_pe, scenario)
    assert state == scenario, (
        f"Pushed CareKit '{scenario}' scenario but RE decoded state as {state!r}. "
        f"Check medication_adherence.json and sensor region alignment."
    )


@pytest.mark.live
def test_health_carry_machine_imported_in_re(live_api: str, live_re: str) -> None:
    """session_health_context must be imported into the RE by the startup lifespan."""
    r = httpx.get(f"{live_re}/api/machines", timeout=5, verify=_ssl_verify(live_re))
    r.raise_for_status()
    names = {m.get("name") for m in r.json().get("machines", [])}
    assert _HEALTH_CARRY_MACHINE_NAME in names, (
        f"{_HEALTH_CARRY_MACHINE_NAME!r} not found in RE. "
        f"Did import_session_machines() run? Found: {sorted(names)}"
    )


@pytest.mark.live
def test_health_carry_persists_after_push(live_pe: str, live_re: str) -> None:
    """
    After pushing a health scenario, perceptualSpace[202:206] must be non-zero
    (session_health_context carry latched the state). On a subsequent quiet push
    (no sensor writes), the carry must remain stable — PE carry-forward semantics.
    """
    _ensure_health_sensors(live_pe)

    # Push thriving scenario — this fires personal_health_baseline [190:194]
    # which then fires session_health_context → writes carry at [202:206]
    _push_health_scenario(live_pe, "thriving")

    # Read carry from RE state (bypassing PE push)
    r = httpx.get(
        f"{live_re}/api/perceptual-simulation/state",
        timeout=5,
        verify=_ssl_verify(live_re),
    )
    r.raise_for_status()
    ps = r.json().get("state", {}).get("perceptualSpace", [])
    carry_after_push = _decode_health_carry(ps)
    assert carry_after_push == "thriving", (
        f"Expected carry=thriving after health push, got {carry_after_push!r}. "
        f"Check session_health_context.json fires on personal_health_baseline output."
    )

    # Quiet push — no sensor updates; carry must hold
    quiet_r = httpx.post(
        f"{live_pe}/api/push",
        timeout=10,
        verify=_ssl_verify(live_pe),
    )
    quiet_r.raise_for_status()
    ps_quiet = quiet_r.json().get("step", {}).get("perceptualSpace", [])
    carry_after_quiet = _decode_health_carry(ps_quiet)
    assert carry_after_quiet == "thriving", (
        f"Carry dropped to {carry_after_quiet!r} after quiet push — "
        f"PE carry-forward should hold [202:206] unchanged when no new sensor fires."
    )
