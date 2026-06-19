"""
Phase 4 tests — CareKit machine, health session carry, PE integrations registry.

Covers the three Phase 4 PE implementations:
  (a) medication_adherence CES machine at [194:198]→[198:202]
  (b) session_health_context bistable carry at [190:194]→[202:206]
  (c) config/pe-integrations.json structure (PE registry for HealthKit + CareKit)

Sections:
  (1)  Offset-drift guard — new machines in _EXPECTED_MACHINE_OFFSETS; JSON agrees
  (2)  CareKit machine JSON — 5 sequences, mutual exclusivity guards
  (3)  CareKit sensors — in _SENSOR_TO_MACHINE, inside machine input window
  (4)  get_carekit_state() decoder — all four states + None + short ps
  (5)  get_health_state_from_carry() decoder — all four states + None + short ps
  (6)  push_carekit_signal() end-to-end — all 4 output states, clamping, fallback
  (7)  import_carekit_machine() idempotency — skip if exists, POST when missing
  (8)  session_health_context.json offsets — input [190:194], output [202:206]
  (9)  get_session_context() — health_state key present and uses carry
  (10) get_current_health_state() — carry fallback when live output is silent
  (11) verify_machine_offsets() — covers all three sensor lists (bug-fix validation)
  (12) pe-integrations.json — version, integrations, sourceMappings structure

All tests are network-free; httpx.Client is monkeypatched with a fake.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from core import reality_bridge

# ── Shared fake helpers ───────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status: int = 200, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._body


class _FakeCareKitClient:
    """
    httpx.Client stand-in for CareKit sensor tests.
    POST /api/push returns a 256-element perceptualSpace with one bit
    set at carekit_state_offset (198=adherent, 199=partial, 200=lapsed, 201=concern).
    """

    def __init__(self, carekit_state_offset: int = 198):
        self.posts: list[dict] = []
        self._carekit_state_offset = carekit_state_offset

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
            ps = [0.0] * 256
            ps[self._carekit_state_offset] = 1.0
            return _FakeResponse(200, {
                "step": {"perceptualSpace": ps},
                "globalStep": 1,
            })
        return _FakeResponse(200, {"ok": True})


@pytest.fixture
def fake_carekit_client(monkeypatch):
    """Default fake: returns adherent state (offset 198)."""
    fake = _FakeCareKitClient(carekit_state_offset=198)
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)
    return fake


# ── (1) Offset-drift guard ────────────────────────────────────────────────────


def test_carekit_machine_is_in_expected_offsets_table():
    """medication_adherence.json must be in the drift guard table."""
    filenames = {spec["path"].name for spec in reality_bridge._EXPECTED_MACHINE_OFFSETS}
    assert "medication_adherence.json" in filenames


def test_session_health_context_is_in_expected_offsets_table():
    """session_health_context.json must be in the drift guard table."""
    filenames = {spec["path"].name for spec in reality_bridge._EXPECTED_MACHINE_OFFSETS}
    assert "session_health_context.json" in filenames


def test_carekit_machine_offsets_match_json():
    """medication_adherence.json must agree with the Python constants."""
    mismatches = reality_bridge.verify_machine_offsets()
    ck_mismatches = [m for m in mismatches if "medication_adherence" in m]
    assert ck_mismatches == [], (
        "medication_adherence.json offset drift: " + " | ".join(ck_mismatches)
    )


def test_session_health_context_offsets_match_json():
    """session_health_context.json must agree with the Python constants."""
    mismatches = reality_bridge.verify_machine_offsets()
    carry_mismatches = [m for m in mismatches if "session_health_context" in m]
    assert carry_mismatches == [], (
        "session_health_context.json offset drift: " + " | ".join(carry_mismatches)
    )


def test_drift_guard_catches_carekit_machine_input_offset_mutation(tmp_path, monkeypatch):
    """If medication_adherence.json input offset moves, the guard must report it."""
    bad_dir = tmp_path / "machines"
    bad_dir.mkdir()
    for p in reality_bridge._MACHINES_DIR.glob("*.json"):
        (bad_dir / p.name).write_text(p.read_text())

    bad_file = bad_dir / "medication_adherence.json"
    data = json.loads(bad_file.read_text())
    data["machine"]["perceptualMapping"]["input"]["offset"] = 999
    bad_file.write_text(json.dumps(data))

    patched = [dict(spec) for spec in reality_bridge._EXPECTED_MACHINE_OFFSETS]
    for spec in patched:
        spec["path"] = bad_dir / spec["path"].name
    monkeypatch.setattr(reality_bridge, "_EXPECTED_MACHINE_OFFSETS", patched)

    mismatches = reality_bridge.verify_machine_offsets()
    assert any(
        "medication_adherence.json" in m and "input" in m for m in mismatches
    ), f"expected carekit machine input mismatch in: {mismatches}"


def test_drift_guard_catches_health_carry_output_offset_mutation(tmp_path, monkeypatch):
    """If session_health_context.json output offset moves, the guard must report it."""
    bad_dir = tmp_path / "machines"
    bad_dir.mkdir()
    for p in reality_bridge._MACHINES_DIR.glob("*.json"):
        (bad_dir / p.name).write_text(p.read_text())

    bad_file = bad_dir / "session_health_context.json"
    data = json.loads(bad_file.read_text())
    data["machine"]["perceptualMapping"]["output"]["offset"] = 888
    bad_file.write_text(json.dumps(data))

    patched = [dict(spec) for spec in reality_bridge._EXPECTED_MACHINE_OFFSETS]
    for spec in patched:
        spec["path"] = bad_dir / spec["path"].name
    monkeypatch.setattr(reality_bridge, "_EXPECTED_MACHINE_OFFSETS", patched)

    mismatches = reality_bridge.verify_machine_offsets()
    assert any(
        "session_health_context.json" in m and "output" in m for m in mismatches
    ), f"expected health carry output mismatch in: {mismatches}"


# ── (2) CareKit machine JSON ──────────────────────────────────────────────────


def _load_carekit_machine() -> dict:
    path = reality_bridge._CAREKIT_MACHINE_PATH
    return json.loads(path.read_text())["machine"]


def test_carekit_machine_has_five_sequences():
    """medication_adherence.json must define exactly 5 sequences."""
    machine = _load_carekit_machine()
    assert len(machine["sequences"]) == 5, (
        f"Expected 5 sequences, got {len(machine['sequences'])}"
    )


def test_carekit_machine_perceptual_mapping():
    """medication_adherence.json input [194:198] and output [198:202]."""
    machine = _load_carekit_machine()
    pm = machine["perceptualMapping"]
    assert pm["input"]  == {"offset": 194, "length": 4}
    assert pm["output"] == {"offset": 198, "length": 4}


def test_carekit_machine_arbiter_is_or():
    machine = _load_carekit_machine()
    assert machine["arbiterRule"] == "OR"


def test_carekit_machine_match_algorithm_is_gte():
    machine = _load_carekit_machine()
    assert machine["matchAlgorithm"] == "gte"


def test_carekit_machine_all_sequences_are_initial():
    """All CareKit sequences must have a single isInitial vector (classifier pattern)."""
    machine = _load_carekit_machine()
    for seq in machine["sequences"]:
        vecs = seq["vectors"]
        assert len(vecs) == 1, f"{seq['id']}: expected 1 vector, got {len(vecs)}"
        assert vecs[0]["isInitial"] is True, f"{seq['id']}: vector must be isInitial"


def test_carekit_machine_sequences_emit_one_hot_outputs():
    """
    Mutual exclusivity: each sequence emits a distinct one-hot [adherent, partial, lapsed, concern].
    partial-task and partial-symptom both emit [0,1,0,0] — that's intentional.
    """
    machine = _load_carekit_machine()
    expected_outputs = {
        "carekit-adherent":         [1.0, 0.0, 0.0, 0.0],
        "carekit-partial-task":     [0.0, 1.0, 0.0, 0.0],
        "carekit-partial-symptom":  [0.0, 1.0, 0.0, 0.0],
        "carekit-lapsed":           [0.0, 0.0, 1.0, 0.0],
        "carekit-concern":          [0.0, 0.0, 0.0, 1.0],
    }
    for seq in machine["sequences"]:
        sid = seq["id"]
        if sid not in expected_outputs:
            continue
        out_vec = seq["vectors"][0]["outputVectors"][0]["vector"]
        assert out_vec == expected_outputs[sid], (
            f"{sid}: expected output {expected_outputs[sid]}, got {out_vec}"
        )


def test_carekit_machine_adherent_guards_are_all_high():
    """
    carekit-adherent: element[0]=HIGH (med), element[1]=HIGH (task), element[2]=HIGH (symptom).
    All GTE value=1.0 — any sensor below 0.5 must not match this sequence.
    """
    machine = _load_carekit_machine()
    seq = next(s for s in machine["sequences"] if s["id"] == "carekit-adherent")
    elements = seq["vectors"][0]["elements"]
    assert elements[0]["value"] == 1.0
    assert elements[1]["value"] == 1.0
    assert elements[2]["value"] == 1.0


def test_carekit_machine_concern_guards_med_and_symptom_low():
    """carekit-concern: element[0]=LOW (med), element[2]=LOW (symptom)."""
    machine = _load_carekit_machine()
    seq = next(s for s in machine["sequences"] if s["id"] == "carekit-concern")
    elements = seq["vectors"][0]["elements"]
    assert elements[0]["value"] == 0.0  # med LOW
    assert elements[2]["value"] == 0.0  # symptom LOW


def test_carekit_machine_lapsed_guards_med_low_symptom_high():
    """carekit-lapsed: element[0]=LOW (med), element[2]=HIGH (symptom_ok)."""
    machine = _load_carekit_machine()
    seq = next(s for s in machine["sequences"] if s["id"] == "carekit-lapsed")
    elements = seq["vectors"][0]["elements"]
    assert elements[0]["value"] == 0.0  # med LOW
    assert elements[2]["value"] == 1.0  # symptom HIGH (no symptoms = ok)


def test_carekit_machine_partial_task_guards_med_high_task_low():
    """carekit-partial-task: element[0]=HIGH (med), element[1]=LOW (task)."""
    machine = _load_carekit_machine()
    seq = next(s for s in machine["sequences"] if s["id"] == "carekit-partial-task")
    elements = seq["vectors"][0]["elements"]
    assert elements[0]["value"] == 1.0  # med HIGH
    assert elements[1]["value"] == 0.0  # task LOW


def test_carekit_machine_partial_symptom_guards_med_task_high_symptom_low():
    """carekit-partial-symptom: med=HIGH, task=HIGH, symptom=LOW."""
    machine = _load_carekit_machine()
    seq = next(s for s in machine["sequences"] if s["id"] == "carekit-partial-symptom")
    elements = seq["vectors"][0]["elements"]
    assert elements[0]["value"] == 1.0  # med HIGH
    assert elements[1]["value"] == 1.0  # task HIGH
    assert elements[2]["value"] == 0.0  # symptom LOW


# ── (3) CareKit sensors ───────────────────────────────────────────────────────


def test_carekit_sensors_are_registered_in_sensor_to_machine():
    """All three CareKit sensor IDs must map to medication_adherence.json."""
    for sid in (
        "localai_carekit_med_adherence",
        "localai_carekit_task_completion",
        "localai_carekit_symptom_ok",
    ):
        assert reality_bridge._SENSOR_TO_MACHINE.get(sid) == "medication_adherence.json", \
            f"{sid} missing from _SENSOR_TO_MACHINE"


def test_carekit_sensors_are_inside_carekit_machine_input_window():
    """
    Each CareKit sensor's region must lie inside medication_adherence.json's
    input window [194:198]. Sensor drift outside this window means the machine
    cannot read the sensor — this must be caught by the drift guard.
    """
    spec = next(
        s for s in reality_bridge._EXPECTED_MACHINE_OFFSETS
        if s["path"].name == "medication_adherence.json"
    )
    m_start = spec["input"]["offset"]
    m_end   = m_start + spec["input"]["length"]

    for sensor in reality_bridge._CAREKIT_SENSORS:
        sr = sensor["region"]
        assert sr["offset"] >= m_start, (
            f"{sensor['sensorId']} offset {sr['offset']} < machine input start {m_start}"
        )
        assert sr["offset"] + sr["length"] <= m_end, (
            f"{sensor['sensorId']} end {sr['offset'] + sr['length']} > machine input end {m_end}"
        )


def test_carekit_sensors_have_correct_offsets():
    """med_adherence→194, task_completion→195, symptom_ok→196."""
    by_id = {s["sensorId"]: s for s in reality_bridge._CAREKIT_SENSORS}
    assert by_id["localai_carekit_med_adherence"]["region"]["offset"]   == 194
    assert by_id["localai_carekit_task_completion"]["region"]["offset"] == 195
    assert by_id["localai_carekit_symptom_ok"]["region"]["offset"]      == 196


# ── (4) get_carekit_state() decoder ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (198, "adherent"),
        (199, "partial"),
        (200, "lapsed"),
        (201, "concern"),
    ],
)
def test_get_carekit_state_decodes_each_state(offset, expected):
    ps = [0.0] * 256
    ps[offset] = 1.0
    assert reality_bridge.get_carekit_state(ps) == expected


def test_get_carekit_state_returns_none_when_machine_silent():
    ps = [0.0] * 256
    assert reality_bridge.get_carekit_state(ps) is None


def test_get_carekit_state_none_on_short_ps():
    assert reality_bridge.get_carekit_state([]) is None
    assert reality_bridge.get_carekit_state([0.0] * 198) is None


def test_get_carekit_state_first_match_wins():
    """When multiple bits are HIGH (shouldn't happen in practice), adherent wins."""
    ps = [0.0] * 256
    ps[198] = 1.0  # adherent
    ps[199] = 1.0  # partial — must not override adherent
    assert reality_bridge.get_carekit_state(ps) == "adherent"


def test_get_carekit_state_threshold_is_point_five():
    """The ≥ 0.5 threshold must apply: 0.49 → None, 0.5 → adherent."""
    ps_below = [0.0] * 256
    ps_below[198] = 0.49
    assert reality_bridge.get_carekit_state(ps_below) is None

    ps_at = [0.0] * 256
    ps_at[198] = 0.5
    assert reality_bridge.get_carekit_state(ps_at) == "adherent"


# ── (5) get_health_state_from_carry() decoder ─────────────────────────────────


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (202, "thriving"),
        (203, "balanced"),
        (204, "watch"),
        (205, "attention"),
    ],
)
def test_get_health_state_from_carry_decodes_each_state(offset, expected):
    ps = [0.0] * 256
    ps[offset] = 1.0
    assert reality_bridge.get_health_state_from_carry(ps) == expected


