#!/usr/bin/env python3
"""
Ingest personal health knowledge documents into the health_docs Qdrant collection.

This script loads the Markdown files from data/documents/health/ and adds them
to the 'health_docs' vector store, enabling the health_search tool in the agent
graph to answer health-related questions with grounded context.

Usage
-----
  # From the repo root (requires the Python env activated and Qdrant running):
  cd services/api && python ../../scripts/ingest_health_docs.py

  # With explicit paths:
  python scripts/ingest_health_docs.py --docs-dir data/documents/health \
      --qdrant-host localhost --qdrant-port 4333

Options
  --docs-dir PATH     Directory containing .md health documents  [default: data/documents/health]
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
_REPO_ROOT   = _SCRIPT_DIR.parent
_SERVICES_API = _REPO_ROOT / "services" / "api"

if str(_SERVICES_API) not in sys.path:
    sys.path.insert(0, str(_SERVICES_API))

# ── Imports (require services/api deps to be installed) ───────────────────────

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.document_loaders import UnstructuredMarkdownLoader
    from langchain_qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams
except ImportError as exc:
    print(f"[error] Missing dependency: {exc}")
    print("  Run: cd services/api && pip install -r requirements.txt")
    sys.exit(1)

SPLITTER = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)


def get_embeddings(model_name: str):
    try:
        from langchain_ollama import OllamaEmbeddings
        return OllamaEmbeddings(model=model_name)
    except ImportError:
        from langchain_community.embeddings import OllamaEmbeddings  # type: ignore
        return OllamaEmbeddings(model=model_name)


def load_health_documents(docs_dir: pathlib.Path) -> list:
    docs = []
    md_files = sorted(docs_dir.glob("*.md"))
    if not md_files:
        print(f"[warn] No .md files found in {docs_dir}")
        return docs

    for md_path in md_files:
        print(f"  Loading: {md_path.name}")
        try:
            loader = UnstructuredMarkdownLoader(str(md_path))
            loaded = loader.load()
            for d in loaded:
                d.metadata["source"] = f"health/{md_path.name}"
                d.metadata["category"] = md_path.stem.replace("_", " ")
            chunks = SPLITTER.split_documents(loaded)
            docs.extend(chunks)
            print(f"    → {len(loaded)} document(s), {len(chunks)} chunk(s)")
        except Exception as exc:
            print(f"  [warn] Failed to load {md_path.name}: {exc}")

    return docs


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--docs-dir",     default=str(_REPO_ROOT / "data" / "documents" / "health"))
    parser.add_argument("--qdrant-host",  default="localhost")
    parser.add_argument("--qdrant-port",  type=int, default=4333)
    parser.add_argument("--collection",   default="health_docs")
    parser.add_argument("--embed-model",  default=None,
                        help="Ollama embedding model (default: read from config.py / .env)")
    parser.add_argument("--clear",        action="store_true",
                        help="Drop and recreate the collection before ingesting")
    args = parser.parse_args()

    # Resolve embed model — prefer CLI arg, then settings, then fallback default
    if args.embed_model:
        embed_model = args.embed_model
    else:
        try:
            from config import get_settings
            embed_model = get_settings().embed_model
        except Exception:
            embed_model = "ternary-bonsai:4"

    embed_dim = 768  # must match the model's output dimension

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

    if args.clear and args.collection in collections:
        print(f"  [--clear] Dropping collection: {args.collection}")
        client.delete_collection(args.collection)
        collections = [c.name for c in client.get_collections().collections]

    if args.collection not in collections:
        print(f"  Creating collection: {args.collection} (dim={embed_dim}, COSINE)")
        client.create_collection(
            collection_name=args.collection,
            vectors_config=VectorParams(size=embed_dim, distance=Distance.COSINE),
        )

    print("\n[3/3] Embedding and ingesting chunks …")
    try:
        embeddings = get_embeddings(embed_model)
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
    print('  curl -s http://localhost:4000/graph/agent \\')
    print('    -H "Content-Type: application/json" \\')
    print('    -d \'{"messages": [{"role":"user","content":"What does low HRV mean?"}]}\'')
    print()


if __name__ == "__main__":
    main()
