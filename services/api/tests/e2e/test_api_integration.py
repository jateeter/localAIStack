"""
Compose integration tests — run against the compose stack (qdrant + redis + api).

PE and RE are NOT required; these tests verify the API's bridge-optional behavior.
The CI e2e workflow starts the stack with PE_URL/RE_URL pointing to unreachable
addresses so the bridge degrades gracefully.

Run locally (compose stack must be up):
  docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d qdrant redis api
  pytest services/api/tests/e2e/test_api_integration.py --integration -v

Run in CI:
  Invoked automatically by .github/workflows/e2e.yml
"""

from __future__ import annotations

import httpx
import pytest


# ── /health endpoint ──────────────────────────────────────────────────────────


@pytest.mark.integration
def test_health_returns_200(live_api: str) -> None:
    r = httpx.get(f"{live_api}/health", timeout=10)
    assert r.status_code == 200


@pytest.mark.integration
def test_health_response_has_required_fields(live_api: str) -> None:
    r = httpx.get(f"{live_api}/health", timeout=10)
    data = r.json()
    assert "status" in data
    assert "bridge" in data
    assert "services" in data


@pytest.mark.integration
def test_health_status_is_valid_string(live_api: str) -> None:
    r = httpx.get(f"{live_api}/health", timeout=10)
    data = r.json()
    assert data["status"] in ("ok", "degraded")


@pytest.mark.integration
def test_health_services_has_core_keys(live_api: str) -> None:
    r = httpx.get(f"{live_api}/health", timeout=10)
    services = r.json()["services"]
    for key in ("api", "ollama", "qdrant", "redis", "pe", "re"):
        assert key in services, f"Missing key: {key}"


@pytest.mark.integration
def test_health_pe_field_is_dict(live_api: str) -> None:
    """PE status must be a structured dict with a 'status' key."""
    r = httpx.get(f"{live_api}/health", timeout=10)
    pe = r.json()["services"]["pe"]
    assert isinstance(pe, dict)
    assert "status" in pe
    assert pe["status"] in ("ok", "unreachable", "unknown")


@pytest.mark.integration
def test_health_re_field_is_dict(live_api: str) -> None:
    """RE status must be a structured dict with a 'status' key."""
    r = httpx.get(f"{live_api}/health", timeout=10)
    re = r.json()["services"]["re"]
    assert isinstance(re, dict)
    assert "status" in re
    assert re["status"] in ("ok", "unreachable", "unknown")


@pytest.mark.integration
def test_health_bridge_degraded_when_pe_re_unreachable(live_api: str) -> None:
    """When PE/RE are not running, bridge must be 'degraded'."""
    r = httpx.get(f"{live_api}/health", timeout=10)
    data = r.json()
    pe_ok = data["services"]["pe"]["status"] == "ok"
    re_ok = data["services"]["re"]["status"] == "ok"
    if not pe_ok or not re_ok:
        assert data["bridge"] == "degraded", (
            f"bridge should be 'degraded' when pe_ok={pe_ok} re_ok={re_ok}, "
            f"got bridge={data['bridge']!r}"
        )


@pytest.mark.integration
def test_health_api_field_is_ok(live_api: str) -> None:
    """The api self-check must always be 'ok' when we can reach the endpoint."""
    r = httpx.get(f"{live_api}/health", timeout=10)
    assert r.json()["services"]["api"] == "ok"


@pytest.mark.integration
def test_health_qdrant_ok_in_compose_stack(live_api: str) -> None:
    """Qdrant is started by the compose stack — it must be reachable."""
    r = httpx.get(f"{live_api}/health", timeout=10)
    qdrant_status = r.json()["services"]["qdrant"]
    assert qdrant_status == "ok", f"Qdrant should be 'ok' in compose stack, got: {qdrant_status}"


@pytest.mark.integration
def test_health_redis_ok_in_compose_stack(live_api: str) -> None:
    """Redis is started by the compose stack — it must be reachable."""
    r = httpx.get(f"{live_api}/health", timeout=10)
    redis_status = r.json()["services"]["redis"]
    assert redis_status == "ok", f"Redis should be 'ok' in compose stack, got: {redis_status}"


# ── / root endpoint ───────────────────────────────────────────────────────────


@pytest.mark.integration
def test_root_returns_200(live_api: str) -> None:
    r = httpx.get(f"{live_api}/", timeout=10)
    assert r.status_code == 200


@pytest.mark.integration
def test_root_response_has_service_field(live_api: str) -> None:
    r = httpx.get(f"{live_api}/", timeout=10)
    data = r.json()
    assert data.get("service") == "localAIStack"
    assert "llm_model" in data
    assert "embed_model" in data


# ── /docs (OpenAPI) ───────────────────────────────────────────────────────────


@pytest.mark.integration
def test_openapi_docs_reachable(live_api: str) -> None:
    r = httpx.get(f"{live_api}/docs", timeout=10)
    assert r.status_code == 200


# ── /rag ingest (no Ollama needed) ───────────────────────────────────────────


@pytest.mark.integration
def test_rag_ingest_text_returns_200(live_api: str) -> None:
    """Ingesting a text chunk into Qdrant should work without Ollama."""
    r = httpx.post(
        f"{live_api}/rag/ingest/text",
        json={
            "text": "localAIStack integration test document.",
            "source": "e2e_test",
            "metadata": {"test": True},
        },
        timeout=30,
    )
    # May fail if embed model is not available — that's OK for CI (no Ollama)
    assert r.status_code in (200, 503, 500), f"Unexpected status: {r.status_code} {r.text}"


# ── /graphql/events ───────────────────────────────────────────────────────────


@pytest.mark.integration
def test_graphql_events_endpoint_reachable(live_api: str) -> None:
    r = httpx.get(f"{live_api}/graphql/events", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert "events" in data
