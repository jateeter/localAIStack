"""Read back the durable record of what localAI removed from engines (core/source_observations)."""

from fastapi import APIRouter

from core import source_observations

router = APIRouter(prefix="/observations", tags=["observations"])


@router.get("/removals")
async def removals(limit: int = 100, instance: str | None = None) -> dict:
    """Sources and machines localAI removed, newest last; optionally one engine's."""
    rows = source_observations.removals(limit=limit, instance=instance)
    return {"count": len(rows), "removals": rows}