def test_get_health_state_from_carry_returns_none_when_cold():
    ps = [0.0] * 256
    assert reality_bridge.get_health_state_from_carry(ps) is None


def test_get_health_state_from_carry_none_on_short_ps():
    assert reality_bridge.get_health_state_from_carry([]) is None
    assert reality_bridge.get_health_state_from_carry([0.0] * 202) is None


def test_get_health_state_from_carry_first_match_wins():
    """When multiple carry bits are HIGH, thriving wins."""
    ps = [0.0] * 256
    ps[202] = 1.0  # thriving
    ps[203] = 1.0  # balanced — must not override
    assert reality_bridge.get_health_state_from_carry(ps) == "thriving"


def test_carry_reads_202_not_190():
    """Carry decoder must read [202:206], not the live output at [190:194]."""
    ps = [0.0] * 256
    ps[190] = 1.0   # live output: thriving
    ps[203] = 1.0   # carry: balanced
    # get_health_state reads [190] → thriving
    assert reality_bridge.get_health_state(ps) == "thriving"
    # get_health_state_from_carry reads [202:206] → balanced
    assert reality_bridge.get_health_state_from_carry(ps) == "balanced"


# ── (6) push_carekit_signal() end-to-end ─────────────────────────────────────


@pytest.mark.parametrize(
    ("carekit_state_offset", "expected_state"),
    [
        (198, "adherent"),
        (199, "partial"),
        (200, "lapsed"),
        (201, "concern"),
    ],
)
def test_push_carekit_signal_returns_correct_state(monkeypatch, carekit_state_offset, expected_state):
    fake = _FakeCareKitClient(carekit_state_offset=carekit_state_offset)
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)

    state = reality_bridge.push_carekit_signal(
        med_adherence_ratio=0.9,
        task_completion_ratio=0.8,
        symptom_ok=1.0,
    )
    assert state == expected_state


