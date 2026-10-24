"""The personal-health knowledge documents, chunked for embedding.

One definition shared by ``scripts/ingest_health_docs.py`` (first ingestion) and
``reembed_cli`` (rebuilding the collection under a new embedding model), so the
two cannot chunk the same files differently. The documents are git-tracked under
``data/health/documents/`` and baked into the API image at ``/app/data/health``.
"""

from __future__ import annotations

import os
import pathlib

from langchain_text_splitters import RecursiveCharacterTextSplitter

SPLITTER = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)


def default_docs_dir() -> pathlib.Path:
    """``LOCALAI_HEALTH_DOCS_DIR``, else the container layout, else the checkout's."""
    override = os.getenv("LOCALAI_HEALTH_DOCS_DIR")
    if override:
        return pathlib.Path(override)
    parents = pathlib.Path(__file__).resolve().parents
    candidates = [parents[1] / "data" / "health" / "documents"]  # /app/core/ -> /app
    if len(parents) > 3:
        candidates.append(
            parents[3] / "data" / "health" / "documents"
        )  # services/api/core/ -> repo
    return next((p for p in candidates if p.is_dir()), candidates[0])


def load_health_documents(docs_dir: pathlib.Path, *, verbose: bool = True) -> list:
    from langchain_community.document_loaders import UnstructuredMarkdownLoader

    docs: list = []
    md_files = sorted(docs_dir.glob("*.md"))
    if not md_files:
        if verbose:
            print(f"[warn] No .md files found in {docs_dir}")
        return docs

    for md_path in md_files:
        if verbose:
            print(f"  Loading: {md_path.name}")
        try:
            loaded = UnstructuredMarkdownLoader(str(md_path)).load()
            for d in loaded:
                d.metadata["source"] = f"health/{md_path.name}"
                d.metadata["category"] = md_path.stem.replace("_", " ")
            chunks = SPLITTER.split_documents(loaded)
            docs.extend(chunks)
            if verbose:
                print(f"    → {len(loaded)} document(s), {len(chunks)} chunk(s)")
        except Exception as exc:
            if verbose:
                print(f"  [warn] Failed to load {md_path.name}: {exc}")
    return docs
