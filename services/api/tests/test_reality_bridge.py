"""
End-to-end smoke tests for core.reality_bridge.

Two layers of coverage:

  (1) Offset-drift guard — verifies the machine JSON files checked into
      data/machines/ agree with the Python constants in reality_bridge.
      This is a pure structural check; any byte-offset move (like the one
      in v1.6 relocating localAI to [7440:7452]/[7492:7508]) that forgets to update
      either side will fail here instead of silently producing wrong routing
      at runtime.

  (2) End-to-end push cycle — exercises the bridge's per-request hot path with
      a fake PE/RE that returns a perceptualSpace showing 'generate' fired.
      Confirms that push_grading_signal decodes the RE response correctly and
      that get_session_context walks the carry offsets we declare.

The tests do not hit the network; httpx.Client is monkeypatched to a fake
that records requests and returns canned responses.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from core import reality_bridge

# ── (1) Offset-drift guard ────────────────────────────────────────────────────


def test_verify_machine_offsets_passes_on_checked_in_tree():
    """Current machine JSONs must agree with the Python constants in reality_bridge."""
    mismatches = reality_bridge.verify_machine_offsets()
    assert mismatches == [], "offset drift: " + " | ".join(mismatches)


def test_verify_machine_offsets_catches_drift(tmp_path, monkeypatch):
    """If a machine JSON drifts away from the expected offset, the guard reports it."""
    # Copy the real machines into a temp dir, then mutate one offset
    bad_dir = tmp_path / "machines"
    bad_dir.mkdir()
    for p in reality_bridge._MACHINES_DIR.glob("*.json"):
        (bad_dir / p.name).write_text(p.read_text())
    bad_file = bad_dir / "ai_load_bridge.json"
    data = json.loads(bad_file.read_text())
    data["machine"]["perceptualMapping"]["input"]["offset"] = 999
    bad_file.write_text(json.dumps(data))

    # Point the expected-machines table at the mutated copies
    patched = [dict(spec) for spec in reality_bridge._EXPECTED_MACHINE_OFFSETS]
    for spec in patched:
        spec["path"] = bad_dir / spec["path"].name
    monkeypatch.setattr(reality_bridge, "_EXPECTED_MACHINE_OFFSETS", patched)

    mismatches = reality_bridge.verify_machine_offsets()
    assert any("ai_load_bridge.json" in m and "input" in m for m in mismatches), (
        f"expected ai_load_bridge input mismatch in: {mismatches}"
    )


# The canonical AI machines live in the corpus. This used to read
# RealityEngine_AI/examples/machines — a repo outside the focus set that CI never
# checks out, so the test skipped everywhere except a workstation that happened
# to have it, and the drift in jateeter/localAIStack#48 went unseen for as long
# as it did. RealityEngine_Machines is a focus repo and is checked out by the
# local regression lane.
_AI_MACHINE_DIR = (
    pathlib.Path(__file__).resolve().parents[4]
    / "RealityEngine_Machines"
    / "machines"
    / "domains"
    / "ai-services"
)

# ai_load_bridge feeds only these two. The other four AI machines
# (AIPowerEfficiency, AICoolingRegulator, AICapacityThrottler, AISecurityMonitor)
# are written by corpus machines AGX051-054, so projecting across all six would
# contend with deterministic corpus writers on cells no arbitration registry
# declares — see jateeter/localAIStack#48.
_BRIDGE_FED = ["AIModelWellness.json", "AIHardwareResilience.json"]
_CORPUS_FED = [
    "AIPowerEfficiency.json",
    "AICoolingRegulator.json",
    "AICapacityThrottler.json",
    "AISecurityMonitor.json",
]


def _bridge_output_window() -> tuple[int, int]:
    spec = next(
        s
        for s in reality_bridge._EXPECTED_MACHINE_OFFSETS
        if s["path"].name == "ai_load_bridge.json"
    )
    start = spec["output"]["offset"]
    return start, start + spec["output"]["length"]


def _ai_input(fname: str) -> dict:
    path = _AI_MACHINE_DIR / fname
    if not path.exists():
        pytest.skip(f"{fname} not present at {_AI_MACHINE_DIR}")
    return json.loads(path.read_text())["machine"]["perceptualMapping"]["input"]


def test_ai_load_bridge_output_window_covers_the_machines_it_feeds():
    """The two AI machines no corpus machine writes must sit inside the window."""
    out_start, out_end = _bridge_output_window()
    for fname in _BRIDGE_FED:
        inp = _ai_input(fname)
        assert out_start <= inp["offset"], (
            f"{fname} input offset {inp['offset']} < ai_load_bridge output start {out_start}"
        )
        assert inp["offset"] + inp["length"] <= out_end, (
            f"{fname} input extends past ai_load_bridge output window [{out_start}:{out_end}]"
        )


def test_ai_load_bridge_does_not_write_corpus_fed_machine_inputs():
    """The four AI machines AGX051-054 feed must sit outside the window.

    This is the half of the contract that keeps the narrowing honest: widening
    the window back over them would silently reintroduce undeclared contention
    with deterministic corpus writers, which is what #48 was filed about.
    """
    out_start, out_end = _bridge_output_window()
    for fname in _CORPUS_FED:
        inp = _ai_input(fname)
        in_start, in_end = inp["offset"], inp["offset"] + inp["length"]
        assert in_end <= out_start or out_end <= in_start, (
            f"{fname} input [{in_start}:{in_end}] overlaps ai_load_bridge output "
            f"[{out_start}:{out_end}] — that cell is written by a corpus machine"
        )


# ── (2) End-to-end push cycle (fake PE/RE) ────────────────────────────────────


class _FakeResponse:
    def __init__(self, status: int = 200, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._body


class _FakePEREClient:
    """Single-context-manager httpx.Client stand-in. Records every POST."""

    def __init__(self, *_, **__):
        self.posts: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url: str, **_):
        if "/api/sources" in url:
            return _FakeResponse(200, {"sources": []})
        if "/api/machines" in url:
            return _FakeResponse(200, {"machines": []})
        return _FakeResponse(200, {})

    def post(self, url: str, json: dict | None = None, **_):
        self.posts.append({"url": url, "json": json})
        if "/api/push" in url:
            # Synthesize a perceptualSpace showing a full localAI cycle:
            #   rag_corrective_cycle fired 'generate',
            #   session_rag_context latched the carry,
            #   agent_activity_classifier asserted 'productive',
            #   ai_load_bridge projected the nominal tier.
            # Sized and indexed from the module constant rather than a literal:
            # the vector has to reach ai_load_bridge's window, and hardcoding
            # that offset here is how this fixture silently stopped covering it
            # when the window moved (jateeter/localAIStack#48).
            tier_offset = reality_bridge._AI_LOAD_TIER_OFFSET
            # The vector must reach BOTH the bridge lane at [272:280] and the
            # reserved band the rest of the machines moved into, and those are no
            # longer the same end of the vector.
            ps = [0.0] * max(tier_offset + 8, reality_bridge._HEALTH_CARRY_OFFSET + 4)
            ps[reality_bridge._OUTPUT_GENERATE] = 1.0  # rag_corrective_cycle.generate
            ps[reality_bridge._AGENT_ACT_PRODUCTIVE] = 1.0  # agent_activity_classifier.productive
            ps[reality_bridge._SESSION_RAG_OFFSET] = 1.0  # session_rag.last_generate carry
            for i in range(2):  # ai_load_bridge writes two 4-byte windows
                base = tier_offset + i * 4
                ps[base + 0] = 0.15
                ps[base + 1] = 0.30
                ps[base + 2] = 0.20
                ps[base + 3] = 0.10
            return _FakeResponse(
                200,
                {
                    "step": {"perceptualSpace": ps},
                    "globalStep": 1,
                },
            )
        if "/api/sources" in url or "/api/sensors/" in url:
            return _FakeResponse(200, {"ok": True})
        if "/api/machines" in url:
            return _FakeResponse(200, {"machine": {"id": "fake-id"}})
        return _FakeResponse(200, {})


@pytest.fixture
def fake_client(monkeypatch):
    fake = _FakePEREClient()
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)
    return fake


def test_get_session_context_decodes_rag_carry():
    ps = [0.0] * 7680
    ps[7500] = 1.0
    assert reality_bridge.get_session_context(ps)["rag"] == "generate"

    ps = [0.0] * 7680
    ps[7501] = 1.0
    assert reality_bridge.get_session_context(ps)["rag"] == "rewrite"

    ps = [0.0] * 7680
    ps[7502] = 1.0
    assert reality_bridge.get_session_context(ps)["rag"] == "abort"

    # Short ps returns None without raising
    assert reality_bridge.get_session_context([])["rag"] is None


def test_get_session_context_decodes_agent_flags():
    ps = [0.0] * 7680
    ps[7504] = 1.0  # agent_ever_engaged
    ps[7505] = 0.0  # tools_ever_used
    ctx = reality_bridge.get_session_context(ps)
    assert ctx["agent"]["ever_engaged"] is True
    assert ctx["agent"]["tools_ever_used"] is False

    ps[7505] = 1.0
    ctx = reality_bridge.get_session_context(ps)
    assert ctx["agent"]["tools_ever_used"] is True


def test_end_to_end_grading_drives_generate_routing(fake_client):
    """
    Full per-request cycle:
      push_retrieval_signal → PE sensor write
      push_grading_signal   → PE sensor write + /api/push trigger + read routing
    The fake /api/push returns a perceptualSpace with generate=HIGH, so the
    decoded route must be 'generate'.
    """
    reality_bridge.push_retrieval_signal(doc_count=5, avg_score=0.8)
    route = reality_bridge.push_grading_signal(
        retrieved_count=5,
        kept_count=5,
        rewrite_count=0,
    )
    assert route == "generate"

    urls = [p["url"] for p in fake_client.posts]
    assert any("/api/sensors/localai_rag_retrieval" in u for u in urls), (
        "expected retrieval sensor write"
    )
    assert any("/api/sensors/localai_rag_grading" in u for u in urls), (
        "expected grading sensor write"
    )
    assert any("/api/push" in u for u in urls), "expected /api/push trigger after grading"


def test_end_to_end_push_response_short_falls_back_to_rewrite(monkeypatch):
    """When the PE returns a truncated perceptualSpace, the bridge degrades to 'rewrite'."""

    class _ShortClient(_FakePEREClient):
        def post(self, url, json=None, **_):
            self.posts.append({"url": url, "json": json})
            if "/api/push" in url:
                return _FakeResponse(200, {"step": {"perceptualSpace": [0.0] * 10}})
            return super().post(url, json=json)

    fake = _ShortClient()
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)

    route = reality_bridge.push_grading_signal(retrieved_count=3, kept_count=0, rewrite_count=0)
    assert route == "rewrite"


def test_grading_sensor_values_are_normalized(fake_client):
    """
    The grading sensor write must carry (kept_ratio, rewrite_count_norm)
    in the first two slots — these are exactly the signals
    rag_corrective_cycle expects at elements [4] and [5] of its input window.
    """
    reality_bridge.push_grading_signal(retrieved_count=4, kept_count=1, rewrite_count=1)
    grading = next(p for p in fake_client.posts if "/api/sensors/localai_rag_grading" in p["url"])
    values = grading["json"]["values"]
    assert values[0] == pytest.approx(0.25)  # 1/4 kept_ratio
    assert values[1] == pytest.approx(0.5)  # 1/2 rewrite_count_norm
    assert values[2] == 0.0 and values[3] == 0.0


def test_retrieval_sensor_clamps_doc_count(fake_client):
    """doc_count >= 10 must saturate doc_count_norm at 1.0."""
    reality_bridge.push_retrieval_signal(doc_count=50, avg_score=0.9)
    post = next(p for p in fake_client.posts if "/api/sensors/localai_rag_retrieval" in p["url"])
    assert post["json"]["values"][0] == 1.0
    assert post["json"]["values"][1] == pytest.approx(0.9)


# ── (3) Agent-activity signal path ────────────────────────────────────────────


def test_push_agent_activity_signal_writes_sensor_and_returns_session(fake_client):
    """
    push_agent_activity_signal is the agent-side analog of push_grading_signal:
      • writes the normalized (calls, errors, depth) vector to the PE sensor
      • triggers /api/push so agent_activity_classifier fires this cycle
      • returns the enriched session context dict
    """
    session = reality_bridge.push_agent_activity_signal(
        tool_calls=2,
        tool_errors=0,
        reasoning_steps=3,
    )
    assert session["agent_activity"] == "productive"
    assert session["ai_load_tier"] == "nominal"
    assert session["rag"] == "generate"

    activity_post = next(
        p for p in fake_client.posts if "/api/sensors/localai_agent_activity" in p["url"]
    )
    vals = activity_post["json"]["values"]
    assert vals[0] == pytest.approx(0.4)  # 2 / 5 tool_calls_norm
    assert vals[1] == 0.0  # 0 / 3 tool_errors_norm
    assert vals[2] == pytest.approx(0.3)  # 3 / 10 reasoning_depth_norm
    assert vals[3] == 0.0

    assert any("/api/push" in p["url"] for p in fake_client.posts)


def test_push_agent_activity_signal_clamps_high_values(fake_client):
    """Saturating inputs must clamp to 1.0 on each dimension."""
    reality_bridge.push_agent_activity_signal(
        tool_calls=99,
        tool_errors=99,
        reasoning_steps=99,
    )
    post = next(p for p in fake_client.posts if "/api/sensors/localai_agent_activity" in p["url"])
    assert post["json"]["values"][:3] == [1.0, 1.0, 1.0]


def test_push_agent_activity_signal_returns_defaults_on_bridge_failure(monkeypatch):
    """When the PE is unreachable, the push function still returns a session dict."""

    class _Raising(_FakePEREClient):
        def post(self, url, json=None, **_):
            if "/api/push" in url:
                raise RuntimeError("PE down")
            return super().post(url, json=json)

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _Raising())

    session = reality_bridge.push_agent_activity_signal(1, 0, 2)
    assert session == {
        "rag": None,
        "agent": {"ever_engaged": False, "tools_ever_used": False},
        "agent_activity": None,
        "ai_load_tier": None,
    }


# ── (4) Decoders (ai_load_tier, agent_activity) ───────────────────────────────


@pytest.mark.parametrize(
    ("v0", "expected"),
    [
        (0.15, "nominal"),
        (0.30, "nominal"),
        (0.62, "elevated"),
        (0.78, "elevated"),
        (0.92, "critical"),
        (0.99, "critical"),
        (0.05, None),
        (0.00, None),
    ],
)
def test_get_ai_load_tier_classifies_first_window(v0, expected):
    """The first element of ai_load_bridge's output window decides the tier."""
    tier_offset = reality_bridge._AI_LOAD_TIER_OFFSET
    ps = [0.0] * (tier_offset + 8)
    ps[tier_offset] = v0
    assert reality_bridge.get_ai_load_tier(ps) == expected