def test_push_carekit_signal_writes_all_three_sensors(fake_carekit_client):
    """push_carekit_signal must POST to each of the three CareKit sensor endpoints."""
    reality_bridge.push_carekit_signal(
        med_adherence_ratio=1.0,
        task_completion_ratio=1.0,
        symptom_ok=1.0,
    )
    urls = [p["url"] for p in fake_carekit_client.posts]
    assert any("/api/sensors/localai_carekit_med_adherence"   in u for u in urls)
    assert any("/api/sensors/localai_carekit_task_completion" in u for u in urls)
    assert any("/api/sensors/localai_carekit_symptom_ok"      in u for u in urls)
    assert any("/api/push" in u for u in urls)


def test_push_carekit_signal_writes_correct_sensor_values(fake_carekit_client):
    reality_bridge.push_carekit_signal(
        med_adherence_ratio=0.75,
        task_completion_ratio=0.60,
        symptom_ok=0.0,
    )
    by_url = {p["url"]: p["json"] for p in fake_carekit_client.posts}
    med_url  = next(u for u in by_url if "med_adherence" in u)
    task_url = next(u for u in by_url if "task_completion" in u)
    symp_url = next(u for u in by_url if "symptom_ok" in u)

    assert by_url[med_url]["values"]  == [0.75]
    assert by_url[task_url]["values"] == [0.60]
    assert by_url[symp_url]["values"] == [0.0]


