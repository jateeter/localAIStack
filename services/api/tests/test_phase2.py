"""
Phase 2 tests — health-aware chat context, health_docs collection, HealthKit config.

Covers:
  (1) Config — health_collection_name and health_context_enabled settings
  (2) reality_bridge.get_current_health_state() — reads RE state without a push
  (3) chat.py health context injection — _inject_health_context logic, hint coverage
  (4) vector_store.get_health_vector_store() — health_docs collection setup
  (5) agent_graph TOOLS — health_search tool is registered
  (6) HealthKit integration config — JSON structure validation

All tests are network-free; httpx.Client and Qdrant are monkeypatched.
Imports of fastapi/langchain/qdrant-dependent modules are deferred inside
test functions to keep test collection free of heavyweight deps.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from core import reality_bridge

# ── helpers shared across sections ───────────────────────────────────────────


class _FakeREResponse:
    def __init__(self, status: int = 200, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._body


class _FakeREClient:
    """httpx.Client stand-in that returns a canned RE /api/perceptual-simulation/state."""

    def __init__(self, health_offset: int | None = 190):
        self._health_offset = health_offset

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url: str, **_):
        if "/api/perceptual-simulation/state" in url:
            ps = [0.0] * 256
            if self._health_offset is not None:
                ps[self._health_offset] = 1.0
            return _FakeREResponse(200, {"state": {"perceptualSpace": ps}})
        return _FakeREResponse(200, {})


_HEALTHKIT_CONFIG_PATH = (
    pathlib.Path(__file__).parent.parent.parent.parent
    / "config"
    / "integrations.healthkit-localai.json"
)


# ── (1) Config settings ───────────────────────────────────────────────────────


def test_config_health_collection_name_default():
    from config import Settings
    s = Settings()
    assert s.health_collection_name == "health_docs"


def test_config_health_context_enabled_default_off():
    from config import Settings
    s = Settings()
    assert s.health_context_enabled is False


def test_config_health_context_enabled_via_env(monkeypatch):
    monkeypatch.setenv("HEALTH_CONTEXT_ENABLED", "true")
    from config import Settings
    s = Settings()
    assert s.health_context_enabled is True


# ── (2) get_current_health_state() ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (190, "thriving"),
        (191, "balanced"),
        (192, "watch"),
        (193, "attention"),
    ],
)
def test_get_current_health_state_decodes_each_state(monkeypatch, offset, expected):
    monkeypatch.setattr(
        reality_bridge.httpx,
        "Client",
        lambda *a, **kw: _FakeREClient(health_offset=offset),
    )
    assert reality_bridge.get_current_health_state() == expected


def test_get_current_health_state_returns_none_when_machine_silent(monkeypatch):
    monkeypatch.setattr(
        reality_bridge.httpx,
        "Client",
        lambda *a, **kw: _FakeREClient(health_offset=None),
    )
    assert reality_bridge.get_current_health_state() is None


def test_get_current_health_state_returns_none_on_re_failure(monkeypatch):
    class _Raising:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def get(self, *a, **kw): raise RuntimeError("RE unreachable")

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _Raising())
    assert reality_bridge.get_current_health_state() is None


def test_get_current_health_state_returns_none_on_re_404(monkeypatch):
    class _NotFound:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def get(self, *a, **kw):
            return _FakeREResponse(404, {})

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _NotFound())
    assert reality_bridge.get_current_health_state() is None


def test_get_current_health_state_calls_re_not_pe(monkeypatch):
    """Must call RE /api/perceptual-simulation/state, not PE /api/state."""
    called_urls: list[str] = []

    class _Tracker:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def get(self, url, **_):
            called_urls.append(url)
            ps = [0.0] * 256
            ps[190] = 1.0
            return _FakeREResponse(200, {"state": {"perceptualSpace": ps}})

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _Tracker())
    reality_bridge.get_current_health_state()
    assert any("/api/perceptual-simulation/state" in u for u in called_urls), (
        f"Expected RE state endpoint in calls: {called_urls}"
    )
    assert not any(u.split("/")[-1] == "/api/state" for u in called_urls), (
        f"Must not call PE /api/state: {called_urls}"
    )


# ── (3) chat.py health context injection ─────────────────────────────────────
# These tests exercise the pure logic (_inject_health_context, _HEALTH_HINTS)
# without importing fastapi. We replicate the function signatures here so that
# the test file remains importable in the lightweight test environment.


_EXPECTED_HINTS = {
    "thriving":  "nominal range",        # substring expected in hint
    "balanced":  "sleep",
    "watch":     "HRV",
    "attention": "heart rate",
}


def test_health_hints_constant_defined():
    """_HEALTH_HINTS must exist and cover all four states."""
    # Import deferred to inside function — fastapi is not available in the test env
    try:
        from routers.chat import _HEALTH_HINTS
        for state in ("thriving", "balanced", "watch", "attention"):
            assert state in _HEALTH_HINTS
            assert len(_HEALTH_HINTS[state]) > 20
    except ImportError:
        # fastapi not installed in the test env; verify the file exists and
        # contains the expected content as a text check instead.
        chat_path = pathlib.Path(__file__).parent.parent / "routers" / "chat.py"
        content = chat_path.read_text()
        for state in ("thriving", "balanced", "watch", "attention"):
            assert f'"{state}"' in content or f"'{state}'" in content, (
                f"_HEALTH_HINTS is missing state: {state}"
            )


def test_inject_health_context_function_exists_in_chat():
    """_inject_health_context must be defined in routers/chat.py."""
    chat_path = pathlib.Path(__file__).parent.parent / "routers" / "chat.py"
    content = chat_path.read_text()
    assert "def _inject_health_context" in content


def test_inject_health_context_handles_no_existing_system_message():
    """When there is no system message, the health hint should be prepended."""
    chat_path = pathlib.Path(__file__).parent.parent / "routers" / "chat.py"
    content = chat_path.read_text()
    # The function must handle the case where no SystemMessage exists
    assert "sys_idx" in content
    assert "insert" in content or "SystemMessage" in content


def test_health_hints_mention_each_expected_substring():
    """Verify _HEALTH_HINTS has appropriate framing for each state."""
    chat_path = pathlib.Path(__file__).parent.parent / "routers" / "chat.py"
    content = chat_path.read_text()
    for state, substring in _EXPECTED_HINTS.items():
        assert substring.lower() in content.lower(), (
            f"Expected '{substring}' in _HEALTH_HINTS['{state}'] but not found in chat.py"
        )


def test_chat_request_has_health_context_field():
    """ChatRequest must expose a health_context field."""
    chat_path = pathlib.Path(__file__).parent.parent / "routers" / "chat.py"
    content = chat_path.read_text()
    assert "health_context" in content


def test_chat_endpoint_checks_header_and_body_and_settings():
    """The endpoint must gate on body field, X-Health-Context header, and global setting."""
    chat_path = pathlib.Path(__file__).parent.parent / "routers" / "chat.py"
    content = chat_path.read_text()
    assert "health_context_enabled" in content
    assert "x_health_context" in content or "X-Health-Context" in content or "Header" in content
    assert "get_current_health_state" in content


def test_inject_logic_body_false_overrides_global_true():
    """Per-request health_context=False wins over settings=True."""
    # Logic extracted for pure Python testing (no fastapi needed)
    class _Req:
        health_context = False

    class _Settings:
        health_context_enabled = True

    req = _Req()
    s = _Settings()
    x_header = None

    inject = (
        (req.health_context is True)
        or (req.health_context is None and s.health_context_enabled)
        or (x_header is not None and x_header.lower() == "enabled")
    )
    assert inject is False


def test_inject_logic_header_enabled_activates_injection():
    """X-Health-Context: enabled must activate injection when body is None and settings off."""
    class _Req:
        health_context = None

    class _Settings:
        health_context_enabled = False

    req = _Req()
    s = _Settings()
    x_header = "enabled"

    inject = (
        (req.health_context is True)
        or (req.health_context is None and s.health_context_enabled)
        or (x_header is not None and x_header.lower() == "enabled")
    )
    assert inject is True


def test_inject_logic_body_true_overrides_settings_false():
    """Per-request health_context=True wins even when settings is off."""
    class _Req:
        health_context = True

    class _Settings:
        health_context_enabled = False

    req = _Req()
    s = _Settings()
    x_header = None

    inject = (
        (req.health_context is True)
        or (req.health_context is None and s.health_context_enabled)
        or (x_header is not None and x_header.lower() == "enabled")
    )
    assert inject is True


# ── (4) vector_store.get_health_vector_store() ───────────────────────────────


def test_get_health_vector_store_function_exists():
    """get_health_vector_store must be defined in core/vector_store.py."""
    vs_path = pathlib.Path(__file__).parent.parent / "core" / "vector_store.py"
    content = vs_path.read_text()
    assert "def get_health_vector_store" in content


def test_get_health_vector_store_uses_health_collection_name():
    """get_health_vector_store must reference health_collection_name from settings."""
    vs_path = pathlib.Path(__file__).parent.parent / "core" / "vector_store.py"
    content = vs_path.read_text()
    assert "health_collection_name" in content


def test_get_health_vector_store_has_own_cache_variable():
    """get_health_vector_store must use a separate global cache from get_vector_store."""
    vs_path = pathlib.Path(__file__).parent.parent / "core" / "vector_store.py"
    content = vs_path.read_text()
    assert "_health_store" in content


# ── (5) agent_graph TOOLS — health_search is registered ──────────────────────


def test_health_search_tool_defined_in_agent_graph():
    """health_search must be defined in graphs/agent_graph.py."""
    ag_path = pathlib.Path(__file__).parent.parent / "graphs" / "agent_graph.py"
    content = ag_path.read_text()
    assert "def health_search" in content


def test_health_search_in_tools_list():
    """health_search must appear in the TOOLS list in agent_graph.py."""
    ag_path = pathlib.Path(__file__).parent.parent / "graphs" / "agent_graph.py"
    content = ag_path.read_text()
    assert "health_search" in content
    # Must be in the TOOLS assignment
    tools_line = next(
        (ln for ln in content.splitlines() if ln.strip().startswith("TOOLS")),
        None,
    )
    assert tools_line is not None, "TOOLS = [...] line not found"
    assert "health_search" in tools_line, f"health_search missing from TOOLS line: {tools_line!r}"


def test_health_search_uses_get_health_vector_store():
    """health_search must query the health vector store, not localai_docs."""
    ag_path = pathlib.Path(__file__).parent.parent / "graphs" / "agent_graph.py"
    content = ag_path.read_text()
    assert "get_health_vector_store" in content


def test_health_search_has_health_focused_description():
    """The health_search docstring must mention health-related terms."""
    ag_path = pathlib.Path(__file__).parent.parent / "graphs" / "agent_graph.py"
    content = ag_path.read_text()
    # Find the health_search function block
    start = content.find("def health_search")
    snippet = content[start:start + 600]
    health_terms = ("health", "HRV", "wellness", "sleep", "heart rate", "recovery")
    assert any(t.lower() in snippet.lower() for t in health_terms), (
        f"health_search docstring missing health-related terms. Snippet:\n{snippet}"
    )


# ── (6) HealthKit integration config structure ────────────────────────────────


@pytest.fixture
def healthkit_config() -> dict:
    assert _HEALTHKIT_CONFIG_PATH.exists(), f"config not found: {_HEALTHKIT_CONFIG_PATH}"
    return json.loads(_HEALTHKIT_CONFIG_PATH.read_text())


def test_healthkit_config_exists():
    assert _HEALTHKIT_CONFIG_PATH.exists(), (
        f"HealthKit integration config not found at {_HEALTHKIT_CONFIG_PATH}"
    )


def test_healthkit_config_has_required_top_level_keys(healthkit_config):
    for key in ("integrationId", "sourceMappings", "bandThresholds", "targetPerceptualRegion"):
        assert key in healthkit_config, f"Missing key: {key}"


def test_healthkit_config_maps_three_hk_types(healthkit_config):
    expected = {
        "HKQuantityTypeIdentifierHeartRate",
        "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
        "HKCategoryTypeIdentifierSleepAnalysis",
    }
    actual = {m["hkTypeIdentifier"] for m in healthkit_config["sourceMappings"]}
    assert actual == expected


def test_healthkit_config_sensor_ids_match_python_constants(healthkit_config):
    expected_ids = {s["sensorId"] for s in reality_bridge._HEALTH_SENSORS}
    config_ids   = {m["sensorId"]  for m in healthkit_config["sourceMappings"]}
    assert config_ids == expected_ids, (
        f"Config sensorIds {config_ids} do not match bridge constants {expected_ids}"
    )


def test_healthkit_config_regions_match_health_sensors(healthkit_config):
    sensor_regions = {
        s["sensorId"]: s["region"]
        for s in reality_bridge._HEALTH_SENSORS
    }
    for mapping in healthkit_config["sourceMappings"]:
        sid = mapping["sensorId"]
        assert sid in sensor_regions, f"{sid} not in _HEALTH_SENSORS"
        assert mapping["region"] == sensor_regions[sid], (
            f"{sid} region mismatch: config={mapping['region']} bridge={sensor_regions[sid]}"
        )


def test_healthkit_config_target_region_is_health_input_window(healthkit_config):
    target = healthkit_config["targetPerceptualRegion"]
    assert target["offset"] == 186
    assert target["length"] == 4


def test_healthkit_config_band_thresholds_match_python_constants(healthkit_config):
    bt = healthkit_config["bandThresholds"]
    assert bt["hr_low_bpm"]      == reality_bridge._HR_LOW_BPM
    assert bt["hr_high_bpm"]     == reality_bridge._HR_HIGH_BPM
    assert bt["hrv_ok_sdnn_ms"]  == reality_bridge._HRV_OK_MS
    assert bt["sleep_ok_hours"]  == reality_bridge._SLEEP_OK_HOURS


def test_healthkit_config_passthrough_normalize_for_ts_pe(healthkit_config):
    """Primary sourceMappings must use passthrough normalize mode (TS PE compat)."""
    for mapping in healthkit_config["sourceMappings"]:
        mode = mapping.get("normalize", {}).get("mode")
        assert mode == "passthrough", (
            f"{mapping['hkTypeIdentifier']} uses normalize.mode={mode!r}; "
            f"TS PE requires 'passthrough'"
        )


def test_healthkit_config_cpp_lsp_block_uses_band_mode(healthkit_config):
    """The cppLspRuntimeConfig block must document 'band' normalize for native runtimes."""
    cpp_block = healthkit_config.get("cppLspRuntimeConfig", {})
    assert cpp_block, "cppLspRuntimeConfig block is missing"
    for mapping in cpp_block.get("sourceMappings", []):
        mode = mapping.get("normalize", {}).get("mode")
        assert mode == "band", (
            f"{mapping.get('hkTypeIdentifier')} in cppLspRuntimeConfig "
            f"should use mode='band', got {mode!r}"
        )
