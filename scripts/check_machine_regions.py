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

    do these machines write any cell a corpus machine also writes?

The answer today is yes, five times, and the first version of this script said
no. That is worth recording, because the mistake is easy to repeat.

It reconciled against `domains/region-allocation.json` alone. That registry
records aggregate domain windows, service lanes, buses and shared output lanes
— it does NOT enumerate machine `perceptualMapping` windows. Its lowest declared
cell is 1731, which reads like "the corpus lives above 1731" and is not what it
means. Measured from the corpus directly: 2656 machine regions across 120 merged
blocks, the first of them [0:2047] contiguous. Corpus machines start at cell 0,
right where these eight sit.

So the reconciliation is against corpus machine windows, and the registry check
is kept alongside it rather than in place of it.

What fails:

  * a localAI write landing on a cell a CORPUS MACHINE writes — genuine
    contention, the cell arbiter resolving a conflict no registry declares;
  * a localAI write landing on a registry-declared lane;
  * two localAI writers on one cell;
  * a region escaping the band this repo claims.

What does not fail:

  * write -> read overlaps. Reading a cell another machine writes is ordinary
    composition, and some of it is deliberate — `ai_load_bridge` writes
    [272:280] precisely so AIModelWellness and AIHardwareResilience read it.
    Reported for visibility, never failed.
  * the five collisions in KNOWN_CORPUS_COLLISIONS, held at baseline so they
    cannot grow while the remap is done as separate work (#57).

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
LOCALAI_BAND = (7440, 7952)

# Regions deliberately outside the reserved band.
#
# ai_load_bridge writes [272:280] so AIModelWellness [272:276] and
# AIHardwareResilience [276:280] read it. That is the machine's whole purpose, so
# the write has to land in the corpus's AI input window rather than in this
# repo's band. No corpus machine WRITES those cells, so it is a bridge, not
# contention.
BRIDGE_LANES: set[tuple[str, str]] = {("ai_load_bridge.json", "output")}

# The corpus registry's reserved band FOR these machines. It is a reservation on
# their behalf, not a competing claim, so writing inside it is the intended state
# rather than contention — the one declared span this repo is allowed to occupy.
OUR_RESERVED_BAND_ID = "localaistack-integration"

# Collisions that exist today, frozen so they cannot grow.
#
# (localai file, corpus file, overlap start, overlap end)
#
# These are real: two machines writing the same cells, with the cell arbiter
# resolving contention no registry declares. They are held rather than fixed
# because remapping a machine's output changes what it writes at runtime and is
# reviewable work in its own right — tracked in #57. The set is frozen in both
# directions, so it can only shrink, and only on purpose.
#
# Do not add to this list to make a build pass. A new collision is the failure
# this script exists to report.
KNOWN_CORPUS_COLLISIONS: set[tuple[str, str, int, int]] = set()


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


def collect_corpus_machine_regions(corpus_root: pathlib.Path) -> tuple[list[Span], list[Span]]:
    """The (inputs, outputs) every corpus machine actually declares.

    This is the check that matters, and its absence is why the first version of
    this script was wrong. `region-allocation.json` records aggregate domain
    windows, service lanes, buses and shared output lanes — it does NOT enumerate
    machine `perceptualMapping` windows. Its lowest declared cell is 1731, which
    invites the conclusion that the corpus lives above 1731. It does not: 2656
    machine regions span 120 merged blocks and the first is [0:2047] contiguous.

    Reconciling against the registry alone therefore reported no contention while
    five localAI writers sat on cells corpus machines write.
    """
    inputs: list[Span] = []
    outputs: list[Span] = []
    for path in sorted((corpus_root / "machines").rglob("*.json")):
        try:
            machine = json.loads(path.read_text(encoding="utf-8"))["machine"]
        except (ValueError, KeyError, TypeError):
            continue  # not a machine file; the corpus gate owns that judgement
        mapping = machine.get("perceptualMapping") or {}
        for key, bucket in (("input", inputs), ("output", outputs)):
            region = mapping.get(key)
            if not isinstance(region, dict) or not isinstance(region.get("offset"), int):
                continue
            bucket.append(Span(f"{path.name}:{key}", region["offset"], region["length"]))
    return inputs, outputs


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

    corpus_root = registry_path.parent.parent

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    declared = collect_declared_spans(registry)
    corpus_inputs, corpus_outputs = collect_corpus_machine_regions(corpus_root)
    inputs, outputs = collect_localai_regions()

    if not outputs:
        print("[fail] no localAI machine outputs found — nothing to reconcile", file=sys.stderr)
        return 1
    if not corpus_outputs:
        print(
            f"[fail] no corpus machine windows found under {corpus_root}/machines", file=sys.stderr
        )
        return 1

    banded = [
        span
        for span in inputs + outputs
        if (span.label.partition(":")[0], span.label.partition(":")[2]) not in BRIDGE_LANES
    ]
    band_lo = min(span.offset for span in banded)
    band_hi = max(span.end for span in banded)

    print(f"check-machine-regions: {len(outputs)} writer(s) against {corpus_root}")
    print(f"  corpus: {len(corpus_outputs)} machine writer(s), {len(declared)} declared span(s)")
    print(
        f"  localAI occupies [{band_lo}:{band_hi}] (claimed band [{LOCALAI_BAND[0]}:{LOCALAI_BAND[1]}])"
    )

    failures: list[str] = []
    seen_known: set[tuple[str, str, int, int]] = set()

    # 1. localAI writers must not contend with each other.
    for i, a in enumerate(outputs):
        for b in outputs[i + 1 :]:
            if a.overlaps(b):
                failures.append(f"localAI writers overlap: {a.label} {a} and {b.label} {b}")

    # 2. localAI writers must not land on a cell a CORPUS MACHINE writes. Two
    #    writers on one cell leave the cell arbiter resolving contention that no
    #    registry declares — the condition #38 exists to prevent.
    for out in outputs:
        for other in corpus_outputs:
            if not out.overlaps(other):
                continue
            key = (
                out.label.split(":")[0],
                other.label.split(":")[0],
                max(out.offset, other.offset),
                min(out.end, other.end),
            )
            if key in KNOWN_CORPUS_COLLISIONS:
                seen_known.add(key)
                continue
            failures.append(f"corpus contention: {out.label} {out} overlaps {other.label} {other}")

    # 3. localAI writers must not land on a cell the registry declares as a lane,
    #    except the band reserved for this repo, which they are meant to occupy.
    ours = f"reservedBand:{OUR_RESERVED_BAND_ID}"
    for out in outputs:
        for span in declared:
            if span.label == ours:
                continue
            if out.overlaps(span):
                failures.append(
                    f"undeclared contention: {out.label} {out} overlaps {span.label} {span}"
                )

    # 4. Everything stays inside the band this repo claims.
    band = Span("localAI band", LOCALAI_BAND[0], LOCALAI_BAND[1] - LOCALAI_BAND[0])
    for region in inputs + outputs:
        name, _, kind = region.label.partition(":")
        if (name, kind) in BRIDGE_LANES:
            continue
        if not (band.offset <= region.offset and region.end <= band.end):
            failures.append(
                f"outside the claimed band: {region.label} {region} escapes {band} — "
                "widen LOCALAI_BAND deliberately, or move the machine back"
            )

    # The baseline is frozen in BOTH directions: an entry that no longer collides
    # is reported too, so a fixed collision must be removed from the list rather
    # than left to rot. The baseline can only shrink, and only deliberately.
    stale = KNOWN_CORPUS_COLLISIONS - seen_known
    for entry in sorted(stale):
        failures.append(
            f"stale baseline: {entry[0]} no longer collides with {entry[1]} at "
            f"[{entry[2]}:{entry[3]}] — remove it from KNOWN_CORPUS_COLLISIONS"
        )

    # Write -> read overlaps are composition, not contention, and some are the
    # whole point: ai_load_bridge writes [272:280] precisely so AIModelWellness
    # and AIHardwareResilience read it. Reported, never failed.
    feeds = [(out, reader) for out in outputs for reader in corpus_inputs if out.overlaps(reader)]

    if failures:
        print(f"\n[fail] {len(failures)} region violation(s):", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    if seen_known:
        print(f"\n  {len(seen_known)} known collision(s) held at baseline (see #57):")
        for entry in sorted(seen_known):
            print(f"    {entry[0]} X {entry[1]} at [{entry[2]}:{entry[3]}]")
    if feeds:
        print(f"\n  {len(feeds)} write->read overlap(s), composition not contention:")
        for out, reader in sorted(feeds, key=lambda f: f[0].label)[:6]:
            print(f"    {out.label} {out} -> {reader.label} {reader}")

    print("\n[ok]   no new contention between a localAI writer and a corpus writer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