def test_push_carekit_signal_clamps_above_one(fake_carekit_client):
    """Values above 1.0 must be clamped to 1.0 before writing to the sensor."""
    reality_bridge.push_carekit_signal(
        med_adherence_ratio=2.5,
        task_completion_ratio=99.0,
        symptom_ok=1.5,
    )
    by_url = {p["url"]: p["json"] for p in fake_carekit_client.posts}
    for url, body in by_url.items():
        if "/api/sensors/" in url:
            v = body["values"][0]
            assert v <= 1.0, f"Sensor {url} value {v} not clamped to [0,1]"


def test_push_carekit_signal_clamps_below_zero(fake_carekit_client):
    """Values below 0.0 must be clamped to 0.0."""
    reality_bridge.push_carekit_signal(
        med_adherence_ratio=-0.5,
        task_completion_ratio=-1.0,
        symptom_ok=-99.0,
    )
    by_url = {p["url"]: p["json"] for p in fake_carekit_client.posts}
    for url, body in by_url.items():
        if "/api/sensors/" in url:
            v = body["values"][0]
            assert v >= 0.0, f"Sensor {url} value {v} not clamped to [0,1]"


def test_push_carekit_signal_falls_back_to_partial_on_pe_failure(monkeypatch):
    """When the PE is unreachable, push_carekit_signal must return 'partial'."""
    class _Raising(_FakeCareKitClient):
        def post(self, url, json=None, **_):
            if "/api/push" in url:
                raise RuntimeError("PE down")
            return super().post(url, json=json)

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _Raising())
    state = reality_bridge.push_carekit_signal(0.5, 0.5, 1.0)
    assert state == "partial", "'partial' is the safe default for CareKit fallback"


