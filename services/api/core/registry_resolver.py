"""Registry-aware RE/PE bridge target resolution.

In native multi-engine mode the universe publishes an instance registry
(``RE_REGISTRY_URL``; from Docker the host default is
``http://host.docker.internal:5999/re-registry.json``). The static
``PE_URL``/``RE_URL`` compose defaults point at Docker single-engine ports
that do not exist in native mode, silently degrading the bridge
(RealityEngine_CI#44).

Resolution order:

  1. The configured env/settings targets, if both RE and PE answer
     ``/api/health`` — an explicit, working configuration always wins.
  2. The first registry instance whose RE and PE answer ``/api/health``.
  3. The first ``running`` registry instance (bridge will surface degraded
     probes against a live target rather than a dead default).
  4. The env/settings targets unchanged (registry absent — Docker
     single-engine mode).

Results are cached for a short TTL so the per-request bridge hot path in
``reality_bridge`` does not re-probe on every call.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request

from config import get_settings

_CACHE_TTL_S = 30.0
_cache: dict = {"at": 0.0, "targets": None}

_PROBE_TIMEOUT_S = 2.0

# Self-signed dev certs are the norm for the RE tls-proxy; mirror the
# RE_SSL_VERIFY contract used by reality_bridge.
_SSL_VERIFY = os.getenv("RE_SSL_VERIFY", "true").lower() not in ("false", "0", "no")


def _ssl_context() -> ssl.SSLContext:
    if _SSL_VERIFY:
        return ssl.create_default_context()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _probe_health(base_url: str) -> bool:
    """True when GET {base_url}/api/health answers with HTTP 2xx."""
    try:
        req = urllib.request.Request(f"{base_url}/api/health")
        with urllib.request.urlopen(
            req, timeout=_PROBE_TIMEOUT_S, context=_ssl_context()
        ) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _fetch_registry(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(
            url, timeout=_PROBE_TIMEOUT_S, context=_ssl_context()
        ) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _running_instances(registry: dict) -> list[dict]:
    return [
        inst
        for inst in registry.get("instances", [])
        if inst.get("status", "running") == "running"
        and inst.get("re_url")
        and inst.get("pe_url")
    ]


def resolve_bridge_targets(force_refresh: bool = False) -> dict:
    """Return the active bridge targets.

    Shape: ``{"re_url", "pe_url", "source", "instance"}`` where ``source``
    is ``"env"`` (configured targets, live or registry-less fallback) or
    ``"registry"`` (re-targeted to a registry instance), and ``instance``
    is the registry instance id when source is ``"registry"``.
    """
    now = time.monotonic()
    if (
        not force_refresh
        and _cache["targets"] is not None
        and now - _cache["at"] < _CACHE_TTL_S
    ):
        return _cache["targets"]

    s = get_settings()
    env_targets = {
        "re_url": s.re_url,
        "pe_url": s.pe_url,
        "source": "env",
        "instance": None,
    }
    targets = env_targets

    env_alive = _probe_health(s.re_url) and _probe_health(s.pe_url)
    if not env_alive:
        registry_url = os.getenv("RE_REGISTRY_URL", "")
        registry = _fetch_registry(registry_url) if registry_url else None
        if registry:
            running = _running_instances(registry)
            chosen = next(
                (
                    inst
                    for inst in running
                    if _probe_health(inst["re_url"]) and _probe_health(inst["pe_url"])
                ),
                running[0] if running else None,
            )
            if chosen is not None:
                targets = {
                    "re_url": chosen["re_url"],
                    "pe_url": chosen["pe_url"],
                    "source": "registry",
                    "instance": chosen.get("id"),
                }

    _cache["at"] = now
    _cache["targets"] = targets
    return targets
