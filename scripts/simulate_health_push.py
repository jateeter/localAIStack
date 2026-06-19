#!/usr/bin/env python3
"""
Personal health baseline simulation — the localAIStack analog of the Yuma/MQTT demo.

Demonstrates the full loop without an iOS device:

  simulated health readings
    → PE sensor registration (localai_health_hr_ok / hrv_ok / sleep_ok)
    → band normalization (in-range → 1.0, out-of-range → 0.0)
    → PE /api/push  (assembles perceptual vector, calls RE /api/perceive)
    → personal_health_baseline CES machine fires in RE
    → health state decoded from perceptualSpace[190:194]
    → GraphQL trigger posted to localAI (POST /graphql)

Analogous to the Yuma/MQTT end-to-end:
  MQTT broker → mapping registry → PE → RE → CES → governance trigger

This script replaces "MQTT broker" with synthetic health readings and
"mapping registry" with the band-normalization logic in reality_bridge.py.

Usage
-----
  python scripts/simulate_health_push.py [options]

Options
  --pe-url URL        Perception Engine URL  [default: http://localhost:3004]
  --re-url URL        Reality Engine URL     [default: http://localhost:3000]
  --localai-url URL   localAIStack API URL   [default: http://localhost:4000]
  --scenario NAME     One of: thriving, balanced, watch, attention, cycle
                      'cycle' runs all four states in sequence  [default: cycle]
  --interval S        Seconds between cycle steps               [default: 2]
  --no-graphql        Skip the GraphQL trigger to localAI

Health scenarios
  thriving   HR=72, HRV=45ms, Sleep=7.5h   → all three sensors HIGH
  balanced   HR=75, HRV=38ms, Sleep=5.5h   → vitals good, sleep below target
  watch      HR=70, HRV=18ms, Sleep=6.0h   → HR ok, HRV low (poor recovery)
  attention  HR=105, HRV=25ms, Sleep=7.0h  → HR above 100bpm ceiling

These mirror the inputSequences in personal_health_baseline.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import NamedTuple

import httpx

# ── Sensor / machine constants (mirrors reality_bridge.py) ────────────────────

_HR_LOW_BPM     = 60.0
_HR_HIGH_BPM    = 100.0
_HRV_OK_MS      = 30.0
_SLEEP_OK_HOURS = 6.5

_HEALTH_OUTPUT_OFFSET = 190   # [thriving, balanced, watch, attention]

_HEALTH_SENSORS = [
    {
        "sensorId": "localai_health_hr_ok",
        "name": "localai/health/hr_ok",
        "region": {"offset": 186, "length": 1},
        "ttlMs": 300_000,
    },
    {
        "sensorId": "localai_health_hrv_ok",
        "name": "localai/health/hrv_ok",
        "region": {"offset": 187, "length": 1},
        "ttlMs": 900_000,
    },
    {
        "sensorId": "localai_health_sleep_ok",
        "name": "localai/health/sleep_ok",
        "region": {"offset": 188, "length": 1},
        "ttlMs": 86_400_000,
    },
]

# GraphQL trigger template — mirrors the machine triggerConfig pattern
_GRAPHQL_UPDATE = """
mutation UpdateProcessState($input: UpdateProcessStateInput!) {
  updateProcessState(input: $input) {
    processState {
      id
      name
      status
      ragStatus { code description }
    }
  }
}
"""

_STATE_TO_RAG = {
    "thriving":  "GREEN",
    "balanced":  "GREEN",
    "watch":     "AMBER",
    "attention": "RED",
}

_STATE_TO_DESCRIPTION = {
    "thriving":  "All baseline health metrics are in nominal range.",
    "balanced":  "Cardiovascular metrics healthy but sleep is below target.",
    "watch":     "Heart rate nominal but HRV indicates low recovery.",
    "attention": "Heart rate is outside the nominal range. Recommend a health check-in.",
}


class HealthReading(NamedTuple):
    label: str
    hr_bpm: float
    hrv_sdnn_ms: float
    sleep_hours: float
    expected_state: str


_SCENARIOS: dict[str, HealthReading] = {
    "thriving":  HealthReading("All nominal — thriving",            72.0, 45.0, 7.5, "thriving"),
    "balanced":  HealthReading("Good vitals, poor sleep — balanced", 75.0, 38.0, 5.5, "balanced"),
    "watch":     HealthReading("HR ok, HRV low — watch",            70.0, 18.0, 6.0, "watch"),
    "attention": HealthReading("HR out of range — attention",       105.0, 25.0, 7.0, "attention"),
}


# ── PE interaction helpers ────────────────────────────────────────────────────

def _band(hr: float, hrv: float, sleep: float) -> tuple[float, float, float]:
    hr_ok    = 1.0 if _HR_LOW_BPM <= hr <= _HR_HIGH_BPM else 0.0
    hrv_ok   = 1.0 if hrv  >= _HRV_OK_MS               else 0.0
    sleep_ok = 1.0 if sleep >= _SLEEP_OK_HOURS           else 0.0
    return hr_ok, hrv_ok, sleep_ok


def _decode_health_state(ps: list[float]) -> str | None:
    def s(i: int) -> float:
        return ps[i] if len(ps) > i else 0.0
    if s(_HEALTH_OUTPUT_OFFSET)     >= 0.5: return "thriving"
    if s(_HEALTH_OUTPUT_OFFSET + 1) >= 0.5: return "balanced"
    if s(_HEALTH_OUTPUT_OFFSET + 2) >= 0.5: return "watch"
    if s(_HEALTH_OUTPUT_OFFSET + 3) >= 0.5: return "attention"
    return None


def ensure_sensors_registered(pe_url: str) -> None:
    """Register health sensor sources in PE if not already present."""
    with httpx.Client(timeout=5) as client:
        try:
            resp = client.get(f"{pe_url}/api/sources")
            resp.raise_for_status()
            existing = {
                s.get("sensorId")
                for s in resp.json().get("sources", [])
                if s.get("type") == "sensor"
            }
        except Exception as exc:
            print(f"  [warn] Cannot list PE sources: {exc}", file=sys.stderr)
            existing = set()

        for sensor in _HEALTH_SENSORS:
            sid = sensor["sensorId"]
            if sid in existing:
                print(f"  [PE]   sensor already registered: {sid}")
                continue
            payload = {
                "type": "sensor",
                "name": sensor["name"],
                "region": sensor["region"],
                "active": True,
                "sensorId": sid,
                "lastValue": [],
                "lastUpdated": None,
                "ttlMs": sensor["ttlMs"],
            }
            try:
                r = client.post(f"{pe_url}/api/sources", json=payload)
                r.raise_for_status()
                print(f"  [PE]   registered: {sid}  region={sensor['region']}")
            except Exception as exc:
                print(f"  [warn] Could not register {sid}: {exc}", file=sys.stderr)


def push_health_reading(
    pe_url: str,
    reading: HealthReading,
) -> tuple[str | None, int | None]:
    """
    Write band values to PE sensors, trigger /api/push, decode health state.
    Returns (state, global_step).
    """
    hr_ok, hrv_ok, sleep_ok = _band(reading.hr_bpm, reading.hrv_sdnn_ms, reading.sleep_hours)

    print(f"\n  Reading:  HR={reading.hr_bpm}bpm  HRV={reading.hrv_sdnn_ms}ms  "
          f"Sleep={reading.sleep_hours}h")
    print(f"  Bands:    hr.ok={hr_ok}  hrv.ok={hrv_ok}  sleep.ok={sleep_ok}")

    with httpx.Client(timeout=5) as client:
        for sid, val in [
            ("localai_health_hr_ok",    hr_ok),
            ("localai_health_hrv_ok",   hrv_ok),
            ("localai_health_sleep_ok", sleep_ok),
        ]:
            try:
                r = client.post(f"{pe_url}/api/sensors/{sid}", json={"values": [val]})
                r.raise_for_status()
            except Exception as exc:
                print(f"  [warn] sensor write failed ({sid}): {exc}", file=sys.stderr)

        try:
            push_resp = client.post(f"{pe_url}/api/push")
            push_resp.raise_for_status()
            data = push_resp.json()
            ps   = data.get("step", {}).get("perceptualSpace", [])
            step = data.get("globalStep")
            state = _decode_health_state(ps)
            return state, step
        except Exception as exc:
            print(f"  [warn] PE push failed: {exc}", file=sys.stderr)
            return None, None


def send_graphql_trigger(localai_url: str, state: str, reading: HealthReading) -> None:
    """POST a GraphQL updateProcessState mutation to localAI."""
    rag_code = _STATE_TO_RAG.get(state, "AMBER")
    variables = {
        "input": {
            "id":              "personal-health-baseline",
            "name":            "Personal Health Baseline",
            "status":          state,
            "ragStatusCode":   rag_code,
            "sourceMachine":   "localai/personal_health_baseline",
            "sourceSequence":  f"health-{state}",
            "context": json.dumps({
                "hr_bpm":      reading.hr_bpm,
                "hrv_sdnn_ms": reading.hrv_sdnn_ms,
                "sleep_hours": reading.sleep_hours,
                "state":       state,
                "description": _STATE_TO_DESCRIPTION.get(state, ""),
            }),
        },
    }
    try:
        with httpx.Client(timeout=5) as client:
            r = client.post(
                f"{localai_url}/graphql",
                json={"query": _GRAPHQL_UPDATE, "variables": variables},
            )
            r.raise_for_status()
            print(f"  [localAI] GraphQL trigger sent → {rag_code}/{state}")
    except Exception as exc:
        print(f"  [warn] GraphQL trigger failed: {exc}", file=sys.stderr)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pe-url",      default="http://localhost:3004")
    parser.add_argument("--re-url",      default="http://localhost:3000")
    parser.add_argument("--localai-url", default="http://localhost:4000")
    parser.add_argument("--scenario",    default="cycle",
                        choices=[*_SCENARIOS, "cycle"])
    parser.add_argument("--interval",    type=float, default=2.0,
                        help="Seconds between cycle steps")
    parser.add_argument("--no-graphql",  action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("  localAIStack — Personal Health Baseline Simulation")
    print("=" * 60)
    print(f"  PE:      {args.pe_url}")
    print(f"  localAI: {args.localai_url}")
    print(f"  Scenario: {args.scenario}")
    print()

    print("[1/3] Ensuring PE health sensors are registered …")
    ensure_sensors_registered(args.pe_url)

    scenarios = list(_SCENARIOS.values()) if args.scenario == "cycle" else [_SCENARIOS[args.scenario]]

    print(f"\n[2/3] Running {len(scenarios)} health scenario(s) …")
    for reading in scenarios:
        print(f"\n  ── {reading.label}")
        state, step = push_health_reading(args.pe_url, reading)
        if state:
            match = "✓" if state == reading.expected_state else "✗"
            print(f"  State:    {state}  (expected: {reading.expected_state}) {match}")
            print(f"  RE step:  {step}")
        else:
            print("  [warn] Could not decode health state from RE (PE/RE may not be running)")

        if not args.no_graphql and state:
            send_graphql_trigger(args.localai_url, state, reading)

        if len(scenarios) > 1:
            time.sleep(args.interval)

    print("\n[3/3] Verify in Grafana / localAI:")
    print(f"  Recent triggers: GET {args.localai_url}/graphql/events")
    print(f"  PE sources:      GET {args.pe_url}/api/sources")
    print(f"  PE state:        GET {args.pe_url}/api/state  (perceptualSpace[186:194])")
    print()
    print("Analogous Yuma/MQTT pipeline:")
    print("  yuma.lateraledge.cloud:1883 → MQTT mappings → PE → RE → CES → GraphQL")
    print("  simulated HealthKit readings → band values   → PE → RE → CES → GraphQL")
    print()


if __name__ == "__main__":
    main()
