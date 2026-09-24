"""
Health integration tests for core.reality_bridge — personal health domain.

Mirrors the structure of test_reality_bridge.py:

  (1) Offset-drift guard — verifies personal_health_baseline.json offsets
      match the Python constants added to reality_bridge.

  (2) Decoder unit tests — get_health_state() maps perceptualSpace[7578:7582]
      to the correct string for each of the four health states.

  (3) End-to-end push cycle — exercises push_health_signal() with a fake
      PE/RE; confirms band grading, the worst-band-wins roll-up write, and
      state decoding.

  (4) Bridge robustness — PE unreachable falls back to "watch".

All tests are network-free; httpx.Client is monkeypatched with a fake that
records requests and returns canned responses containing a 7680-element
perceptualSpace vector with one state asserted in [7578:7582].
"""

from __future__ import annotations

import json

import pytest

from core import reality_bridge

# ── (1) Offset-drift guard ────────────────────────────────────────────────────


def test_personal_health_baseline_offsets_check_in_tree():
    """personal_health_baseline.json must agree with the Python constants."""
    mismatches = reality_bridge.verify_machine_offsets()
    health_mismatches = [m for m in mismatches if "personal_health_baseline" in m]
    assert health_mismatches == [], "personal_health_baseline.json offset drift: " + " | ".join(
        health_mismatches
    )


def test_personal_health_baseline_is_in_expected_offsets_table():
    """The drift guard must cover personal_health_baseline.json."""
    filenames = {spec["path"].name for spec in reality_bridge._EXPECTED_MACHINE_OFFSETS}
    assert "personal_health_baseline.json" in filenames


def test_drift_guard_catches_health_machine_offset_mutation(tmp_path, monkeypatch):
    """If personal_health_baseline.json input offset moves, the guard reports it."""
    bad_dir = tmp_path / "machines"
    bad_dir.mkdir()
    for p in reality_bridge._MACHINES_DIR.glob("*.json"):
        (bad_dir / p.name).write_text(p.read_text())

    bad_file = bad_dir / "personal_health_baseline.json"
    data = json.loads(bad_file.read_text())
    data["machine"]["perceptualMapping"]["input"]["offset"] = 999
    bad_file.write_text(json.dumps(data))

    patched = [dict(spec) for spec in reality_bridge._EXPECTED_MACHINE_OFFSETS]
    for spec in patched:
        spec["path"] = bad_dir / spec["path"].name
    monkeypatch.setattr(reality_bridge, "_EXPECTED_MACHINE_OFFSETS", patched)

    mismatches = reality_bridge.verify_machine_offsets()
    assert any("personal_health_baseline.json" in m and "input" in m for m in mismatches), (
        f"expected health machine input mismatch in: {mismatches}"
    )


def test_health_sensors_are_registered_in_sensor_to_machine():
    """The roll-up sensor maps to personal_health_baseline.json; the legacy three do not."""
    assert (
        reality_bridge._SENSOR_TO_MACHINE.get("localai_health_rollup")
        == "personal_health_baseline.json"
    )
    for sid in reality_bridge._LEGACY_HEALTH_SENSOR_IDS:
        assert sid not in reality_bridge._SENSOR_TO_MACHINE


def test_rollup_sensor_fills_health_machine_input_window():
    """The roll-up is the whole [7574:7578] window: one writer, not four."""
    assert [s["sensorId"] for s in reality_bridge._HEALTH_SENSORS] == ["localai_health_rollup"]
    assert reality_bridge._HEALTH_SENSORS[0]["region"] == {"offset": 7574, "length": 4}


def test_health_sensors_are_inside_health_machine_input_window():
    """
    Each health sensor's region must lie inside personal_health_baseline.json's
    input window [7574:7578]. If sensor offsets drift outward, the machine cannot
    read them — the drift guard must catch this.
    """
    spec = next(
        s
        for s in reality_bridge._EXPECTED_MACHINE_OFFSETS
        if s["path"].name == "personal_health_baseline.json"
    )
    m_start = spec["input"]["offset"]
    m_end = m_start + spec["input"]["length"]

    for sensor in reality_bridge._HEALTH_SENSORS:
        sr = sensor["region"]
        assert sr["offset"] >= m_start, (
            f"{sensor['sensorId']} offset {sr['offset']} < machine input start {m_start}"
        )
        assert sr["offset"] + sr["length"] <= m_end, (
            f"{sensor['sensorId']} end {sr['offset'] + sr['length']} > machine input end {m_end}"
        )


