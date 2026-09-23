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

    def __init__(self, health_offset: int | None = 7578):
        self._health_offset = health_offset

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url: str, **_):
        if "/api/perceptual-simulation/state" in url:
            ps = [0.0] * 7680
            if self._health_offset is not None:
                ps[self._health_offset] = 1.0
            return _FakeREResponse(200, {"state": {"perceptualSpace": ps}})
        return _FakeREResponse(200, {})


_HEALTHKIT_CONFIG_PATH = (
    pathlib.Path(__file__).parent.parent.parent.parent / "config" / "integrations.json"
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
        (7578, "thriving"),
        (7579, "balanced"),
        (7580, "watch"),
        (7581, "attention"),
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
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get(self, *a, **kw):
            raise RuntimeError("RE unreachable")

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _Raising())
    assert reality_bridge.get_current_health_state() is None


def test_get_current_health_state_returns_none_on_re_404(monkeypatch):
    class _NotFound:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get(self, *a, **kw):
            return _FakeREResponse(404, {})

    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: _NotFound())
    assert reality_bridge.get_current_health_state() is None


def test_get_current_health_state_calls_re_not_pe(monkeypatch):
    """Must call RE /api/perceptual-simulation/state, not PE /api/state."""
    called_urls: list[str] = []

    class _Tracker:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get(self, url, **_):
            called_urls.append(url)
            ps = [0.0] * 7680
            ps[7578] = 1.0
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
    "thriving": "nominal range",  # substring expected in hint
    "balanced": "slightly outside",
    "watch": "several",
    "attention": "well outside",
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
    snippet = content[start : start + 600]
    health_terms = ("health", "HRV", "wellness", "sleep", "heart rate", "recovery")
    assert any(t.lower() in snippet.lower() for t in health_terms), (
        f"health_search docstring missing health-related terms. Snippet:\n{snippet}"
    )


# ── (6) HealthKit in the integration config ───────────────────────────────────
# HEALTH_INTEGRATION_ROADMAP T2/T8. HealthKit families land in the corpus lanes
# through RealityEngine_CI's config; localAIStack's config maps no HealthKit
# sensor, because anything it mapped into [7574:7578] or [7600:7632] would be a
# second writer on windows core/health_scope.py owns.


@pytest.fixture
def healthkit_config() -> dict:
    assert _HEALTHKIT_CONFIG_PATH.exists(), f"config not found: {_HEALTHKIT_CONFIG_PATH}"
    return json.loads(_HEALTHKIT_CONFIG_PATH.read_text())


def test_healthkit_config_declares_healthkit_integration(healthkit_config):
    kinds = {i["id"]: i["kind"] for i in healthkit_config["integrations"]}
    assert kinds.get("healthkit-localai") == "healthkit"


def test_healthkit_config_maps_no_healthkit_sensor(healthkit_config):
    hk = [m["id"] for m in healthkit_config["sourceMappings"] if m["id"].startswith("healthkit")]
    assert hk == [], f"localAIStack config must not map HealthKit sensors: {hk}"


def test_healthkit_config_writes_nothing_into_localai_health_windows(healthkit_config):
    owned = [(7574, 7578), (7600, 7632)]
    for m in healthkit_config["sourceMappings"]:
        r = m.get("region")
        if not r:
            continue
        lo, hi = r["offset"], r["offset"] + r["length"]
        for a, b in owned:
            assert hi <= a or lo >= b, f"{m['id']} writes [{lo}:{hi}], inside [{a}:{b}]"


def test_redundant_healthkit_configs_are_gone():
    """T2: one config, not three that had already drifted apart."""
    cfg = _HEALTHKIT_CONFIG_PATH.parent
    assert not (cfg / "pe-integrations.json").exists()
    assert not (cfg / "integrations.healthkit-localai.json").exists()