def test_push_carekit_signal_falls_back_on_short_ps(monkeypatch):
    """When PE returns a truncated perceptualSpace, fall back to 'partial'."""
    class _ShortPS(_FakeCareKitClient):
        def post(self, url, json=None, **_):
            self.posts.append({"url": url, "json": json})
            if "/api/push" in url:
                return _FakeResponse(200, {"step": {"perceptualSpace": [0.0] * 10}})
            return super().post(url, json=json)

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _ShortPS())
    state = reality_bridge.push_carekit_signal(0.9, 0.8, 1.0)
    assert state == "partial"


# ── (7) import_carekit_machine() idempotency ─────────────────────────────────


def test_import_carekit_machine_skips_when_machine_exists(monkeypatch):
    """import_carekit_machine must be idempotent — skip if the machine name exists."""
    class _WithMachine(_FakeCareKitClient):
        def get(self, url, **_):
            if "/api/machines" in url:
                return _FakeResponse(200, {
                    "machines": [{"name": reality_bridge._CAREKIT_MACHINE_NAME}],
                })
            return super().get(url)

    fake = _WithMachine()
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)

    reality_bridge.import_carekit_machine()
    machine_posts = [p for p in fake.posts if "/api/machines" in p["url"]]
    assert machine_posts == []


def test_import_carekit_machine_imports_when_missing(monkeypatch):
    """import_carekit_machine must POST the machine JSON when it is not loaded."""
    posted_names: list[str] = []

    class _Fresh(_FakeCareKitClient):
        def get(self, url, **_):
            if "/api/machines" in url:
                return _FakeResponse(200, {"machines": []})
            return super().get(url)

        def post(self, url, json=None, **_):
            if "/api/machines" in url and json:
                posted_names.append(
                    (json.get("machine") or {}).get("name", "")
                )
                return _FakeResponse(200, {"machine": {"id": "fake-ck-id"}})
            return super().post(url, json=json)

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _Fresh())

    reality_bridge.import_carekit_machine()
    assert reality_bridge._CAREKIT_MACHINE_NAME in posted_names


