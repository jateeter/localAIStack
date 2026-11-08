"""Lane TLS (core/lane_tls.py, scripts/ollama_tls_front.py; localAIStack#172).

Real handshakes against a throwaway CA made with the openssl CLI: the API's
listener and its clients hold to the transport profile (TLS 1.3 only, the
profile's suites, a client certificate required), the lane-off path is what it
was, and Ollama's TLS front relays to a loopback upstream only.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import socket
import ssl
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from core import lane_tls

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl CLI")

_FRONT = Path(__file__).resolve().parents[3] / "scripts" / "ollama_tls_front.py"


def _openssl(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def pki(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("pki")
    _openssl("ecparam", "-name", "secp384r1", "-genkey", "-noout", "-out", "ca.key", cwd=d)
    _openssl(
        "req", "-x509", "-new", "-key", "ca.key", "-sha384", "-days", "2", "-subj", "/CN=test lane CA",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-out", "ca.crt", cwd=d,
    )  # fmt: skip
    for name in ("service", "peer"):
        _openssl("ecparam", "-name", "secp384r1", "-genkey", "-noout", "-out", f"{name}.key", cwd=d)
        _openssl(
            "req",
            "-new",
            "-key",
            f"{name}.key",
            "-subj",
            f"/CN={name}",
            "-out",
            f"{name}.csr",
            cwd=d,
        )
        (d / f"{name}.ext").write_text(
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature\n"
            "extendedKeyUsage=serverAuth,clientAuth\n"
            "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
        )
        _openssl(
            "x509", "-req", "-in", f"{name}.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
            "-CAcreateserial", "-sha384", "-days", "2", "-extfile", f"{name}.ext", "-out", f"{name}.crt",
            cwd=d,
        )  # fmt: skip
    # An outsider: a certificate from a CA the lane does not trust.
    _openssl("ecparam", "-name", "secp384r1", "-genkey", "-noout", "-out", "other.key", cwd=d)
    _openssl(
        "req", "-x509", "-new", "-key", "other.key", "-sha384", "-days", "2", "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1", "-out", "other.crt", cwd=d,
    )  # fmt: skip
    return {k: d / v for k, v in {
        "ca": "ca.crt", "cert": "service.crt", "key": "service.key",
        "peer_cert": "peer.crt", "peer_key": "peer.key", "other_cert": "other.crt", "other_key": "other.key",
    }.items()}  # fmt: skip


@pytest.fixture
def lane_on(pki, monkeypatch):
    monkeypatch.setenv("RE_TLS_CERT", str(pki["cert"]))
    monkeypatch.setenv("RE_TLS_KEY", str(pki["key"]))
    monkeypatch.setenv("RE_TLS_CA", str(pki["ca"]))
    monkeypatch.delenv("RE_TLS_CRL", raising=False)
    lane_tls.client_context.cache_clear()
    yield pki
    lane_tls.client_context.cache_clear()


@pytest.fixture
def lane_off(monkeypatch):
    for n in ("RE_TLS_CERT", "RE_TLS_KEY", "RE_TLS_CA", "RE_TLS_CRL"):
        monkeypatch.delenv(n, raising=False)
    lane_tls.client_context.cache_clear()
    yield
    lane_tls.client_context.cache_clear()


def _tls_server(ctx: ssl.SSLContext) -> tuple[int, list, threading.Thread]:
    """One-connection TLS server on 127.0.0.1; returns (port, outcomes, thread)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(4)
    outcomes: list = []

    def run():
        conn, _ = sock.accept()
        try:
            with ctx.wrap_socket(conn, server_side=True) as s:
                s.recv(1)  # TLS 1.3 sends the client's certificate after the handshake
                s.sendall(b"ok")
                outcomes.append(("ok", s.version(), (s.cipher() or ("",))[0]))
        except (ssl.SSLError, OSError) as exc:
            outcomes.append(("refused", type(exc).__name__))
        sock.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return sock.getsockname()[1], outcomes, t


def _client(port: int, ctx: ssl.SSLContext) -> bytes:
    with (
        socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
        ctx.wrap_socket(raw, server_hostname="localhost") as s,
    ):
        s.sendall(b"x")
        return s.recv(2)


def test_lane_off_keeps_the_legacy_switch(lane_off, monkeypatch):
    assert not lane_tls.enabled()
    monkeypatch.setenv("RE_SSL_VERIFY", "false")
    assert lane_tls.httpx_verify() is False
    assert lane_tls.urllib_context().verify_mode == ssl.CERT_NONE
    monkeypatch.setenv("RE_SSL_VERIFY", "true")
    assert lane_tls.httpx_verify() is True
    assert lane_tls.qdrant_kwargs() == {}
    assert lane_tls.redis_kwargs() == {}
    assert lane_tls.ollama_client_kwargs() == {}
    assert lane_tls.redis_url("redis://redis:6379") == "redis://redis:6379"


