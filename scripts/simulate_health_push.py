#!/usr/bin/env python3
"""
Personal health baseline simulation — the localAIStack analog of the Yuma/MQTT demo.

Demonstrates the full loop without an iOS device:

  simulated health readings
    → graded ok / watch / concern against data/health/health_bands.json
    → worst band wins → roll-up one-hot written to sensor localai_health_rollup [7574:7578]
    → PE /api/push  (assembles perceptual vector, calls RE /api/perceive)
    → personal_health_baseline CES machine fires in RE
    → health state decoded from perceptualSpace[7578:7582]
    → GraphQL trigger posted to localAI (POST /graphql)

On a device the bridge's families reach the PE directly and localAIStack's
scope follower grades them (services/api/core/health_scope.py). This script
grades readings it makes up, with the same table.

Analogous to the Yuma/MQTT end-to-end:
  MQTT broker → mapping registry → PE → RE → CES → governance trigger

This script replaces "MQTT broker" with synthetic health readings and
"mapping registry" with the band table in data/health/health_bands.json.

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

Health scenarios (grades: pulse / hrv / sleep; worst band wins)
  thriving   HR=72,  HRV=45ms, Sleep=7.5h  → ok / ok / ok
  balanced   HR=75,  HRV=38ms, Sleep=5.5h  → ok / ok / watch       (one watch)
  watch      HR=110, HRV=25ms, Sleep=7.0h  → watch / watch / ok    (two watch)
  attention  HR=70,  HRV=18ms, Sleep=6.0h  → ok / concern / watch  (any concern)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import NamedTuple

import httpx

# The band table and roll-up are the service's own, not a copy that can drift.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services" / "api"))
from core import health_bands  # noqa: E402

_HEALTH_OUTPUT_OFFSET = 7578  # [thriving, balanced, watch, attention]

_ROLLUP_SENSOR = {
    "sensorId": "localai_health_rollup",
    "name": "localai/health/rollup",
    "region": {"offset": 7574, "length": 4},
    "ttlMs": 86_400_000,
}

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
    "thriving": "GREEN",
    "balanced": "GREEN",
    "watch": "AMBER",
    "attention": "RED",
}

_STATE_TO_DESCRIPTION = {
    "thriving": "All baseline health metrics are in nominal range.",
    "balanced": "One health measure is slightly outside its nominal range.",
    "watch": "Several health measures are slightly outside their nominal ranges.",
    "attention": "At least one health measure is well outside its nominal range.",
}


class HealthReading(NamedTuple):
    label: str
    hr_bpm: float
    hrv_sdnn_ms: float
    sleep_hours: float
    expected_state: str


_SCENARIOS: dict[str, HealthReading] = {
    "thriving": HealthReading("All nominal — thriving", 72.0, 45.0, 7.5, "thriving"),
    "balanced": HealthReading("Short sleep — balanced", 75.0, 38.0, 5.5, "balanced"),
    "watch": HealthReading("Pulse and HRV slightly off — watch", 110.0, 25.0, 7.0, "watch"),
    "attention": HealthReading("HRV well below range — attention", 70.0, 18.0, 6.0, "attention"),
}


# ── PE interaction helpers ────────────────────────────────────────────────────


def _grades(hr: float, hrv: float, sleep: float) -> dict[str, str]:
    table = health_bands.load_bands()
    return {
        "pulse": health_bands.grade_raw(health_bands.band(table, "pulse"), hr),
        "hrv": health_bands.grade_raw(health_bands.band(table, "hrv"), hrv),
        "sleep": health_bands.grade_raw(health_bands.band(table, "sleep"), sleep),
    }


def _decode_health_state(ps: list[float]) -> str | None:
    def s(i: int) -> float:
        return ps[i] if len(ps) > i else 0.0

    if s(_HEALTH_OUTPUT_OFFSET) >= 0.5:
        return "thriving"
    if s(_HEALTH_OUTPUT_OFFSET + 1) >= 0.5:
        return "balanced"
    if s(_HEALTH_OUTPUT_OFFSET + 2) >= 0.5:
        return "watch"
    if s(_HEALTH_OUTPUT_OFFSET + 3) >= 0.5:
        return "attention"
    return None


def ensure_sensors_registered(pe_url: str) -> None:
    """Register the roll-up sensor source in the PE if not already present."""
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

        for sensor in [_ROLLUP_SENSOR]:
            sid = sensor["sensorId"]
            if sid in existing:
                print(f"  [PE]   sensor already registered: {sid}")
                continue
            payload = {
                "type": "sensor",
                "name": sensor["name"],
                "region": sensor["region"],
                "active": False,  # activated by its first value
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


def _activate(client: httpx.Client, pe_url: str, sensor_id: str) -> None:
    """Activate a sensor source after its value is written, never before."""
    sources = client.get(f"{pe_url}/api/sources").json().get("sources", [])
    src = next((x for x in sources if x.get("sensorId") == sensor_id), None)
    if src and not src.get("active"):
        client.patch(f"{pe_url}/api/sources/{src['id']}", json={"active": True}).raise_for_status()


def push_health_reading(
    pe_url: str,
    reading: HealthReading,
) -> tuple[str | None, int | None]:
    """
    Grade the reading, write the roll-up, trigger /api/push, decode health state.
    Returns (state, global_step).
    """
    grades = _grades(reading.hr_bpm, reading.hrv_sdnn_ms, reading.sleep_hours)
    rollup = health_bands.rollup(list(grades.values()))

    print(
        f"\n  Reading:  HR={reading.hr_bpm}bpm  HRV={reading.hrv_sdnn_ms}ms  "
        f"Sleep={reading.sleep_hours}h"
    )
    print("  Grades:   " + "  ".join(f"{b}={g}" for b, g in grades.items()))
    print(f"  Roll-up:  {rollup}")

    sid = _ROLLUP_SENSOR["sensorId"]
    with httpx.Client(timeout=5) as client:
        try:
            r = client.post(
                f"{pe_url}/api/sensors/{sid}", json={"values": health_bands.rollup_vector(rollup)}
            )
            r.raise_for_status()
            _activate(client, pe_url, sid)
        except Exception as exc:
            print(f"  [warn] sensor write failed ({sid}): {exc}", file=sys.stderr)

        try:
            push_resp = client.post(f"{pe_url}/api/push")
            push_resp.raise_for_status()
            data = push_resp.json()
            ps = data.get("step", {}).get("perceptualSpace", [])
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
            "id": "personal-health-baseline",
            "name": "Personal Health Baseline",
            "status": state,
            "ragStatusCode": rag_code,
            "sourceMachine": "localai/personal_health_baseline",
            "sourceSequence": f"health-{state}",
            "context": json.dumps(
                {
                    "hr_bpm": reading.hr_bpm,
                    "hrv_sdnn_ms": reading.hrv_sdnn_ms,
                    "sleep_hours": reading.sleep_hours,
                    "state": state,
                    "description": _STATE_TO_DESCRIPTION.get(state, ""),
                }
            ),
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


# ── CareKit leg (T5) ──────────────────────────────────────────────────────────
# Pre-normalised ratios, no band step (medication_adherence.json). Sensor
# definitions and the decoder are the service's own, not copies.

_CAREKIT_SCENARIOS: dict[str, tuple[float, float, float]] = {
    # (med_adherence, task_completion, symptom_ok)
    "adherent": (1.0, 1.0, 1.0),
    "partial": (1.0, 0.2, 1.0),
    "lapsed": (0.2, 0.5, 1.0),
    "concern": (0.0, 0.3, 0.0),
}


def run_carekit(pe_url: str, scenario: str, interval: float) -> None:
    from core import reality_bridge

    names = list(_CAREKIT_SCENARIOS) if scenario == "cycle" else [scenario]
    with httpx.Client(timeout=5) as client:
        existing = reality_bridge.get_sensor_sources(client, pe_url)
        reality_bridge._register_sensor_list(
            client, reality_bridge._CAREKIT_SENSORS, existing, pe_url
        )
        for name in names:
            values = _CAREKIT_SCENARIOS[name]
            for sensor, value in zip(reality_bridge._CAREKIT_SENSORS, values, strict=True):
                sid = sensor["sensorId"]
                client.post(
                    f"{pe_url}/api/sensors/{sid}", json={"values": [value]}
                ).raise_for_status()
                _activate(client, pe_url, sid)
            r = client.post(f"{pe_url}/api/push")
            r.raise_for_status()
            ps = r.json().get("step", {}).get("perceptualSpace", [])
            state = reality_bridge.get_carekit_state(ps)
            mark = "✓" if state == name else "✗"
            print(f"  CareKit {name:9s} med/task/symptom={values}  →  {state}  {mark}")
            if len(names) > 1:
                time.sleep(interval)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pe-url", default="http://localhost:3004")
    parser.add_argument("--re-url", default="http://localhost:3000")
    parser.add_argument("--localai-url", default="http://localhost:4000")
    parser.add_argument("--scenario", default="cycle", choices=[*_SCENARIOS, "cycle"])
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between cycle steps")
    parser.add_argument("--no-graphql", action="store_true")
    parser.add_argument(
        "--carekit",
        choices=[*_CAREKIT_SCENARIOS, "cycle"],
        help="Drive the CareKit leg instead: medication_adherence scenarios",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  localAIStack — Personal Health Baseline Simulation")
    print("=" * 60)
    print(f"  PE:      {args.pe_url}")
    print(f"  localAI: {args.localai_url}")
    print(f"  Scenario: {args.scenario}")
    print()

    if args.carekit:
        run_carekit(args.pe_url, args.carekit, args.interval)
        return

    print("[1/3] Ensuring PE health sensors are registered …")
    ensure_sensors_registered(args.pe_url)

    scenarios = (
        list(_SCENARIOS.values()) if args.scenario == "cycle" else [_SCENARIOS[args.scenario]]
    )

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
    print(f"  PE state:        GET {args.pe_url}/api/state  (perceptualSpace[7574:7582])")
    print()
    print("Analogous Yuma/MQTT pipeline:")
    print("  yuma.lateraledge.cloud:1883 → MQTT mappings → PE → RE → CES → GraphQL")
    print("  simulated HealthKit readings → graded roll-up → PE → RE → CES → GraphQL")
    print()


if __name__ == "__main__":
    main()
