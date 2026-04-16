#!/usr/bin/env python3
"""
Ingest documents into the local RAG knowledge base.

Usage:
  python scripts/ingest.py path/to/doc.pdf
  python scripts/ingest.py path/to/docs/     # ingest entire directory
  python scripts/ingest.py --text "raw text" --source "label"
"""

import sys
import os
import argparse
import json
from pathlib import Path

try:
    import httpx
except ImportError:
    print("httpx not found. Run: pip install httpx")
    sys.exit(1)

API_BASE = os.getenv("API_BASE", "http://localhost:8000")


def ingest_file(path: Path, client: httpx.Client):
    with open(path, "rb") as f:
        resp = client.post(
            f"{API_BASE}/rag/ingest/file",
            files={"file": (path.name, f, "application/octet-stream")},
            timeout=120,
        )
    resp.raise_for_status()
    result = resp.json()
    print(f"  [ok] {path.name} → {result['ingested_chunks']} chunks")
    return result


def ingest_text(text: str, source: str, client: httpx.Client):
    resp = client.post(
        f"{API_BASE}/rag/ingest/text",
        json={"text": text, "source": source},
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"  [ok] text ({source}) → {result['ingested_chunks']} chunks")
    return result


def main():
    parser = argparse.ArgumentParser(description="Ingest documents into localAIStack RAG")
    parser.add_argument("paths", nargs="*", help="File or directory paths to ingest")
    parser.add_argument("--text", help="Raw text to ingest")
    parser.add_argument("--source", default="cli", help="Source label for raw text")
    args = parser.parse_args()

    if not args.paths and not args.text:
        parser.print_help()
        sys.exit(1)

    with httpx.Client() as client:
        # Health check
        try:
            h = client.get(f"{API_BASE}/health", timeout=5)
            h.raise_for_status()
        except Exception as e:
            print(f"API not reachable at {API_BASE}: {e}")
            sys.exit(1)

        if args.text:
            ingest_text(args.text, args.source, client)

        for path_str in args.paths:
            p = Path(path_str)
            if p.is_dir():
                supported = {".pdf", ".txt", ".md", ".docx"}
                files = [f for f in p.rglob("*") if f.suffix.lower() in supported]
                print(f"Ingesting {len(files)} files from {p}/")
                for f in sorted(files):
                    try:
                        ingest_file(f, client)
                    except Exception as e:
                        print(f"  [err] {f.name}: {e}")
            elif p.is_file():
                try:
                    ingest_file(p, client)
                except Exception as e:
                    print(f"  [err] {p.name}: {e}")
            else:
                print(f"  [skip] not found: {path_str}")


if __name__ == "__main__":
    main()