# ── (8) session_health_context.json ──────────────────────────────────────────


def _load_health_carry_machine() -> dict:
    path = reality_bridge._HEALTH_CARRY_MACHINE_PATH
    return json.loads(path.read_text())["machine"]


def test_session_health_context_perceptual_mapping():
    """session_health_context: input [190:194] (health output), output [202:206] (carry)."""
    machine = _load_health_carry_machine()
    pm = machine["perceptualMapping"]
    assert pm["input"]  == {"offset": 190, "length": 4}
    assert pm["output"] == {"offset": 202, "length": 4}


def test_session_health_context_has_four_sequences():
    """session_health_context.json must define exactly 4 sequences (one per health state)."""
    machine = _load_health_carry_machine()
    assert len(machine["sequences"]) == 4, (
        f"Expected 4 sequences, got {len(machine['sequences'])}"
    )


def test_session_health_context_all_sequences_are_initial():
    """All carry sequences must be isInitial (bistable flip-flop pattern)."""
    machine = _load_health_carry_machine()
    for seq in machine["sequences"]:
        vecs = seq["vectors"]
        assert len(vecs) == 1, f"{seq['id']}: expected 1 vector"
        assert vecs[0]["isInitial"] is True, f"{seq['id']}: must be isInitial"


def test_session_health_context_sequence_outputs_are_one_hot():
    """Each carry sequence writes a distinct one-hot to [202:206]."""
    machine = _load_health_carry_machine()
    expected = {
        "sess-health-thriving":  [1.0, 0.0, 0.0, 0.0],
        "sess-health-balanced":  [0.0, 1.0, 0.0, 0.0],
        "sess-health-watch":     [0.0, 0.0, 1.0, 0.0],
        "sess-health-attention": [0.0, 0.0, 0.0, 1.0],
    }
    for seq in machine["sequences"]:
        sid = seq["id"]
        if sid not in expected:
            continue
        out_vec = seq["vectors"][0]["outputVectors"][0]["vector"]
        assert out_vec == expected[sid], (
            f"{sid}: expected carry output {expected[sid]}, got {out_vec}"
        )


def test_session_health_context_is_in_session_machine_defs():
    """session_health_context must be in _SESSION_MACHINE_DEFS for auto-import."""
    names = {d["name"] for d in reality_bridge._SESSION_MACHINE_DEFS}
    assert reality_bridge._HEALTH_CARRY_MACHINE_NAME in names


# ── (9) get_session_context() includes health_state ──────────────────────────


def test_get_session_context_includes_health_state_key():
    """get_session_context() must always include the 'health_state' key."""
    ps = [0.0] * 256
    ctx = reality_bridge.get_session_context(ps)
    assert "health_state" in ctx, "health_state key missing from get_session_context()"


def test_get_session_context_health_state_from_carry():
    """When carry [202:206] is set, health_state must reflect the carry."""
    ps = [0.0] * 256
    ps[203] = 1.0   # carry: balanced
    ctx = reality_bridge.get_session_context(ps)
    assert ctx["health_state"] == "balanced"


def test_get_session_context_health_state_none_when_carry_cold():
    """When [202:206] is all zeros and [190:194] is all zeros, health_state is None."""
    ps = [0.0] * 256
    ctx = reality_bridge.get_session_context(ps)
    assert ctx["health_state"] is None


def test_get_session_context_health_state_on_short_ps():
    """get_session_context must tolerate a short ps (no IndexError)."""
    ctx = reality_bridge.get_session_context([])
    assert ctx["health_state"] is None


# ── (10) get_current_health_state() carry fallback ───────────────────────────


