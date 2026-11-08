"""Lane TLS: localAI on the transport profile (localAIStack#172, RealityEngine_CI#668).

The runtime contract is RealityEngine_CI ``docs/TRANSPORT_SECURITY.md``:

- ``RE_TLS_CERT`` / ``RE_TLS_KEY``: this service's certificate chain and key,
  issued by the lane CA. Setting ``RE_TLS_CERT`` turns TLS on: the API serves
  TLS 1.3 with mTLS, and every link it opens (engines, the instance registry,
  Qdrant, Redis, Ollama's TLS front) is TLS 1.3 with this certificate presented.
- ``RE_TLS_CA``: the lane CA, the only trust anchor while the lane is on.
- ``RE_TLS_CRL``: the lane CA's revocation list, checked against every peer
  certificate when set.

With ``RE_TLS_CERT`` unset nothing changes: the old ``RE_SSL_VERIFY`` behaviour
stays for the plaintext lane until the lanes delete it. With it set,
``RE_SSL_VERIFY`` is ignored, because the profile forbids disabling
verification outside a named test fixture. A lane that is on but missing a file
raises ``LaneTlsError`` rather than falling back.

The profile (``re-transport-2026.11``, RealityEngine_CI
``config/transport-profile.json``): TLS 1.3 only; suites
TLS_AES_256_GCM_SHA384 and TLS_CHACHA20_POLY1305_SHA256; groups X25519MLKEM768
where the linked OpenSSL has it, falling back to X25519 or secp384r1.
"""

from __future__ import annotations

import os
import ssl
from functools import lru_cache

PROFILE_ID = "re-transport-2026.11"
CIPHER_SUITES = ("TLS_AES_256_GCM_SHA384", "TLS_CHACHA20_POLY1305_SHA256")
PREFERRED_GROUPS = ("X25519MLKEM768",)
FALLBACK_GROUPS = ("X25519", "secp384r1")

_LEGACY_VERIFY_OFF = ("false", "0", "no")


class LaneTlsError(RuntimeError):
    """RE_TLS_CERT is set but the lane's material cannot be used."""


def enabled() -> bool:
    """The lane is on for this process: RE_TLS_CERT is set (the contract's switch)."""
    return bool(os.environ.get("RE_TLS_CERT"))


def material() -> dict[str, str | None]:
    """RE_TLS_CERT/KEY/CA (required when on) and RE_TLS_CRL (optional)."""
    found = {n: os.environ.get(n, "") for n in ("RE_TLS_CERT", "RE_TLS_KEY", "RE_TLS_CA")}
    missing = [n for n, v in found.items() if not v or not os.path.isfile(v)]
    if missing:
        raise LaneTlsError(
            "RE_TLS_CERT is set but " + ", ".join(missing) + " is unset or not a file; "
            "a lane that is on never falls back to plaintext or unverified TLS"
        )
    crl = os.environ.get("RE_TLS_CRL") or None
    if crl and not os.path.isfile(crl):
        raise LaneTlsError(f"RE_TLS_CRL names {crl}, which is not a file")
    return {
        "cert": found["RE_TLS_CERT"],
        "key": found["RE_TLS_KEY"],
        "ca": found["RE_TLS_CA"],
        "crl": crl,
    }


def groups_for(openssl_version: tuple[int, ...]) -> tuple[str, ...]:
    """The profile's groups this OpenSSL can offer: the PQ hybrid needs 3.5+."""
    if tuple(openssl_version[:2]) >= (3, 5):
        return PREFERRED_GROUPS + FALLBACK_GROUPS
    return FALLBACK_GROUPS


def _apply_profile(ctx: ssl.SSLContext) -> ssl.SSLContext:
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    # Python 3.15 exposes TLS 1.3 suites and groups. Without them the context
    # would accept OpenSSL's defaults (AES-128 among them), which the profile
    # does not allow, so their absence is an error, not a silent fallback.
    if not (hasattr(ctx, "set_ciphersuites") and hasattr(ctx, "set_groups")):
        raise LaneTlsError(
            "this Python cannot set TLS 1.3 cipher suites and groups "
            "(SSLContext.set_ciphersuites / set_groups need Python 3.15+)"
        )
    # The ignores: the type stubs predate these 3.15 methods.
    ctx.set_ciphersuites(":".join(CIPHER_SUITES))  # pyright: ignore[reportAttributeAccessIssue]
    ctx.set_groups(":".join(groups_for(ssl.OPENSSL_VERSION_INFO)))  # pyright: ignore[reportAttributeAccessIssue]
    return ctx


def _load(ctx: ssl.SSLContext, m: dict[str, str | None]) -> ssl.SSLContext:
    ctx.load_cert_chain(m["cert"], m["key"])  # type: ignore[arg-type]
    ctx.load_verify_locations(cafile=m["ca"])
    if m["crl"]:
        ctx.load_verify_locations(cafile=m["crl"])
        ctx.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
    return ctx


@lru_cache(maxsize=1)
def client_context() -> ssl.SSLContext:
    """TLS 1.3 to a lane peer: lane CA only, hostname checked, our certificate presented."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # check_hostname and CERT_REQUIRED by default
    return _load(_apply_profile(ctx), material())


def server_context() -> ssl.SSLContext:
    """The API's listener: TLS 1.3, and a client certificate from the lane CA is required."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.verify_mode = ssl.CERT_REQUIRED
    return _load(_apply_profile(ctx), material())


def uvicorn_context_factory(config, default_factory) -> ssl.SSLContext:
    """uvicorn ``ssl_context_factory``; module level so ``--reload`` can pickle it.

    uvicorn's default factory is not used: it cannot set TLS 1.3 suites or groups.
    """
    del config, default_factory
    return server_context()


def _legacy_verify() -> bool:
    return os.getenv("RE_SSL_VERIFY", "true").lower() not in _LEGACY_VERIFY_OFF


def httpx_verify() -> ssl.SSLContext | bool:
    """``verify=`` for an httpx client to an engine or the instance registry."""
    return client_context() if enabled() else _legacy_verify()


def qdrant_kwargs() -> dict:
    """Extra QdrantClient arguments: https with the lane context when on.

    Qdrant itself cannot be held to the profile (it also accepts TLS 1.2 and
    AES-128 and offers no PQ group); this client never offers anything but the
    profile, which is the compensation recorded for that deficiency
    (RealityEngine_CI config/transport-inventory.json, localai-to-qdrant).

    qdrant-client's own version check dials with a bare ``httpx.get``, no client
    certificate and the system trust store, so against mTLS it always fails and
    warns. It is off on the lane; ``core.vector_store`` runs the same check over
    the lane's TLS instead.
    """
    if not enabled():
        return {}
    return {"https": True, "verify": client_context(), "check_compatibility": False}


def redis_url(url: str) -> str:
    """``rediss://`` for the configured Redis URL when the lane is on."""
    if enabled() and url.startswith("redis://"):
        return "rediss://" + url[len("redis://") :]
    return url


def redis_kwargs() -> dict:
    """redis-py SSL arguments (it takes files, not a context) when the lane is on."""
    if not enabled():
        return {}
    m = material()
    return {
        "ssl_certfile": m["cert"],
        "ssl_keyfile": m["key"],
        "ssl_ca_certs": m["ca"],
        "ssl_cert_reqs": "required",
        "ssl_check_hostname": True,
        "ssl_min_version": ssl.TLSVersion.TLSv1_3,
    }


def ollama_client_kwargs() -> dict:
    """``client_kwargs`` for langchain_ollama / ollama.Client (httpx underneath)."""
    return {"verify": client_context()} if enabled() else {}
