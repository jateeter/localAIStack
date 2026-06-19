"""
E2E and integration test fixtures for localAIStack.

Two test tiers controlled by CLI flags:

  pytest --integration   Run compose integration tests (starts/assumes compose stack;
                         no PE/RE required). Default URLs: http://localhost:4000.
                         Used by CI (.github/workflows/e2e.yml).

  pytest --live          Run full live-stack tests (requires PE + RE + localAI API
                         all running). Skips any service that is not reachable.

  pytest (no flags)      Unit tests only — no network, no Docker.

Environment variable overrides (all optional):
  LOCALAI_API_URL    default: http://localhost:4000
  PE_URL             default: http://localhost:3004
  RE_URL             default: http://localhost:3000
  E2E_TIMEOUT        seconds to wait for service readiness  default: 60
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

# ── CLI options ───────────────────────────────────────────────────────────────


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run compose integration tests (API must be reachable; no PE/RE required)",
    )
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run full live-stack tests (PE + RE + localAI API all required)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: compose stack integration tests — pass --integration to run",
    )
    config.addinivalue_line(
        "markers",
        "live: full live-stack tests (PE+RE+localAI) — pass --live to run",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    run_integration = config.getoption("--integration")
    run_live        = config.getoption("--live")

    skip_integration = pytest.mark.skip(reason="pass --integration to run")
    skip_live        = pytest.mark.skip(reason="pass --live to run")

    for item in items:
        if "integration" in item.keywords and not run_integration:
            item.add_marker(skip_integration)
        if "live" in item.keywords and not run_live:
            item.add_marker(skip_live)


# ── URL fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def api_url() -> str:
    return os.getenv("LOCALAI_API_URL", "http://localhost:4000")


@pytest.fixture(scope="session")
def pe_url() -> str:
    return os.getenv("PE_URL", "http://localhost:3004")


@pytest.fixture(scope="session")
def re_url() -> str:
    return os.getenv("RE_URL", "http://localhost:3000")


@pytest.fixture(scope="session")
def e2e_timeout() -> int:
    return int(os.getenv("E2E_TIMEOUT", "60"))


# ── Readiness helpers ─────────────────────────────────────────────────────────


def _wait_for_http(url: str, timeout: int, label: str) -> bool:
    """Poll url with GET until 200 or timeout. Returns True if reachable."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=3.0, follow_redirects=True)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


@pytest.fixture(scope="session")
def live_api(api_url: str, e2e_timeout: int) -> str:
    """Skip the test if the localAI API is not reachable."""
    if not _wait_for_http(f"{api_url}/health", e2e_timeout, "localAI API"):
        pytest.skip(f"localAI API not reachable at {api_url} (set LOCALAI_API_URL or start the stack)")
    return api_url


@pytest.fixture(scope="session")
def live_pe(pe_url: str, e2e_timeout: int) -> str:
    """Skip the test if the Perception Engine is not reachable."""
    if not _wait_for_http(f"{pe_url}/api/sources", e2e_timeout, "PE"):
        pytest.skip(f"PE not reachable at {pe_url} (set PE_URL or start RealityEngine_AI PE)")
    return pe_url


@pytest.fixture(scope="session")
def live_re(re_url: str, e2e_timeout: int) -> str:
    """Skip the test if the Reality Engine is not reachable."""
    if not _wait_for_http(f"{re_url}/api/machines", e2e_timeout, "RE"):
        pytest.skip(f"RE not reachable at {re_url} (set RE_URL or start RealityEngine_AI RE)")
    return re_url


# ── Polling helpers ───────────────────────────────────────────────────────────


def poll_until(
    condition_fn,
    timeout: int = 30,
    interval: float = 2.0,
    label: str = "condition",
) -> bool:
    """Poll condition_fn() every interval seconds until True or timeout. Returns last result."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition_fn():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False
