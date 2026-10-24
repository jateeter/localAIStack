#!/usr/bin/env python3
"""Re-embed localAI's Qdrant collections after the embedding model changes.

The implementation is ``services/api/reembed_cli.py`` (it also ships in the API
image, where ``realityengine reembed`` runs it). This wrapper only makes it
runnable from a checkout, with Qdrant on the host port the compose file publishes
(4333) unless QDRANT_PORT or --qdrant-port says otherwise. ``--help`` has the usage.
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services" / "api"))

if "--qdrant-port" not in sys.argv and "QDRANT_PORT" not in os.environ:
    sys.argv += ["--qdrant-port", "4333"]

from reembed_cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
