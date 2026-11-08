"""Container healthcheck: GET /health on this API, over the lane's mTLS when on.

Exits 0 when the API answers 2xx. With RE_TLS_CERT set it connects to
https://localhost:<port> and presents the service's own certificate, which the
lane CA issued for localhost; otherwise it is the plain http check it replaces.
"""

from __future__ import annotations

import sys
import urllib.request

from core import lane_tls


def main(port: int = 8000) -> int:
    # The lane's scheme, as serve.py chose it; localhost is in the lane certificate.
    on = lane_tls.enabled()
    scheme = "https" if on else "http"
    url = f"{scheme}://localhost:{port}/health"
    ctx = lane_tls.client_context() if on else None
    try:
        with urllib.request.urlopen(url, timeout=5, context=ctx) as r:
            return 0 if 200 <= r.status < 300 else 1
    except Exception as exc:  # noqa: BLE001 — any failure is unhealthy
        print(f"healthcheck: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8000))