# ── (2) Decoder unit tests ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (7578, "thriving"),
        (7579, "balanced"),
        (7580, "watch"),
        (7581, "attention"),
    ],
)
def test_get_health_state_decodes_each_state(offset, expected):
    ps = [0.0] * 7680
    ps[offset] = 1.0
    assert reality_bridge.get_health_state(ps) == expected


def test_get_health_state_returns_none_when_machine_silent():
    ps = [0.0] * 7680
    assert reality_bridge.get_health_state(ps) is None


def test_get_health_state_none_on_short_ps():
    assert reality_bridge.get_health_state([]) is None
    assert reality_bridge.get_health_state([0.0] * 7578) is None


def test_get_health_state_first_match_wins():
    """When multiple bits are set (shouldn't happen in practice), thriving wins."""
    ps = [0.0] * 7680
    ps[7578] = 1.0  # thriving
    ps[7579] = 1.0  # balanced — should not override thriving
    assert reality_bridge.get_health_state(ps) == "thriving"


# ── (3) Band normalization ────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status: int = 200, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._body


class _FakeHealthClient:
    """httpx.Client stand-in for health sensor tests. Records every POST."""

    def __init__(self, health_state_offset: int = 7578):
        self.posts: list[dict] = []
        self._health_state_offset = health_state_offset

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
            ps = [0.0] * 7680
            ps[self._health_state_offset] = 1.0
            return _FakeResponse(
                200,
                {
                    "step": {"perceptualSpace": ps},
                    "globalStep": 1,
                },
            )
        return _FakeResponse(200, {"ok": True})


@pytest.fixture
def fake_health_client(monkeypatch):
    fake = _FakeHealthClient(health_state_offset=7578)  # thriving
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)
    return fake


def _rollup_posts(fake) -> list[list[float]]:
    return [
        p["json"]["values"] for p in fake.posts if "/api/sensors/localai_health_rollup" in p["url"]
    ]


def test_push_health_signal_thriving_writes_rollup(fake_health_client):
    """HR=80, HRV=45ms, sleep=7.5h: every band ok → roll-up thriving [1,0,0,0]."""
    state = reality_bridge.push_health_signal(hr_bpm=80.0, hrv_sdnn_ms=45.0, sleep_hours=7.5)
    assert state == "thriving"

    urls = [p["url"] for p in fake_health_client.posts]
    assert _rollup_posts(fake_health_client) == [[1.0, 0.0, 0.0, 0.0]]
    assert any("/api/push" in u for u in urls)
    # Declared before its first write, inactive (core/pe_sources.py).
    decl = next(p for p in fake_health_client.posts if p["url"].endswith("/api/sources"))
    assert decl["json"]["sensorId"] == "localai_health_rollup"
    assert decl["json"]["active"] is False
    for sid in reality_bridge._LEGACY_HEALTH_SENSOR_IDS:
        assert not any(sid in u for u in urls), f"legacy sensor {sid} written"


@pytest.mark.parametrize(
    ("hr", "hrv", "sleep", "grades", "rollup"),
    [
        # grades: pulse, hrv, sleep — against data/health/health_bands.json
        (80.0, 45.0, 7.5, ("ok", "ok", "ok"), [1.0, 0.0, 0.0, 0.0]),
        (60.0, 30.0, 6.5, ("ok", "ok", "ok"), [1.0, 0.0, 0.0, 0.0]),  # lower bounds ok
        (100.0, 45.0, 7.5, ("ok", "ok", "ok"), [1.0, 0.0, 0.0, 0.0]),  # upper bound ok
        (75.0, 38.0, 5.5, ("ok", "ok", "watch"), [0.0, 1.0, 0.0, 0.0]),  # one watch
        (110.0, 25.0, 7.0, ("watch", "watch", "ok"), [0.0, 0.0, 1.0, 0.0]),  # two watch
        (70.0, 18.0, 6.0, ("ok", "concern", "watch"), [0.0, 0.0, 0.0, 1.0]),  # concern wins
        (130.0, 45.0, 7.5, ("concern", "ok", "ok"), [0.0, 0.0, 0.0, 1.0]),
        (45.0, 45.0, 7.5, ("concern", "ok", "ok"), [0.0, 0.0, 0.0, 1.0]),
        (80.0, 45.0, 4.0, ("ok", "ok", "concern"), [0.0, 0.0, 0.0, 1.0]),
    ],
)
def test_push_health_signal_grades_worst_band_wins(monkeypatch, hr, hrv, sleep, grades, rollup):
    assert tuple(reality_bridge.health_grades(hr, hrv, sleep).values()) == grades
    fake = _FakeHealthClient(health_state_offset=7578 + rollup.index(1.0))
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)
    reality_bridge.push_health_signal(hr_bpm=hr, hrv_sdnn_ms=hrv, sleep_hours=sleep)
    assert _rollup_posts(fake) == [rollup]