def test_get_current_health_state_uses_carry_when_live_is_silent(monkeypatch):
    """
    When [190:194] is all zero (personal_health_baseline not yet fired) but
    [202:206] has a state (carry from a prior push), get_current_health_state()
    must return the carry state rather than None.
    """
    class _CarryOnly(_FakeCareKitClient):
        def get(self, url, **_):
            if "/api/perceptual-simulation/state" in url:
                ps = [0.0] * 256
                ps[204] = 1.0   # carry: watch — live [190:194] is all-zero
                return _FakeResponse(200, {"state": {"perceptualSpace": ps}})
            return super().get(url)

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _CarryOnly())
    state = reality_bridge.get_current_health_state()
    assert state == "watch", (
        "Expected 'watch' from carry [204], but got: " + repr(state)
    )


def test_get_current_health_state_prefers_live_over_carry(monkeypatch):
    """
    When both [190:194] (live) and [202:206] (carry) are set, the live output
    must win — it is the most recent machine classification.
    """
    class _Both(_FakeCareKitClient):
        def get(self, url, **_):
            if "/api/perceptual-simulation/state" in url:
                ps = [0.0] * 256
                ps[190] = 1.0   # live: thriving
                ps[203] = 1.0   # carry: balanced (must be overridden)
                return _FakeResponse(200, {"state": {"perceptualSpace": ps}})
            return super().get(url)

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _Both())
    state = reality_bridge.get_current_health_state()
    assert state == "thriving", (
        "Live output at [190] should win over carry at [203]; got: " + repr(state)
    )


def test_get_current_health_state_returns_none_on_re_failure(monkeypatch):
    """When the RE is unreachable, get_current_health_state must return None."""
    class _Down(_FakeCareKitClient):
        def get(self, url, **_):
            raise RuntimeError("RE down")

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _Down())
    assert reality_bridge.get_current_health_state() is None


# ── (11) verify_machine_offsets() covers all sensor lists ────────────────────


def test_drift_guard_checks_carekit_sensors_not_just_rag(monkeypatch, tmp_path):
    """
    Regression test for the bug where verify_machine_offsets() only iterated
    _RAG_SENSORS. If a CareKit sensor is mapped to a non-existent machine, the
    guard must report it — not silently pass.
    """
    # Inject a carekit sensor pointing at a machine that doesn't exist in the table
    bad_carekit = [
        {
            "sensorId": "localai_carekit_fake_sensor",
            "name":     "localai/carekit/fake",
            "region":   {"offset": 194, "length": 1},
            "ttlMs":    3_600_000,
        }
    ]
    monkeypatch.setattr(reality_bridge, "_CAREKIT_SENSORS",
                        reality_bridge._CAREKIT_SENSORS + bad_carekit)
    # This sensor has no entry in _SENSOR_TO_MACHINE → must be flagged
    mismatches = reality_bridge.verify_machine_offsets()
    assert any(
        "localai_carekit_fake_sensor" in m for m in mismatches
    ), (
        "verify_machine_offsets() did not check CareKit sensors. "
        f"Mismatches returned: {mismatches}"
    )


def test_drift_guard_checks_health_sensors_not_just_rag(monkeypatch):
    """
    Same regression: a health sensor mapped to a non-existent machine must also
    be caught — confirming the fix iterates all three sensor lists.
    """
    bad_health = [
        {
            "sensorId": "localai_health_fake_sensor",
            "name":     "localai/health/fake",
            "region":   {"offset": 186, "length": 1},
            "ttlMs":    300_000,
        }
    ]
    monkeypatch.setattr(reality_bridge, "_HEALTH_SENSORS",
                        reality_bridge._HEALTH_SENSORS + bad_health)
    mismatches = reality_bridge.verify_machine_offsets()
    assert any(
        "localai_health_fake_sensor" in m for m in mismatches
    ), (
        "verify_machine_offsets() did not check health sensors. "
        f"Mismatches returned: {mismatches}"
    )


def test_drift_guard_all_sensor_lists_pass_clean():
    """
    With the production sensor lists unmodified, verify_machine_offsets() must
    return no CareKit or health sensor mismatches. (RAG and other machines may
    have pre-existing issues — we only assert the Phase 4 additions are clean.)
    """
    mismatches = reality_bridge.verify_machine_offsets()
    phase4_mismatches = [
        m for m in mismatches
        if any(k in m for k in (
            "localai_carekit_",
            "localai_health_",
            "medication_adherence",
            "session_health_context",
            "personal_health_baseline",
        ))
    ]
    assert phase4_mismatches == [], (
        "Phase 4 offset drift detected: " + " | ".join(phase4_mismatches)
    )


