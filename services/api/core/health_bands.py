"""Health bands: grade the HealthKit families the bridge delivers, roll them up.

HEALTH_INTEGRATION_ROADMAP T8, re-based. The personal health machine used to
read three localAI-owned sensors (HR, HRV, sleep) that nothing on a phone ever
fed: the bridge delivers blood-pressure, workout and sleep *families* into the
corpus regions [4320:4344]. It now grades those families.

The set of families is not fixed. It changes through an authorization workflow
tied to the owner's Solid pod, reported by the PE as HealthKit scope
(localHealthkitBridge INGEST_CONTRACT.md, "Scope and resync"). Every band has a
*slot* only while its type is in scope, so this module works over whatever bands
are live rather than a fixed list.

Pure: no I/O. ``health_scope`` does the PE reads and writes around it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Beside data/machines, resolved the way reality_bridge resolves that directory,
# so a deployment that relocates LOCALAI_MACHINES_DIR moves both together.
_BANDS_PATH = (
    Path(
        os.getenv(
            "LOCALAI_MACHINES_DIR",
            # A .parent chain, as reality_bridge uses, not .parents[3]: the default
            # is evaluated even when the variable is set, and in the container
            # (/app/core/...) there is no fourth ancestor to index.
            str(Path(__file__).parent.parent.parent.parent / "data" / "machines"),
        )
    ).parent
    / "health"
    / "health_bands.json"
)

GRADES = ("ok", "watch", "concern")
GRADE_VALUE = {"ok": 1.0, "watch": 0.5, "concern": 0.0}  # the slot sensor encoding
STATES = ("thriving", "balanced", "watch", "attention")


@dataclass(frozen=True)
class Axis:
    index: int
    name: str
    unit: str
    source_range: tuple[float, float] | None
    ok: dict
    watch: dict


@dataclass(frozen=True)
class Band:
    id: str
    hk_type: str
    lane: str | None
    lane_region: tuple[int, int] | None
    confidence_axis: int | None
    axes: tuple[Axis, ...]
    dormant: bool = False


@dataclass
class BandTable:
    bands: tuple[Band, ...]
    min_confidence: float
    slot_offset: int
    slot_capacity: int
    rollup: dict = field(default_factory=dict)

    def for_type(self, hk_type: str) -> list[Band]:
        return [b for b in self.bands if b.hk_type == hk_type and not b.dormant]

    def live_types(self) -> set[str]:
        return {b.hk_type for b in self.bands if not b.dormant}


def load_bands(path: Path = _BANDS_PATH) -> BandTable:
    raw = json.loads(path.read_text())
    bands = []
    for b in raw["bands"]:
        region = b.get("laneRegion")
        bands.append(
            Band(
                id=b["id"],
                hk_type=b["hkType"],
                lane=b.get("lane"),
                lane_region=(region["offset"], region["length"]) if region else None,
                confidence_axis=b.get("confidenceAxis"),
                dormant=bool(b.get("dormant")) or region is None,
                axes=tuple(
                    Axis(
                        index=a["index"],
                        name=a["name"],
                        unit=a["unit"],
                        source_range=tuple(a["sourceRange"]) if a.get("sourceRange") else None,
                        ok=a["ok"],
                        watch=a["watch"],
                    )
                    for a in b["axes"]
                ),
            )
        )
    slot = raw["slotTable"]
    return BandTable(
        bands=tuple(bands),
        min_confidence=float(raw["minConfidence"]),
        slot_offset=int(slot["offset"]),
        slot_capacity=int(slot["capacity"]),
        rollup=raw["rollup"],
    )


def _satisfies(value: float, cond: dict) -> bool:
    checks = {
        "gte": lambda v, t: v >= t,
        "gt": lambda v, t: v > t,
        "lte": lambda v, t: v <= t,
        "lt": lambda v, t: v < t,
    }
    return all(checks[k](value, t) for k, t in cond.items())


def grade_axis_raw(axis: Axis, raw: float) -> str:
    if _satisfies(raw, axis.ok):
        return "ok"
    if _satisfies(raw, axis.watch):
        return "watch"
    return "concern"


def denormalize(axis: Axis, normalized: float) -> float:
    lo, hi = axis.source_range  # type: ignore[misc]
    return lo + normalized * (hi - lo)


def grade_band(band: Band, family: list[float] | None, min_confidence: float) -> str | None:
    """The band's worst axis grade, or None when there is nothing to grade.

    None, not concern: a family that is missing, too short, or below the
    confidence floor says nothing about the owner's health, and grading it as a
    failure would turn absent data into a finding.
    """
    if band.dormant or not family:
        return None
    ci = band.confidence_axis
    if ci is not None and (len(family) <= ci or family[ci] < min_confidence):
        return None
    worst = "ok"
    for axis in band.axes:
        if len(family) <= axis.index or axis.source_range is None:
            return None
        g = grade_axis_raw(axis, denormalize(axis, float(family[axis.index])))
        if GRADES.index(g) > GRADES.index(worst):
            worst = g
    return worst


def grade_raw(band: Band, raw: float) -> str:
    """Grade a single-axis band from a raw reading in its own unit.

    For callers holding a measurement rather than a lane family — the
    health push script and ``push_health_signal``. Dormant bands grade too: dormancy
    means no lane feeds them, not that their thresholds are unknown.
    """
    if len(band.axes) != 1:
        raise ValueError(f"band {band.id} has {len(band.axes)} axes; grade it from its family")
    return grade_axis_raw(band.axes[0], float(raw))


def band(table: BandTable, band_id: str) -> Band:
    return next(b for b in table.bands if b.id == band_id)


def rollup(grades: list[str], table_rollup: dict | None = None) -> str | None:
    """Worst band wins, over however many bands are live.

    any concern → attention; otherwise one watch → balanced, two or more →
    watch; all ok → thriving. No graded bands → None: no state rather than a
    guessed one.
    """
    names = table_rollup or {
        "allOk": "thriving",
        "oneWatch": "balanced",
        "multiWatch": "watch",
        "anyConcern": "attention",
    }
    if not grades:
        return None
    if "concern" in grades:
        return names["anyConcern"]
    watches = grades.count("watch")
    if watches >= 2:
        return names["multiWatch"]
    if watches == 1:
        return names["oneWatch"]
    return names["allOk"]


def rollup_vector(state: str | None) -> list[float]:
    """One-hot over STATES — the personal_health_baseline input window."""
    return [1.0 if state == s else 0.0 for s in STATES]


def allocate_slots(current: dict[str, int], wanted: list[str], capacity: int) -> dict[str, int]:
    """Stable slot allocation: kept bands keep their slot; new ones take the
    lowest free index; bands no longer wanted release theirs. Bands beyond
    capacity get no slot (reported by the caller, never silently merged).
    """
    kept = {b: i for b, i in current.items() if b in wanted}
    used = set(kept.values())
    for band in wanted:
        if band in kept:
            continue
        free = next((i for i in range(capacity) if i not in used), None)
        if free is None:
            continue
        kept[band] = free
        used.add(free)
    return kept
