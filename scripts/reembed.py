#!/usr/bin/env python3
"""Re-embed localAI's Qdrant collections after the embedding model changes.

Vectors from two embedding models do not mix, so changing the embedder (for
example `make model-use ID=embeddinggemma-2-270m`) means rebuilding every
collection. This does it without losing data: it builds `<logical>__<model>`
beside the live collection from the source documents, checks retrieval against
the old one, switches the alias atomically, and keeps the old collection until
asked to drop it. See services/api/core/reembed.py and core/collections.py.

  scripts/reembed.py status                 every collection, the model that filled it, and whether it matches
  scripts/reembed.py run [LOGICAL ...]      migrate to the configured embedder (default: all rebuildable)
  scripts/reembed.py rollback LOGICAL       switch the alias back to the previous collection
  scripts/reembed.py drop-old LOGICAL       delete the collections the alias no longer points at

Options:
  --qdrant-host/--qdrant-port   default localhost:4333
  --embed-model TAG             default: .env / config.py (the registry default)
  --allow-data-loss             replace a populated collection that has no source documents
  --old-model TAG               the model that filled a legacy collection, to compare retrieval with
  --docs-dir DIR                health documents (default data/health/documents)

Exit status is non-zero when any collection was refused, failed verification, or
is a mismatch after the run.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "services" / "api"))

try:
    from langchain_ollama import OllamaEmbeddings
    from qdrant_client import QdrantClient

    from config import get_settings
    from core import collections as qc
    from core import reembed as rb
    from core.model_registry import load_registry
except ImportError as exc:  # pragma: no cover - environment, not logic
    sys.exit(
        f"[error] Missing dependency: {exc}\n  Run: cd services/api && pip install -r requirements.txt"
    )


def _health_chunks(docs_dir: pathlib.Path):
    """The health corpus, chunked exactly as scripts/ingest_health_docs.py chunks it."""
    spec = importlib.util.spec_from_file_location(
        "ingest_health_docs", _REPO / "scripts" / "ingest_health_docs.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.load_health_documents(docs_dir)


def _embedder(tag: str):
    return OllamaEmbeddings(
        model=tag, base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    )


def _old_embedder(slug: str | None):
    """The embedder that filled a collection, when its slug maps to a registered model."""
    if not slug:
        return None
    for m in load_registry().models:
        if m.role == "embedding" and qc.model_slug(m.tag) == slug:
            return _embedder(m.tag)
    return None


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("command", choices=["status", "run", "rollback", "drop-old"])
    p.add_argument("logicals", nargs="*")
    p.add_argument("--qdrant-host", default=os.getenv("QDRANT_HOST", "localhost"))
    p.add_argument("--qdrant-port", type=int, default=int(os.getenv("QDRANT_PORT", "4333")))
    p.add_argument("--embed-model", default=None)
    p.add_argument("--allow-data-loss", action="store_true")
    p.add_argument(
        "--old-model",
        default=None,
        help="the model that filled a legacy collection (it never recorded one), so retrieval can be compared",
    )
    p.add_argument("--docs-dir", default=str(_REPO / "data" / "health" / "documents"))
    p.add_argument("--json", action="store_true", help="machine-readable output")
    a = p.parse_args()

    s = get_settings()
    model = a.embed_model or s.embed_model
    client = QdrantClient(host=a.qdrant_host, port=a.qdrant_port)
    default_logicals = [s.collection_name, s.health_collection_name]
    logicals = a.logicals or default_logicals
    out: list[dict] = []
    bad = False

    if a.command == "status":
        embedder = _embedder(model)
        dim = len(embedder.embed_query("dimension probe"))
        rows = qc.inventory(client, model, dim, logicals)
        out = rows
        bad = any(r["state"] == "mismatch" for r in rows)
        if not a.json:
            print(f"embedder: {model}  (probed dimension {dim}, configured {s.embed_dim})")
            for r in rows:
                print(
                    f"  {r['logical']:14} {r['state']:9} {r['physical'] or '-':42} model={r['model'] or 'unrecorded'} "
                    f"dim={r['dim']} points={r['points']}"
                )
                if r["why"]:
                    print(f"      {r['why']}")
    else:
        for logical in logicals:
            if a.command == "run":
                embedder = _embedder(model)
                chunks = (
                    _health_chunks(pathlib.Path(a.docs_dir))
                    if logical == s.health_collection_name
                    else None
                )
                cur = qc.resolve(client, logical)
                res = rb.run(
                    client,
                    logical,
                    model,
                    embedder,
                    chunks=chunks,
                    old_embedder=_embedder(a.old_model)
                    if a.old_model
                    else _old_embedder(cur.model_slug),
                    allow_data_loss=a.allow_data_loss,
                )
            elif a.command == "rollback":
                res = rb.rollback(client, logical)
            else:
                res = rb.drop_old(client, logical)
            out.append(res.__dict__)
            bad |= not res.ok
            if not a.json:
                extra = (
                    ""
                    if res.recall_new is None
                    else f" recall@{res.k} new={res.recall_new:.2f}"
                    + ("" if res.recall_old is None else f" old={res.recall_old:.2f}")
                )
                print(
                    f"  {logical:14} {res.action:13} {res.from_physical or '-'} -> {res.to_physical or '-'}"
                    f"{' points=' + str(res.points) if res.points is not None else ''}{extra}"
                )
                for line in ([res.detail] if res.detail else []) + res.notes:
                    print(f"      {line}")
                if res.snapshot:
                    print(f"      snapshot kept: {res.snapshot}")
    if a.json:
        print(json.dumps(out, indent=2, default=str))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
