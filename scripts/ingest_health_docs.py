#!/usr/bin/env python3
"""
Ingest personal health knowledge documents into the health_docs Qdrant collection.

This script loads the Markdown files from data/health/documents/ and adds them
to the 'health_docs' vector store, enabling the health_search tool in the agent
graph to answer health-related questions with grounded context.

Usage
-----
  # From the repo root (requires the Python env activated and Qdrant running):
  cd services/api && python ../../scripts/ingest_health_docs.py

  # With explicit paths:
  python scripts/ingest_health_docs.py --docs-dir data/health/documents \
      --qdrant-host localhost --qdrant-port 4333

Options
  --docs-dir PATH     Directory containing .md health documents  [default: data/health/documents]
  --qdrant-host HOST  Qdrant host                                [default: localhost]
  --qdrant-port PORT  Qdrant port                                [default: 4333]
  --collection NAME   Target collection name                     [default: health_docs]
  --embed-model NAME  Ollama embedding model                     [default: from .env / config.py]
  --clear             Drop and recreate the collection before ingesting

The script is idempotent when --clear is not set — re-running adds the documents
again, which creates duplicate embeddings. Use --clear for a clean rebuild.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# ── Allow running from repo root or scripts/ ─────────────────────────────────

_SCRIPT_DIR = pathlib.Path(__file__).parent
_REPO_ROOT = _SCRIPT_DIR.parent
_SERVICES_API = _REPO_ROOT / "services" / "api"

if str(_SERVICES_API) not in sys.path:
    sys.path.insert(0, str(_SERVICES_API))

# ── Imports (require services/api deps to be installed) ───────────────────────

try:
    from langchain_qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient

    from core.health_docs import SPLITTER, load_health_documents  # noqa: F401  (shared with reembed_cli)
except ImportError as exc:
    print(f"[error] Missing dependency: {exc}")
    print("  Run: cd services/api && pip install -r requirements.txt")
    sys.exit(1)


def get_embeddings(model_name: str):
    # OLLAMA_BASE_URL as the service uses it; without it the client dials
    # localhost, which is wrong everywhere but a bare host run.
    import os

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=model_name, base_url=base_url)
    except ImportError:
        from langchain_community.embeddings import OllamaEmbeddings  # type: ignore

        return OllamaEmbeddings(model=model_name, base_url=base_url)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--docs-dir", default=str(_REPO_ROOT / "data" / "health" / "documents"))
    parser.add_argument("--qdrant-host", default="localhost")
    parser.add_argument("--qdrant-port", type=int, default=4333)
    parser.add_argument("--collection", default="health_docs")
    parser.add_argument(
        "--embed-model",
        default=None,
        help="Ollama embedding model (default: read from config.py / .env)",
    )
    parser.add_argument(
        "--clear", action="store_true", help="Drop and recreate the collection before ingesting"
    )
    args = parser.parse_args()

    # Resolve embed model — prefer CLI arg, then settings, then fallback default
    if args.embed_model:
        embed_model = args.embed_model
    else:
        try:
            from config import get_settings

            embed_model = get_settings().embed_model
        except Exception:
            embed_model = "embeddinggemma-2:270m"  # the registry's default embedder

    print("=" * 60)
    print("  localAIStack — Health Docs Ingestion")
    print("=" * 60)
    print(f"  docs-dir:   {args.docs_dir}")
    print(f"  qdrant:     {args.qdrant_host}:{args.qdrant_port}")
    print(f"  collection: {args.collection}")
    print(f"  model:      {embed_model}")
    print()

    docs_dir = pathlib.Path(args.docs_dir)
    if not docs_dir.exists():
        print(f"[error] docs-dir does not exist: {docs_dir}")
        sys.exit(1)

    print("[1/3] Loading health documents …")
    chunks = load_health_documents(docs_dir)
    if not chunks:
        print("[error] No chunks to ingest. Exiting.")
        sys.exit(1)
    print(f"  Total: {len(chunks)} chunks ready for ingestion")

    print("\n[2/3] Connecting to Qdrant …")
    try:
        client = QdrantClient(host=args.qdrant_host, port=args.qdrant_port)
        collections = [c.name for c in client.get_collections().collections]
        print(f"  Qdrant connected. Existing collections: {collections}")
    except Exception as exc:
        print(f"[error] Cannot connect to Qdrant at {args.qdrant_host}:{args.qdrant_port}: {exc}")
        sys.exit(1)

    # The dimension is what the model returns, not a number written here: a model
    # whose metadata says one thing and whose output says another is the case this
    # exists for (embeddinggemma-2:270m reports 512 and returns 768).
    embeddings = get_embeddings(embed_model)
    embed_dim = len(embeddings.embed_query("dimension probe"))
    print(f"  embedding dimension (probed): {embed_dim}")

    from core import collections as qc

    if args.clear:
        res = qc.resolve(client, args.collection)
        if res.physical:
            print(f"  [--clear] Dropping {res.kind} collection: {res.physical}")
            client.delete_collection(res.physical)
            if res.kind == "alias":
                from qdrant_client.models import DeleteAlias, DeleteAliasOperation

                client.update_collection_aliases(
                    [DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=args.collection))]
                )
    # --collection is a logical name: an alias onto <name>__<model>. An existing
    # collection filled by a different model raises CollectionMismatch here, with
    # the command that migrates it, instead of mixing vectors that do not mix.
    try:
        res = qc.ensure(client, args.collection, embed_model, embed_dim)
    except qc.CollectionMismatch as exc:
        print(f"[error] {exc}")
        sys.exit(1)
    print(f"  Collection: {args.collection} -> {res.physical} (dim={embed_dim}, COSINE)")

    print("\n[3/3] Embedding and ingesting chunks …")
    try:
        store = QdrantVectorStore(
            client=client,
            collection_name=args.collection,
            embedding=embeddings,
        )
        ids = store.add_documents(chunks)
        print(f"  Ingested {len(ids)} chunks into '{args.collection}'")
    except Exception as exc:
        print(f"[error] Ingestion failed: {exc}")
        sys.exit(1)

    print()
    print("Verify ingestion:")
    print(f"  curl http://{args.qdrant_host}:{args.qdrant_port}/collections/{args.collection}")
    print()
    print("Test health_search tool (agent endpoint):")
    print("  curl -s http://localhost:4000/graph/agent \\")
    print('    -H "Content-Type: application/json" \\')
    print('    -d \'{"messages": [{"role":"user","content":"What does low HRV mean?"}]}\'')
    print()


if __name__ == "__main__":
    main()
