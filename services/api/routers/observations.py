"""Read back localAI's durable records: removals (core/source_observations) and LLM drift (core/llm_drift)."""

from fastapi import APIRouter

from core import llm_drift, source_observations

router = APIRouter(prefix="/observations", tags=["observations"])


@router.get("/removals")
async def removals(limit: int = 100, instance: str | None = None) -> dict:
    """Sources and machines localAI removed, newest last; optionally one engine's."""
    rows = source_observations.removals(limit=limit, instance=instance)
    return {"count": len(rows), "removals": rows}


@router.get("/drift")
async def drift(limit: int = 100, label: str | None = None) -> dict:
    """Engine-initiated LLM answers compared across engines, and broadcast divergences.

    Newest last. Every engine's full reading is kept: this is the training corpus
    for the near-miss learning cycles (RealityEngine_CI#518).
    """
    rows = llm_drift.records(limit=limit, label=label)
    return {"count": len(rows), "summary": llm_drift.report(), "records": rows}
