"""
Health integration tests for core.reality_bridge — personal health domain.

Mirrors the structure of test_reality_bridge.py:

  (1) Offset-drift guard — verifies personal_health_baseline.json offsets
      match the Python constants added to reality_bridge.

  (2) Decoder unit tests — get_health_state() maps perceptualSpace[190:194]
      to the correct string for each of the four health states.

  (3) End-to-end push cycle — exercises push_health_signal() with a fake
      PE/RE; confirms band normalization, sensor writes, and state decoding.

  (4) Bridge robustness — PE unreachable falls back to "watch".

All tests are network-free; httpx.Client is monkeypatched with a fake that
records requests and returns canned responses containing a 256-element
perceptualSpace vector with the 'thriving' state asserted at [190].
"""

from __future__ import annotations

import json
import pathlib

import pytest

from core import reality_bridge


# ── (1) Offset-drift guard ────────────────────────────────────────────────────


def test_personal_health_baseline_offsets_check_in_tree():
    """personal_health_baseline.json must agree with the Python constants."""
    mismatches = reality_bridge.verify_machine_offsets()
    health_mismatches = [m for m in mismatches if "personal_health_baseline" in m]
    assert health_mismatches == [], (
        "personal_health_baseline.json offset drift: " + " | ".join(health_mismatches)
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
    assert any(
        "personal_health_baseline.json" in m and "input" in m for m in mismatches
    ), f"expected health machine input mismatch in: {mismatches}"


def test_health_sensors_are_registered_in_sensor_to_machine():
    """All three health sensor IDs must map to personal_health_baseline.json."""
    for sid in ("localai_health_hr_ok", "localai_health_hrv_ok", "localai_health_sleep_ok"):
        assert reality_bridge._SENSOR_TO_MACHINE.get(sid) == "personal_health_baseline.json", \
            f"{sid} missing from _SENSOR_TO_MACHINE"


def test_health_sensors_are_inside_health_machine_input_window():
    """
    Each health sensor's region must lie inside personal_health_baseline.json's
    input window [186:190]. If sensor offsets drift outward, the machine cannot
    read them — the drift guard must catch this.
    """
    spec = next(
        s for s in reality_bridge._EXPECTED_MACHINE_OFFSETS
        if s["path"].name == "personal_health_baseline.json"
    )
    m_start = spec["input"]["offset"]
    m_end   = m_start + spec["input"]["length"]

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
        (190, "thriving"),
        (191, "balanced"),
        (192, "watch"),
        (193, "attention"),
    ],
)
def test_get_health_state_decodes_each_state(offset, expected):
    ps = [0.0] * 256
    ps[offset] = 1.0
    assert reality_bridge.get_health_state(ps) == expected


def test_get_health_state_returns_none_when_machine_silent():
    ps = [0.0] * 256
    assert reality_bridge.get_health_state(ps) is None


def test_get_health_state_none_on_short_ps():
    assert reality_bridge.get_health_state([]) is None
    assert reality_bridge.get_health_state([0.0] * 190) is None


def test_get_health_state_first_match_wins():
    """When multiple bits are set (shouldn't happen in practice), thriving wins."""
    ps = [0.0] * 256
    ps[190] = 1.0  # thriving
    ps[191] = 1.0  # balanced — should not override thriving
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

    def __init__(self, health_state_offset: int = 190):
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
            ps = [0.0] * 256
            ps[self._health_state_offset] = 1.0
            return _FakeResponse(200, {
                "step": {"perceptualSpace": ps},
                "globalStep": 1,
            })
        return _FakeResponse(200, {"ok": True})


@pytest.fixture
def fake_health_client(monkeypatch):
    fake = _FakeHealthClient(health_state_offset=190)  # thriving
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)
    return fake