def test_lane_on_ignores_re_ssl_verify(lane_on, monkeypatch):
    monkeypatch.setenv("RE_SSL_VERIFY", "false")
    ctx = lane_tls.httpx_verify()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.minimum_version == ctx.maximum_version == ssl.TLSVersion.TLSv1_3
    assert lane_tls.urllib_context() is ctx
    assert lane_tls.qdrant_kwargs() == {"https": True, "verify": ctx, "check_compatibility": False}
    assert lane_tls.ollama_client_kwargs() == {"verify": ctx}
    assert lane_tls.redis_url("redis://redis:6379") == "rediss://redis:6379"
    assert lane_tls.redis_kwargs()["ssl_min_version"] == ssl.TLSVersion.TLSv1_3


def test_lane_on_with_a_missing_file_raises(lane_on, monkeypatch, tmp_path):
    monkeypatch.setenv("RE_TLS_CA", str(tmp_path / "absent.crt"))
    with pytest.raises(lane_tls.LaneTlsError, match="RE_TLS_CA"):
        lane_tls.client_context()


def test_groups_follow_the_openssl_version():
    assert lane_tls.groups_for((3, 5, 0)) == ("X25519MLKEM768", "X25519", "secp384r1")
    assert lane_tls.groups_for((3, 0, 13)) == ("X25519", "secp384r1")


def test_mtls_round_trip_meets_the_profile(lane_on):
    port, outcomes, t = _tls_server(lane_tls.server_context())
    assert _client(port, lane_tls.client_context()) == b"ok"
    t.join(5)
    status, version, cipher = outcomes[0]
    assert (status, version) == ("ok", "TLSv1.3")
    assert cipher in lane_tls.CIPHER_SUITES


def test_listener_refuses_a_client_without_a_certificate(lane_on):
    port, outcomes, t = _tls_server(lane_tls.server_context())
    anon = ssl.create_default_context(cafile=str(lane_on["ca"]))
    with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
        _client(port, anon)
    t.join(5)
    assert outcomes and outcomes[0][0] == "refused"


def test_listener_refuses_tls_1_2(lane_on):
    port, _, _ = _tls_server(lane_tls.server_context())
    old = ssl.create_default_context(cafile=str(lane_on["ca"]))
    old.maximum_version = ssl.TLSVersion.TLSv1_2
    old.load_cert_chain(str(lane_on["peer_cert"]), str(lane_on["peer_key"]))
    with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
        _client(port, old)


def test_client_refuses_a_server_outside_the_lane(lane_on):
    srv = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv.load_cert_chain(str(lane_on["other_cert"]), str(lane_on["other_key"]))
    port, _, _ = _tls_server(srv)
    with pytest.raises(ssl.SSLCertVerificationError):
        _client(port, lane_tls.client_context())


# ── Ollama's TLS front ─────────────────────────────────────────────────────────


def _load_front():
    spec = importlib.util.spec_from_file_location("ollama_tls_front", _FRONT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ollama_tls_front"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_front_refuses_an_upstream_off_loopback():
    front = _load_front()
    assert front.loopback_upstream("127.0.0.1:11434") == ("127.0.0.1", 11434)
    assert front.loopback_upstream("localhost:11434") == ("127.0.0.1", 11434)
    assert front.loopback_upstream("[::1]:11434") == ("::1", 11434)
    with pytest.raises(ValueError, match="not loopback"):
        front.loopback_upstream("192.168.1.10:11434")
    with pytest.raises(ValueError):
        front.loopback_upstream("11434")


def test_front_relays_over_mtls_to_a_plain_upstream(lane_on):
    front = _load_front()

    async def scenario() -> tuple[bytes, bool]:
        async def upstream(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            req = await r.readuntil(b"\r\n\r\n")
            body = b"streamed:" + req.split(b" ")[1]
            w.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body)
                + body
            )
            await w.drain()
            w.close()

        up = await asyncio.start_server(upstream, "127.0.0.1", 0)
        up_port = up.sockets[0].getsockname()[1]
        srv = await asyncio.start_server(
            lambda r, w: front._relay(r, w, ("127.0.0.1", up_port)),
            "127.0.0.1",
            0,
            ssl=lane_tls.server_context(),
        )
        port = srv.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection(
            "127.0.0.1", port, ssl=lane_tls.client_context(), server_hostname="localhost"
        )
        w.write(b"GET /api/tags HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await w.drain()
        reply = await asyncio.wait_for(r.read(), 5)
        w.close()

        anon = ssl.create_default_context(cafile=str(lane_on["ca"]))
        refused = False
        try:
            r2, w2 = await asyncio.open_connection(
                "127.0.0.1", port, ssl=anon, server_hostname="localhost"
            )
            w2.write(b"GET / HTTP/1.1\r\n\r\n")
            await w2.drain()
            refused = await asyncio.wait_for(r2.read(), 5) == b""
        except ssl.SSLError, ConnectionError, OSError:
            refused = True
        srv.close()
        up.close()
        return reply, refused

    reply, refused = asyncio.run(scenario())
    assert reply.startswith(b"HTTP/1.1 200 OK") and reply.endswith(b"streamed:/api/tags")
    assert refused, "the front answered a client without a certificate"
