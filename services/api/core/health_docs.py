"""The personal-health knowledge documents, chunked for embedding.

One definition shared by ``scripts/ingest_health_docs.py`` (first ingestion) and
``reembed_cli`` (rebuilding the collection under a new embedding model), so the
two cannot chunk the same files differently, and by ``core.rag_seed``, which
makes them the default RAG corpus at startup. The documents are git-tracked under
``data/health/documents/`` and baked into the API image at ``/app/data/health``.

They are read as text. ``UnstructuredMarkdownLoader`` parsed them through an
NLP pipeline whose spaCy model is not in the image: the first load downloaded
and pip-installed ``en_core_web_sm`` into the running container (and hung where
it could not), then took ~18 s for nine files. A startup seed cannot depend on
that, and plain Markdown needs no parser: the leading front matter is dropped,
the ``#`` headings stay (``reembed.derive_queries`` queries by them), and the
same splitter cuts the text.
"""

from __future__ import annotations

import os
import pathlib
import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.rag_seed import SEED_TAG

SPLITTER = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)

_FRONT_MATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)


def read_markdown(md_path: pathlib.Path) -> str:
    """A document's text: UTF-8, without its leading YAML front matter."""
    return _FRONT_MATTER.sub("", md_path.read_text(encoding="utf-8"), count=1).strip()


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
    from langchain_core.documents import Document

    # Chunks of the default corpus carry the seed tag, whichever path writes them
    # (the startup seed, the ingest script, a re-embed), so the seed can tell its own
    # points from a document someone ingested by hand. Another directory is not ours.
    ours = docs_dir.resolve() == default_docs_dir().resolve()
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
            loaded = [
                Document(
                    page_content=read_markdown(md_path),
                    metadata={
                        "source": f"health/{md_path.name}",
                        "category": md_path.stem.replace("_", " "),
                    },
                )
            ]
            chunks = SPLITTER.split_documents(loaded)
            if ours:
                for c in chunks:
                    c.metadata["seed"] = SEED_TAG
            docs.extend(chunks)
            if verbose:
                print(f"    → {len(loaded)} document(s), {len(chunks)} chunk(s)")
        except Exception as exc:
            if verbose:
                print(f"  [warn] Failed to load {md_path.name}: {exc}")
    return docs
