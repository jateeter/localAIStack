import asyncio
import os

import httpx
from fastapi import APIRouter
from qdrant_client import QdrantClient
import redis as redis_lib

from config import get_settings

router = APIRouter()


async def _check_pe(pe_url: str, ssl_verify: bool | str) -> dict:
    """Probe the Perception Engine: source list + health sensor count."""
    try:
        async with httpx.AsyncClient(timeout=3.0, verify=ssl_verify) as c:
            r = await c.get(f"{pe_url}/api/sources")
            r.raise_for_status()
            sources = r.json().get("sources", [])
            health_sensors = [
                s for s in sources
                if s.get("sensorId", "").startswith("localai_health_")
            ]
            return {
                "status": "ok",
                "sensor_count": len(sources),
                "health_sensors": len(health_sensors),
            }
    except Exception as exc:
        return {"status": "unreachable", "error": str(exc)[:200]}


async def _check_re(re_url: str, ssl_verify: bool | str) -> dict:
    """Probe the Reality Engine: perceptual space + machine count + current health state."""
    try:
        async with httpx.AsyncClient(timeout=3.0, verify=ssl_verify) as c:
            # Fan out: state and machine list in parallel
            state_coro    = c.get(f"{re_url}/api/perceptual-simulation/state")
            machines_coro = c.get(f"{re_url}/api/machines")
            state_r, machines_r = await asyncio.gather(state_coro, machines_coro)
            state_r.raise_for_status()
            machines_r.raise_for_status()

        data = state_r.json()
        ps   = data.get("state", {}).get("perceptualSpace", [])

        from core.reality_bridge import get_health_state
        health_state = get_health_state(ps)

        machine_count = len(machines_r.json().get("machines", []))
        return {
            "status":        "ok",
            "health_state":  health_state,
            "machine_count": machine_count,
            "ps_length":     len(ps),
        }
    except Exception as exc:
        return {"status": "unreachable", "error": str(exc)[:200]}


@router.get("/health")
async def health():
    s = get_settings()
    # RE_SSL_VERIFY mirrors the env var used by reality_bridge.py
    ssl_verify: bool | str = os.getenv("RE_SSL_VERIFY", "true").lower() not in (
        "false", "0", "no"
    )

    services: dict = {
        "api":    "ok",
        "ollama": "unknown",
        "qdrant": "unknown",
        "redis":  "unknown",
        "pe":     {"status": "unknown"},
        "re":     {"status": "unknown"},
    }

    # ── Core service probes (run in parallel) ──────────────────────────────────

    async def _check_ollama() -> None:
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{s.ollama_base_url}/api/tags")
                models = [m["name"] for m in r.json().get("models", [])]
            services["ollama"] = "ok"
            services["ollama_models"] = models
        except Exception as exc:
            services["ollama"] = f"error: {exc}"

    def _check_qdrant() -> None:
        try:
            qc = QdrantClient(host=s.qdrant_host, port=s.qdrant_port, timeout=3)
            collections = [c.name for c in qc.get_collections().collections]
            services["qdrant"] = "ok"
            services["qdrant_collections"] = collections
        except Exception as exc:
            services["qdrant"] = f"error: {exc}"

    def _check_redis() -> None:
        try:
            rc = redis_lib.from_url(s.redis_url, socket_timeout=3)
            rc.ping()
            services["redis"] = "ok"
        except Exception as exc:
            services["redis"] = f"error: {exc}"

    # Run sync probes in a thread pool so they don't block the event loop
    loop = asyncio.get_event_loop()
    await asyncio.gather(
        _check_ollama(),
        loop.run_in_executor(None, _check_qdrant),
        loop.run_in_executor(None, _check_redis),
        _attach_pe_re(services, s.pe_url, s.re_url, ssl_verify),
    )

    # ── Status rollup ──────────────────────────────────────────────────────────
    # Overall "status" reflects only core services — the AI pipeline must work.
    # PE/RE are supplementary; their health is surfaced via "bridge" separately.
    core_ok = all(
        services[k] == "ok"
        for k in ("ollama", "qdrant", "redis")
    )
    bridge_ok = all(
        services[k].get("status") == "ok"
        for k in ("pe", "re")
    )

    return {
        "status":  "ok" if core_ok else "degraded",
        "bridge":  "ok" if bridge_ok else "degraded",
        "services": services,
    }


async def _attach_pe_re(
    services: dict,
    pe_url: str,
    re_url: str,
    ssl_verify: bool | str,
) -> None:
    pe_result, re_result = await asyncio.gather(
        _check_pe(pe_url, ssl_verify),
        _check_re(re_url, ssl_verify),
    )
    services["pe"] = pe_result
    services["re"] = re_result