def test_push_health_signal_returns_state_the_machine_asserted(monkeypatch):
    """The returned state is the RE's, read back from [7578:7582], not the local roll-up."""
    fake = _FakeHealthClient(health_state_offset=7580)  # RE says watch
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)
    assert reality_bridge.push_health_signal(80.0, 45.0, 7.5) == "watch"


def test_register_sensors_removes_legacy_health_sensors(monkeypatch):
    """A PE still holding hr_ok/hrv_ok/sleep_ok loses them: they overlap the roll-up."""
    legacy = [
        {"id": f"src-{i}", "type": "sensor", "sensorId": sid, "active": True, "lastValue": [1.0]}
        for i, sid in enumerate(reality_bridge._LEGACY_HEALTH_SENSOR_IDS)
    ]

    class _WithLegacy(_FakeHealthClient):
        def __init__(self):
            super().__init__()
            self.deletes: list[str] = []

        def get(self, url, **_):
            if url.endswith("/api/sources"):
                return _FakeResponse(200, {"sources": legacy})
            return super().get(url)

        def delete(self, url, **_):
            self.deletes.append(url)
            return _FakeResponse(200, {})

        def patch(self, url, json=None, **_):
            return _FakeResponse(200, {})

    fake = _WithLegacy()
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)
    monkeypatch.setattr(
        reality_bridge, "_re_targets", lambda: [{"pe_url": "http://pe", "instance": "t"}]
    )
    assert reality_bridge.register_sensors() is True
    assert sorted(fake.deletes) == [f"http://pe/api/sources/src-{i}" for i in range(3)]


# ── (4) Bridge robustness ─────────────────────────────────────────────────────


def test_push_health_signal_falls_back_to_watch_on_pe_failure(monkeypatch):
    """When the PE is unreachable, push_health_signal must return 'watch'."""

    class _Raising(_FakeHealthClient):
        def post(self, url, json=None, **_):
            if "/api/push" in url:
                raise RuntimeError("PE down")
            return super().post(url, json=json)

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _Raising())

    state = reality_bridge.push_health_signal(80.0, 40.0, 7.5)
    assert state == "watch", "'watch' is the safe default — mindful without assuming crisis"


def test_push_health_signal_short_ps_falls_back_to_watch(monkeypatch):
    """When the PE returns a truncated perceptualSpace, fall back to 'watch'."""

    class _ShortPS(_FakeHealthClient):
        def post(self, url, json=None, **_):
            self.posts.append({"url": url, "json": json})
            if "/api/push" in url:
                return _FakeResponse(200, {"step": {"perceptualSpace": [0.0] * 10}})
            return super().post(url, json=json)

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _ShortPS())

    state = reality_bridge.push_health_signal(80.0, 40.0, 7.5)
    assert state == "watch"


# ── (5) import_health_machines() ─────────────────────────────────────────────


def test_import_health_machines_skips_when_machine_exists(monkeypatch, fake_health_client):
    """import_health_machines must be idempotent — skip if the machine name exists."""

    class _WithMachine(_FakeHealthClient):
        def get(self, url, **_):
            if "/api/machines" in url:
                return _FakeResponse(
                    200,
                    {
                        "machines": [{"name": reality_bridge._HEALTH_MACHINE_NAME}],
                    },
                )
            return super().get(url)

    fake = _WithMachine()
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)

    reality_bridge.import_health_machines()
    # No POST to /api/machines should have been made
    machine_posts = [p for p in fake.posts if "/api/machines" in p["url"]]
    assert machine_posts == []


def test_import_health_machines_imports_when_missing(monkeypatch, fake_health_client):
    """import_health_machines must POST the machine JSON when it's not loaded."""
    posted_names: list[str] = []

    class _Fresh(_FakeHealthClient):
        def get(self, url, **_):
            if "/api/machines" in url:
                return _FakeResponse(200, {"machines": []})
            return super().get(url)

        def post(self, url, json=None, **_):
            if "/api/machines" in url and json:
                posted_names.append((json.get("machine") or {}).get("name", ""))
                return _FakeResponse(200, {"machine": {"id": "fake-health-id"}})
            return super().post(url, json=json)

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _Fresh())

    reality_bridge.import_health_machines()
    assert reality_bridge._HEALTH_MACHINE_NAME in posted_names
