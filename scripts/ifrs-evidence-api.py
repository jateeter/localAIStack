#!/usr/bin/env python3
"""Focused development deployment of the same authenticated evidence routes.

Uses the real configured embedder and a persistent local Qdrant collection when
IFRS_QDRANT_PATH is explicit. No legacy engine routes or startup seed are run.
This is a connector verification surface, not composed-universe acceptance.
"""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))
from fastapi import FastAPI
import uvicorn
from routers import domain_evidence

app = FastAPI(title="IFRS evidence connector verification")
app.include_router(domain_evidence.router)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("IFRS_EVIDENCE_PORT", "8791")))
