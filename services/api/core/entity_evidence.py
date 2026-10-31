"""Versioned entity evidence projection with canonical freshness on every read.

The canonical document sidecar remains the only document owner. Event sequence
numbers are supplied by its durable event outbox, never by an LLM. Historical
retrieval is explicit; current-approved retrieval fails closed on source lag.
"""

import json
import sqlite3
from pathlib import Path


class ProjectionLag(ValueError):
    pass


class EntityProjection:
    def __init__(self, state, evidence_class="actual"):
        self.evidence_class = evidence_class
        self.path = Path(state) / "entity-projection.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS revisions(entity TEXT, period TEXT, asset TEXT, revision INTEGER, sequence INTEGER, body TEXT, PRIMARY KEY(entity,period,asset,revision))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS heads(entity TEXT, period TEXT, asset TEXT, revision INTEGER, sequence INTEGER, body TEXT, PRIMARY KEY(entity,period,asset))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS canonical_source(id INTEGER PRIMARY KEY CHECK(id=1), store_id TEXT NOT NULL)"
            )

    def db(self):
        return sqlite3.connect(self.path, timeout=60)

    def apply(self, event):
        keys = (event["entityId"], event["period"], event["assetTypeId"])
        sequence, revision = event["sequence"], event["revision"]
        if type(sequence) is not int or sequence < 1 or type(revision) is not int or revision < 1:
            raise ValueError("Positive canonical sequence/revision required")
        if event["status"] not in {"DRAFT", "IN_REVIEW", "APPROVED", "STALE"}:
            raise ValueError("Unknown lifecycle status")
        if event.get("evidenceClass") != self.evidence_class or event.get("synthetic") != (
            self.evidence_class == "synthetic"
        ):
            raise ValueError("Evidence class does not match this isolated projection")
        if event["status"] == "APPROVED" and (
            not event.get("review") or not event.get("evidenceRefs") or not event.get("sourceRefs")
        ):
            raise ValueError("Approval provenance is incomplete")
        body = json.dumps(event, sort_keys=True)
        if not event.get("canonicalStoreId"):
            raise ValueError("Canonical document store identity required")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            source = db.execute("SELECT store_id FROM canonical_source WHERE id=1").fetchone()
            if source and source[0] != event["canonicalStoreId"]:
                raise ProjectionLag(
                    "Canonical document store changed; explicit projection migration required"
                )
            db.execute(
                "INSERT OR IGNORE INTO canonical_source VALUES(1,?)", (event["canonicalStoreId"],)
            )
            prior = db.execute(
                "SELECT sequence,body FROM revisions WHERE entity=? AND period=? AND asset=? AND revision=?",
                (*keys, revision),
            ).fetchone()
            if prior and prior[0] == sequence and prior[1] != body:
                raise ValueError("Conflicting event with same canonical sequence")
            if not prior or sequence > prior[0]:
                db.execute(
                    "INSERT OR REPLACE INTO revisions VALUES(?,?,?,?,?,?)",
                    (*keys, revision, sequence, body),
                )
            head = db.execute(
                "SELECT sequence FROM heads WHERE entity=? AND period=? AND asset=?", keys
            ).fetchone()
            if not head or sequence > head[0]:
                db.execute(
                    "INSERT OR REPLACE INTO heads VALUES(?,?,?,?,?,?)",
                    (*keys, revision, sequence, body),
                )
        return {"sequence": sequence, "projection": "entity-artifact", "status": "RECONCILED"}

    def current(self, entity, period, asset, canonical_fetch):
        with self.db() as db:
            row = db.execute(
                "SELECT body FROM heads WHERE entity=? AND period=? AND asset=?",
                (entity, period, asset),
            ).fetchone()
        if not row:
            raise ProjectionLag("No projected evidence")
        event = json.loads(row[0])
        canonical = canonical_fetch(entity, period, asset)
        if canonical.get("canonicalStoreId") != event["canonicalStoreId"]:
            raise ProjectionLag("Canonical document store identity mismatch")
        head = canonical.get("head") or {}
        if any(head.get(k) != event[k] for k in ("revision", "contentHash", "status")):
            raise ProjectionLag("Projection is behind canonical head")
        if head["status"] != "APPROVED":
            raise ProjectionLag("Current evidence is not approved")
        if (canonical.get("revision") or {}).get("contentHash") != event["contentHash"]:
            raise ProjectionLag("Canonical revision integrity mismatch")
        return {
            "retrievalMode": "structured-current-approved",
            "artifact": canonical["revision"],
            "head": head,
            "projectionSequence": event["sequence"],
            "generationStatus": "NOT_REQUESTED",
            "evidenceClass": self.evidence_class,
        }

    def historical(self, entity, period, asset, revision):
        with self.db() as db:
            row = db.execute(
                "SELECT body FROM revisions WHERE entity=? AND period=? AND asset=? AND revision=?",
                (entity, period, asset, revision),
            ).fetchone()
        if not row:
            raise ProjectionLag("Historical revision not projected")
        return {"retrievalMode": "historical-not-current-evidence", "event": json.loads(row[0])}
