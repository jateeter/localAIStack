"""Run the API: plain HTTP, or TLS 1.3 with mTLS when RE_TLS_CERT is set.

    python serve.py [--host 0.0.0.0] [--port 8000] [--reload]

uvicorn's own TLS options cannot hold the listener to the transport profile (no
TLS 1.3 suites or groups, no CRL), so with the lane on the context comes from
core/lane_tls.py through uvicorn's ssl_context_factory (localAIStack#172,
RealityEngine_CI#668). With RE_TLS_CERT unset this is ``uvicorn main:app``.
"""

from __future__ import annotations

import argparse

import uvicorn

from core import lane_tls


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="development: reload on source change")
    a = p.parse_args(argv)
    kwargs: dict = {}
    if lane_tls.enabled():
        lane_tls.server_context()  # fail at start, not on the first connection
        kwargs["ssl_context_factory"] = lane_tls.uvicorn_context_factory
    uvicorn.run("main:app", host=a.host, port=a.port, reload=a.reload, **kwargs)


if __name__ == "__main__":
    main()