# ── (12) pe-integrations.json ─────────────────────────────────────────────────

_PE_INTEGRATIONS_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "config" / "pe-integrations.json"


def _load_pe_integrations() -> dict:
    return json.loads(_PE_INTEGRATIONS_PATH.read_text())


def test_pe_integrations_file_exists():
    assert _PE_INTEGRATIONS_PATH.exists(), (
        f"config/pe-integrations.json not found at {_PE_INTEGRATIONS_PATH}"
    )


def test_pe_integrations_version():
    data = _load_pe_integrations()
    assert data.get("version") == "1.0"


def test_pe_integrations_has_healthkit_integration():
    data = _load_pe_integrations()
    integrations = {i["id"]: i for i in data.get("integrations", [])}
    assert "healthkit-localai" in integrations, (
        "healthkit-localai integration missing from pe-integrations.json"
    )
    assert integrations["healthkit-localai"]["kind"] == "healthkit"
    assert integrations["healthkit-localai"]["enabled"] is True


def test_pe_integrations_has_carekit_integration():
    data = _load_pe_integrations()
    integrations = {i["id"]: i for i in data.get("integrations", [])}
    assert "carekit-localai" in integrations, (
        "carekit-localai integration missing from pe-integrations.json"
    )
    assert integrations["carekit-localai"]["kind"] == "carekit"
    assert integrations["carekit-localai"]["enabled"] is True


def test_pe_integrations_healthkit_source_mappings():
    """HealthKit source mappings must cover HR, HRV, and Sleep at offsets 186–188."""
    data = _load_pe_integrations()
    by_id = {m["id"]: m for m in data.get("sourceMappings", [])}
    hk_hr  = by_id.get("healthkit:HKQuantityTypeIdentifierHeartRate")
    hk_hrv = by_id.get("healthkit:HKQuantityTypeIdentifierHeartRateVariabilitySDNN")
    hk_sl  = by_id.get("healthkit:HKCategoryTypeIdentifierSleepAnalysis")

    assert hk_hr  is not None, "HK HR source mapping missing"
    assert hk_hrv is not None, "HK HRV source mapping missing"
    assert hk_sl  is not None, "HK Sleep source mapping missing"

    assert hk_hr["region"]["offset"]  == 186
    assert hk_hrv["region"]["offset"] == 187
    assert hk_sl["region"]["offset"]  == 188


def test_pe_integrations_carekit_source_mappings():
    """CareKit source mappings must cover med, task, symptom at offsets 194–196."""
    data = _load_pe_integrations()
    by_id = {m["id"]: m for m in data.get("sourceMappings", [])}
    ck_med  = by_id.get("carekit-localai-med")
    ck_task = by_id.get("carekit-localai-task")
    ck_symp = by_id.get("carekit-localai-symptom")

    assert ck_med  is not None, "CareKit med source mapping missing"
    assert ck_task is not None, "CareKit task source mapping missing"
    assert ck_symp is not None, "CareKit symptom source mapping missing"

    assert ck_med["region"]["offset"]  == 194
    assert ck_task["region"]["offset"] == 195
    assert ck_symp["region"]["offset"] == 196


def test_pe_integrations_source_mapping_regions_match_sensor_constants():
    """
    The PE registry file's region offsets must agree with the Python sensor
    constants in reality_bridge._CAREKIT_SENSORS and _HEALTH_SENSORS.
    """
    data = _load_pe_integrations()
    by_sensor_id = {m.get("sensorId"): m for m in data.get("sourceMappings", []) if m.get("sensorId")}

    all_sensors = reality_bridge._HEALTH_SENSORS + reality_bridge._CAREKIT_SENSORS
    for sensor in all_sensors:
        sid = sensor["sensorId"]
        if sid not in by_sensor_id:
            continue
        json_offset = by_sensor_id[sid]["region"]["offset"]
        py_offset   = sensor["region"]["offset"]
        assert json_offset == py_offset, (
            f"Offset mismatch for {sid}: "
            f"pe-integrations.json says {json_offset}, "
            f"reality_bridge says {py_offset}"
        )
