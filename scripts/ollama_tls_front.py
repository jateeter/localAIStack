#!/usr/bin/env python3
"""Ollama's TLS front: TLS 1.3 with mTLS for every Ollama client (localAIStack#172).

Ollama serves no TLS (0.40.13; its FAQ offers only a reverse proxy). The owner
took option (a) on 2026-11-07 (RealityEngine_CI#668): clients — the engines,
localAI and the OpenClaw gateway — reach this front on TLS 1.3 with mTLS, held to
the transport profile in full, and the front reaches Ollama on the host's
loopback. That last hop is the accepted deficiency ``ollama-front-to-ollama`` in
RealityEngine_CI ``config/transport-inventory.json``; its compensation is that
Ollama binds 127.0.0.1 only, so the front refuses any upstream that is not a
loopback address.

It relays bytes, not HTTP: streamed generations pass through as they are
produced, and nothing about Ollama's API is interpreted here.

    ollama_tls_front.py [--bind 127.0.0.1] [--port 11443] [--upstream 127.0.0.1:11434]
                        [--pid-file PATH]

Certificates come from the runtime contract (RE_TLS_CERT, RE_TLS_KEY, RE_TLS_CA,
RE_TLS_CRL; RealityEngine_CI docs/TRANSPORT_SECURITY.md), all required: the
front has no plaintext mode. The context is services/api/core/lane_tls.py's, so
localAI and its front hold one definition of the profile.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

from core import lane_tls  # noqa: E402 — the path above makes it importable

_CHUNK = 64 * 1024


def loopback_upstream(spec: str) -> tuple[str, int]:
    """``host:port`` for Ollama, refused unless host is a loopback address."""
    host, sep, port = spec.rpartition(":")
    if not sep or not port.isdigit():
        raise ValueError(f"upstream {spec!r} is not host:port")
    host = host.strip("[]")
    if host == "localhost":
        host = "127.0.0.1"
    if not ipaddress.ip_address(host).is_loopback:
        raise ValueError(f"upstream {host} is not loopback; Ollama must stay on 127.0.0.1")
    return host, int(port)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(_CHUNK):
            writer.write(data)
            await writer.drain()
    except ConnectionError, asyncio.IncompleteReadError, OSError:
        pass
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()
            else:
                writer.close()
        except OSError, RuntimeError:
            pass


async def _relay(
    client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter, upstream: tuple[str, int]
) -> None:
    try:
        up_r, up_w = await asyncio.open_connection(*upstream)
    except OSError:
        client_w.close()
        return
    try:
        await asyncio.gather(_pipe(client_r, up_w), _pipe(up_r, client_w))
    finally:
        for w in (up_w, client_w):
            w.close()


def _quiet_refusals(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    # A refused handshake (no client certificate, TLS 1.2, plaintext) is the
    # profile doing its job, not a fault worth a traceback.
    exc = context.get("exception")
    if isinstance(exc, (ConnectionError, OSError)) or "SSL" in str(context.get("message", "")):
        return
    loop.default_exception_handler(context)


async def serve(bind: str, port: int, upstream: tuple[str, int]) -> None:
    ctx = lane_tls.server_context()
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_quiet_refusals)
    server = await asyncio.start_server(
        lambda r, w: _relay(r, w, upstream), bind, port, ssl=ctx, ssl_handshake_timeout=10
    )
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    print(
        f"ollama-tls-front: https://{bind}:{port} -> http://{upstream[0]}:{upstream[1]} "
        f"(TLS 1.3, mTLS, {lane_tls.PROFILE_ID})",
        flush=True,
    )
    async with server:
        await stop.wait()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=11443)
    p.add_argument("--upstream", default="127.0.0.1:11434")
    p.add_argument("--pid-file")
    a = p.parse_args(argv)
    if not lane_tls.enabled():
        print(
            "ollama-tls-front: RE_TLS_CERT is unset; the front has no plaintext mode",
            file=sys.stderr,
        )
        return 2
    try:
        upstream = loopback_upstream(a.upstream)
        lane_tls.server_context()
    except (ValueError, lane_tls.LaneTlsError, OSError) as exc:
        print(f"ollama-tls-front: {exc}", file=sys.stderr)
        return 2
    if a.pid_file:
        Path(a.pid_file).write_text(f"{os.getpid()}\n")
    try:
        asyncio.run(serve(a.bind, a.port, upstream))
    finally:
        if a.pid_file:
            Path(a.pid_file).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
