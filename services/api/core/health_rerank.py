"""Re-rank retrieved documents by the user's health state — HEALTH_INTEGRATION_ROADMAP T10.

A user in `attention` or `watch` should see recovery and intervention guidance
ahead of general material, and a user whose sleep is the problem should see
the sleep guide first. Documents are re-ordered, never dropped or added:
within each group the retriever's similarity order is kept, so a thriving user
(or one with no state) gets exactly what the retriever returned.

Keyed on `metadata["source"]` (`health/<file>.md`, set by
scripts/ingest_health_docs.py), which is stable, rather than on the derived
category string.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# Guidance for the overall state, most relevant first.
STATE_SOURCES: dict[str, tuple[str, ...]] = {
    "attention": ("health_state_interventions.md", "recovery_protocols.md"),
    "watch": ("recovery_protocols.md", "stress_and_hrv.md", "health_state_interventions.md"),
    "balanced": ("sleep_quality.md", "recovery_protocols.md"),
}

# Guidance for a specific band the follower graded watch or concern.
BAND_SOURCES: dict[str, tuple[str, ...]] = {
    "pulse": ("heart_rate_guide.md",),
    "blood_pressure": ("heart_rate_guide.md",),
    "sleep": ("sleep_quality.md",),
    "exercise": ("recovery_protocols.md", "wellness_baselines.md"),
    "hrv": ("hrv_interpretation.md", "stress_and_hrv.md"),
}


def _source_file(doc: Any) -> str:
    meta = getattr(doc, "metadata", None) or {}
    return str(meta.get("source", "")).rsplit("/", 1)[-1]


def priority_sources(state: str | None, flagged: Sequence[tuple[str, str]] = ()) -> list[str]:
    """Source files to promote, in order: concern bands, watch bands, then the state's."""
    order: list[str] = []
    for grade in ("concern", "watch"):
        for band, g in flagged:
            if g == grade:
                order.extend(BAND_SOURCES.get(band, ()))
    order.extend(STATE_SOURCES.get(state or "", ()))
    return list(dict.fromkeys(order))  # first occurrence wins


def rerank(docs: Sequence[Any], state: str | None, flagged: Sequence[tuple[str, str]] = ()) -> list:
    """Stable re-order of ``docs``: promoted sources first, in priority order."""
    rank = {src: i for i, src in enumerate(priority_sources(state, flagged))}
    if not rank:
        return list(docs)
    last = len(rank)
    indexed = list(enumerate(docs))
    indexed.sort(key=lambda p: (rank.get(_source_file(p[1]), last), p[0]))
    return [d for _, d in indexed]


def health_focus() -> tuple[str | None, list[tuple[str, str]]]:
    """(state, [(band, grade)] for bands not ok) for the bound engine. Never raises."""
    try:
        from core import health_scope
        from core.bridge_binding import bind
        from core.reality_bridge import current_health_state

        state = current_health_state()
        target = bind()
        summary = health_scope.last_summary(target["pe_url"]) if target else None
        flagged = [
            (band, v["grade"])
            for band, v in sorted(((summary or {}).get("slots") or {}).items())
            if v.get("grade") in ("watch", "concern")
        ]
        return state, flagged
    except Exception:  # noqa: BLE001 - re-ranking is best-effort, never a failure
        return None, []
