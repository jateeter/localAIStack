"""
Upstream trigger receiver for Reality Engine machine outputs.

Exposes `POST /graphql` with a single mutation — `updateProcessState` — that
machines in the Reality Engine (or any upstream caller) invoke when one of
their outputs changes and needs to signal a contextualised status back into
the local AI.

The schema mirrors the template the user supplied for the machine-side
trigger: a process identifier, a RAG (red/amber/green) status code, and an
optional human-readable description.  Received events are logged and kept in
an in-memory ring buffer accessible via `GET /graphql/events` for quick
verification during development.
"""

from __future__ import annotations

import enum
import time
from collections import deque
from typing import Deque, Optional

import strawberry
import structlog
from fastapi import APIRouter
from strawberry.fastapi import GraphQLRouter

log = structlog.get_logger()


# ── Schema ────────────────────────────────────────────────────────────────────

@strawberry.enum
class RagStatusCode(enum.Enum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"


@strawberry.type
class RagStatus:
    code: RagStatusCode
    description: str


@strawberry.type
class ProcessState:
    id: str
    name: str
    status: str
    rag_status: RagStatus


@strawberry.type
class UpdateProcessStatePayload:
    process_state: ProcessState


@strawberry.input
class UpdateProcessStateInput:
    id: str
    rag_status_code: RagStatusCode
    name: Optional[str] = None
    status: Optional[str] = None
    source_machine: Optional[str] = None
    source_sequence: Optional[str] = None
    context: Optional[str] = None


# Human-readable descriptions paired with each RAG code.  Kept here so callers
# only have to send the code; the receiver fills in the description to keep
# the schema's `RagStatus.description` field populated.
_RAG_DESCRIPTIONS = {
    RagStatusCode.GREEN: "Nominal — no intervention required",
    RagStatusCode.AMBER: "Degraded — monitor and prepare remediation",
    RagStatusCode.RED: "Critical — immediate attention required",
}


# ── In-memory event log ──────────────────────────────────────────────────────
# Small ring buffer so `/graphql/events` can show recent triggers without a
# backing store.  Swap for Redis or Qdrant when persistence is needed.
_EVENTS: Deque[dict] = deque(maxlen=128)


def _record_event(payload: dict) -> None:
    payload["received_at"] = time.time()
    _EVENTS.append(payload)
    log.info("machine_trigger_received", **payload)


# ── Mutations / Queries ──────────────────────────────────────────────────────

@strawberry.type
class Query:
    @strawberry.field
    def recent_triggers(self, limit: int = 20) -> list[str]:
        """Return the most recent trigger events as compact JSON-like strings."""
        return [
            f"{e.get('source_machine','?')}:{e.get('rag_status_code','?')}"
            f"@{e.get('received_at', 0):.0f}"
            for e in list(_EVENTS)[-limit:]
        ]


@strawberry.type
class Mutation:
    @strawberry.mutation
    def update_process_state(
        self, input: UpdateProcessStateInput
    ) -> UpdateProcessStatePayload:
        description = _RAG_DESCRIPTIONS[input.rag_status_code]
        _record_event({
            "id": input.id,
            "name": input.name,
            "status": input.status,
            "rag_status_code": input.rag_status_code.value,
            "source_machine": input.source_machine,
            "source_sequence": input.source_sequence,
            "context": input.context,
        })
        return UpdateProcessStatePayload(
            process_state=ProcessState(
                id=input.id,
                name=input.name or input.id,
                status=input.status or input.rag_status_code.value.lower(),
                rag_status=RagStatus(
                    code=input.rag_status_code,
                    description=description,
                ),
            )
        )


schema = strawberry.Schema(query=Query, mutation=Mutation)


# ── Routers ──────────────────────────────────────────────────────────────────
# GraphQL over HTTP lives at /graphql; a sibling REST helper at /graphql/events
# returns the ring-buffer contents so CI / manual tests can assert on triggers
# without issuing a GraphQL query.
graphql_app: GraphQLRouter = GraphQLRouter(schema)

events_router = APIRouter(prefix="/graphql", tags=["graphql"])


@events_router.get("/events")
async def list_events(limit: int = 50) -> dict:
    limit = max(1, min(limit, _EVENTS.maxlen or 128))
    return {"count": len(_EVENTS), "events": list(_EVENTS)[-limit:]}