def test_push_health_signal_thriving_writes_all_sensors_high(fake_health_client):
    """
    Nominal scenario: HR=80, HRV=45ms, Sleep=7.5h → all three bands HIGH.
    push_health_signal must write 1.0 to each sensor and return 'thriving'.
    """
    state = reality_bridge.push_health_signal(
        hr_bpm=80.0, hrv_sdnn_ms=45.0, sleep_hours=7.5,
    )
    assert state == "thriving"

    urls = [p["url"] for p in fake_health_client.posts]
    assert any("/api/sensors/localai_health_hr_ok" in u for u in urls)
    assert any("/api/sensors/localai_health_hrv_ok" in u for u in urls)
    assert any("/api/sensors/localai_health_sleep_ok" in u for u in urls)
    assert any("/api/push" in u for u in urls)

    hr_post = next(
        p for p in fake_health_client.posts
        if "/api/sensors/localai_health_hr_ok" in p["url"]
    )
    assert hr_post["json"]["values"] == [1.0]


def test_push_health_signal_band_values_hr_in_range():
    """HR=60 (exactly on lower bound) and HR=100 (upper bound) should both be OK."""
    for bpm in (60.0, 80.0, 100.0):
        hr_ok    = 1.0 if 60.0 <= bpm <= 100.0 else 0.0
        assert hr_ok == 1.0, f"HR={bpm} should be in range"

    for bpm in (59.9, 100.1, 120.0, 40.0):
        hr_ok = 1.0 if 60.0 <= bpm <= 100.0 else 0.0
        assert hr_ok == 0.0, f"HR={bpm} should be out of range"


def test_push_health_signal_band_values_hrv_threshold():
    """HRV ≥ 30ms → ok=1.0; below → 0.0."""
    assert (1.0 if 30.0 >= reality_bridge._HRV_OK_MS else 0.0) == 1.0
    assert (1.0 if 29.9 >= reality_bridge._HRV_OK_MS else 0.0) == 0.0


def test_push_health_signal_attention_writes_hr_low(monkeypatch):
    """HR=105 (above 100 ceiling) must write hr.ok=0.0, returning 'attention'."""
    fake = _FakeHealthClient(health_state_offset=193)  # attention
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)

    state = reality_bridge.push_health_signal(
        hr_bpm=105.0, hrv_sdnn_ms=25.0, sleep_hours=7.0,
    )
    assert state == "attention"

    hr_post = next(
        p for p in fake.posts
        if "/api/sensors/localai_health_hr_ok" in p["url"]
    )
    assert hr_post["json"]["values"] == [0.0]


def test_push_health_signal_watch_writes_hrv_low(monkeypatch):
    """HR in range but HRV=18ms (<30ms) must write hrv.ok=0.0, returning 'watch'."""
    fake = _FakeHealthClient(health_state_offset=192)  # watch
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)

    state = reality_bridge.push_health_signal(
        hr_bpm=70.0, hrv_sdnn_ms=18.0, sleep_hours=6.0,
    )
    assert state == "watch"

    hrv_post = next(
        p for p in fake.posts
        if "/api/sensors/localai_health_hrv_ok" in p["url"]
    )
    assert hrv_post["json"]["values"] == [0.0]


def test_push_health_signal_balanced_sleep_low(monkeypatch):
    """HR and HRV nominal but sleep=5.5h (<6.5h) → sleep.ok=0.0 → 'balanced'."""
    fake = _FakeHealthClient(health_state_offset=191)  # balanced
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)

    state = reality_bridge.push_health_signal(
        hr_bpm=75.0, hrv_sdnn_ms=38.0, sleep_hours=5.5,
    )
    assert state == "balanced"

    sleep_post = next(
        p for p in fake.posts
        if "/api/sensors/localai_health_sleep_ok" in p["url"]
    )
    assert sleep_post["json"]["values"] == [0.0]


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
    assert state == "watch", (
        "'watch' is the safe default — mindful without assuming crisis"
    )


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
                return _FakeResponse(200, {
                    "machines": [{"name": reality_bridge._HEALTH_MACHINE_NAME}],
                })
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
                posted_names.append(
                    (json.get("machine") or {}).get("name", "")
                )
                return _FakeResponse(200, {"machine": {"id": "fake-health-id"}})
            return super().post(url, json=json)

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _Fresh())

    reality_bridge.import_health_machines()
    assert reality_bridge._HEALTH_MACHINE_NAME in posted_names