def test_get_ai_load_tier_none_on_short_ps():
    assert reality_bridge.get_ai_load_tier([]) is None
    assert reality_bridge.get_ai_load_tier([0.0] * 120) is None


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (7456, "productive"),
        (7457, "normal"),
        (7458, "struggling"),
    ],
)
def test_session_context_decodes_agent_activity(offset, expected):
    ps = [0.0] * 7680
    ps[offset] = 1.0
    assert reality_bridge.get_session_context(ps)["agent_activity"] == expected


def test_session_context_agent_activity_none_when_classifier_silent():
    ps = [0.0] * 7680
    assert reality_bridge.get_session_context(ps)["agent_activity"] is None


# ── (5) Drift guard: agent_activity_classifier is registered ──────────────────


def test_drift_guard_covers_agent_activity_classifier():
    """The drift guard must check agent_activity_classifier alongside the others."""
    filenames = {spec["path"].name for spec in reality_bridge._EXPECTED_MACHINE_OFFSETS}
    assert "agent_activity_classifier.json" in filenames


def test_drift_guard_rejects_drifted_agent_sensor(monkeypatch):
    """If localai_agent_activity drifts outside classifier input, guard reports it."""
    bad_sensors = [dict(s) for s in reality_bridge._RAG_SENSORS]
    for s in bad_sensors:
        if s["sensorId"] == "localai_agent_activity":
            s["region"] = {"offset": 7588, "length": 4}  # outside [7452:7456]
    monkeypatch.setattr(reality_bridge, "_RAG_SENSORS", bad_sensors)

    mismatches = reality_bridge.verify_machine_offsets()
    assert any(
        "localai_agent_activity" in m and "agent_activity_classifier.json" in m for m in mismatches
    ), f"expected sensor/consumer mismatch in: {mismatches}"
