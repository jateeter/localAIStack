#!/usr/bin/env python3
"""Reconcile this repo's machine regions against the corpus allocation registry.

`data/machines/*.json` load into the Reality Engine alongside the corpus and
write real positions in the universal vector, but they live here rather than in
RealityEngine_Machines (jateeter/localAIStack#38). Validating their *shape*
against the canonical schema — which `scripts/validate-machines.sh` does — says
nothing about *where* they write. A machine whose region no allocation registry
knows about is a contributor the arbitration registry cannot account for: its
cells are contended-but-undeclared, which is precisely what the corpus gate
exists to prevent.

This script closes that half. It answers one question, mechanically:

    do these machines write any cell the corpus already claims?

Today the answer is no, and not narrowly. The corpus footprint recorded in
`domains/region-allocation.json` starts at cell 1731; these eight occupy
[52:280]. The two do not merely fail to collide, they are in different parts of
the vector entirely. That is worth pinning rather than rediscovering, because
nothing reserves [52:280] on their behalf — the disjointness is a fact about
today's allocations, not a guarantee the registry makes.

So the gate is deliberately two-sided:

  * a localAI write landing on a corpus-declared cell is a FAILURE — that is
    the undeclared contention the issue is about;
  * a localAI write drifting outside the band this repo claims is a WARNING
    surfaced as failure too, because the band is the only thing standing in
    for a reservation the registry does not yet hold.

Only *outputs* are checked for contention. Reading a cell another machine owns
is ordinary composition — `ai_load_bridge` reads [112:120] on purpose. Writing
one is contention.

Usage:
    ./scripts/check_machine_regions.py
    MACHINES_DIR=/path/to/RealityEngine_Machines ./scripts/check_machine_regions.py

Exits non-zero on any violation, and on a missing registry — a gate that cannot
run must say so rather than pass quietly.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
MACHINE_DIR = REPO / "data" / "machines"

# The band this repo claims for its integration machines. Not a reservation the
# corpus registry grants — see the module docstring — but the declared intent
# that makes drift visible.
LOCALAI_BAND = (0, 512)


class Span:
    """A half-open cell range [offset, offset+length) with a label for reporting."""

    __slots__ = ("label", "offset", "length")

    def __init__(self, label: str, offset: int, length: int) -> None:
        self.label = label
        self.offset = offset
        self.length = length

    @property
    def end(self) -> int:
        return self.offset + self.length

    def overlaps(self, other: Span) -> bool:
        return self.offset < other.end and other.offset < self.end

    def __str__(self) -> str:
        return f"[{self.offset}:{self.end}]"


def corpus_registry_path() -> pathlib.Path | None:
    root = os.environ.get("MACHINES_DIR")
    candidates = [pathlib.Path(root)] if root else []
    candidates.append(REPO.parent / "RealityEngine_Machines")
    for candidate in candidates:
        path = candidate / "domains" / "region-allocation.json"
        if path.is_file():
            return path
    return None


def collect_declared_spans(registry: dict) -> list[Span]:
    """Every cell range the corpus registry declares, from all four of its shapes.

    The registry is generated, and its shapes have grown over time, so this walks
    the domain trees structurally rather than reaching for known key paths. A
    shape added later is picked up rather than silently skipped — which for a
    gate is the only safe direction to be wrong in.
    """
    spans: list[Span] = []

    for lane in registry.get("serviceLanes", []):
        spans.append(Span(f"serviceLane:{lane.get('id', '?')}", lane["offset"], lane["length"]))

    for band in registry.get("reservedBands", []):
        spans.append(Span(f"reservedBand:{band.get('id', '?')}", band["offset"], band["length"]))

    def walk(label: str, node: object) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("offset"), int) and isinstance(node.get("length"), int):
                spans.append(Span(label, node["offset"], node["length"]))
            for key, value in node.items():
                walk(f"{label}.{key}", value)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(f"{label}[{index}]", value)

    for domain_name, domain in registry.get("domains", {}).items():
        walk(f"domain:{domain_name}", domain)

    for bus in registry.get("interDomainBuses", []):
        for key in ("inputRegion", "outputRegion"):
            region = bus.get(key)
            if region:
                spans.append(
                    Span(f"bus:{bus.get('id', '?')}.{key}", region["offset"], region["length"])
                )

    for lane in registry.get("sharedOutputLanes", []):
        cells = lane["cells"]
        owner = (lane.get("owners") or ["?"])[0]
        spans.append(Span(f"sharedLane:{owner}", cells["offset"], cells["length"]))

    return spans


def collect_localai_regions() -> tuple[list[Span], list[Span]]:
    """The (inputs, outputs) declared by this repo's machine definitions."""
    inputs: list[Span] = []
    outputs: list[Span] = []
    for path in sorted(MACHINE_DIR.glob("*.json")):
        machine = json.loads(path.read_text(encoding="utf-8"))["machine"]
        mapping = machine.get("perceptualMapping", {})
        for key, bucket in (("input", inputs), ("output", outputs)):
            region = mapping.get(key)
            if not region:
                continue
            bucket.append(Span(f"{path.name}:{key}", region["offset"], region["length"]))
    return inputs, outputs


def main() -> int:
    registry_path = corpus_registry_path()
    if registry_path is None:
        print(
            "[fail] region-allocation.json not found under a sibling RealityEngine_Machines "
            "checkout — set MACHINES_DIR",
            file=sys.stderr,
        )
        return 1

    if not MACHINE_DIR.is_dir():
        print(f"[fail] no machine definitions at {MACHINE_DIR}", file=sys.stderr)
        return 1

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    declared = collect_declared_spans(registry)
    inputs, outputs = collect_localai_regions()

    if not outputs:
        print("[fail] no localAI machine outputs found — nothing to reconcile", file=sys.stderr)
        return 1

    corpus_floor = min(span.offset for span in declared) if declared else None
    band_lo = min(span.offset for span in inputs + outputs)
    band_hi = max(span.end for span in inputs + outputs)

    print(f"check-machine-regions: {len(outputs)} writer(s) against {registry_path}")
    print(f"  corpus declares {len(declared)} span(s), lowest cell {corpus_floor}")
    print(
        f"  localAI occupies [{band_lo}:{band_hi}] (claimed band [{LOCALAI_BAND[0]}:{LOCALAI_BAND[1]}])"
    )

    failures: list[str] = []

    # 1. localAI writers must not contend with each other.
    for i, a in enumerate(outputs):
        for b in outputs[i + 1 :]:
            if a.overlaps(b):
                failures.append(f"localAI writers overlap: {a.label} {a} and {b.label} {b}")

    # 2. localAI writers must not land on a cell the corpus already declares.
    for out in outputs:
        for span in declared:
            if out.overlaps(span):
                failures.append(
                    f"undeclared contention: {out.label} {out} overlaps {span.label} {span}"
                )

    # 3. Everything stays inside the band this repo claims.
    band = Span("localAI band", LOCALAI_BAND[0], LOCALAI_BAND[1] - LOCALAI_BAND[0])
    for region in inputs + outputs:
        if not (band.offset <= region.offset and region.end <= band.end):
            failures.append(
                f"outside the claimed band: {region.label} {region} escapes {band} — "
                "widen LOCALAI_BAND deliberately, or move the machine back"
            )

    if failures:
        print(f"\n[fail] {len(failures)} region violation(s):", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("\n[ok]   no localAI writer contends with a corpus-declared cell")
    return 0


if __name__ == "__main__":
    sys.exit(main())
