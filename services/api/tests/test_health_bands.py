"""
Health bands — HEALTH_INTEGRATION_ROADMAP T7/T8.

  (1) Grading: raw thresholds, de-normalisation, confidence floor, worst axis.
  (2) Roll-up: worst band wins over however many bands are live.
  (3) Slot allocation: stable, lowest free index, bounded.
  (4) T7 parity: every band's lane region and axis source ranges match
      localHealthkitBridge docs/lane-semantics.json — a band that de-normalises
      against a different range than the bridge normalised with grades a
      different reading than the one taken.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from core import health_bands as hb

TABLE = hb.load_bands()
_LANE_SEMANTICS = (
    pathlib.Path(__file__).resolve().parents[4]
    / "localHealthkitBridge"
    / "docs"
    / "lane-semantics.json"
)


def _band(band_id: str) -> hb.Band:
    return hb.band(TABLE, band_id)


def _bp_family(systolic: float, diastolic: float, pulse: float, confidence: float = 1.0):
    return [systolic / 200, diastolic / 120, pulse / 200, confidence]


# ── (1) Grading ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pulse", "grade"),
    [
        (72, "ok"),
        (60, "ok"),
        (100, "ok"),
        (55, "watch"),
        (110, "watch"),
        (45, "concern"),
        (130, "concern"),
    ],
)
def test_pulse_band_grades_from_family(pulse, grade):
    assert hb.grade_band(_band("pulse"), _bp_family(120, 75, pulse), TABLE.min_confidence) == grade


@pytest.mark.parametrize(
    ("sys", "dia", "grade"),
    [
        (118, 76, "ok"),
        (135, 76, "watch"),
        (118, 85, "watch"),
        (150, 76, "concern"),
        (118, 95, "concern"),
    ],
)
def test_blood_pressure_band_is_its_worst_axis(sys, dia, grade):
    assert (
        hb.grade_band(_band("blood_pressure"), _bp_family(sys, dia, 70), TABLE.min_confidence)
        == grade
    )


def test_below_confidence_floor_is_not_graded():
    assert hb.grade_band(_band("pulse"), _bp_family(120, 75, 72, confidence=0.2), 0.5) is None


def test_missing_or_short_family_is_not_graded():
    assert hb.grade_band(_band("pulse"), None, 0.5) is None
    assert hb.grade_band(_band("pulse"), [], 0.5) is None
    assert hb.grade_band(_band("pulse"), [0.5, 0.5], 0.5) is None


def test_pulse_not_measured_is_not_graded():
    """Pulse 0 means not measured (lane-semantics absentValue): a manual Health
    blood-pressure entry has no heart rate and was graded a 0 bpm concern."""
    fam = _bp_family(132, 86, 0)
    assert hb.grade_band(_band("pulse"), fam, TABLE.min_confidence) is None
    # The rest of the family is unaffected.
    assert hb.grade_band(_band("blood_pressure"), fam, TABLE.min_confidence) == "watch"


def test_dormant_band_is_never_graded_from_a_family():
    hrv = _band("hrv")
    assert hrv.dormant
    assert hb.grade_band(hrv, [0.9, 0, 0, 1], 0.5) is None


def test_grade_raw_grades_dormant_single_axis_band():
    assert hb.grade_raw(_band("hrv"), 45) == "ok"
    assert hb.grade_raw(_band("hrv"), 25) == "watch"
    assert hb.grade_raw(_band("hrv"), 15) == "concern"


def test_grade_raw_refuses_multi_axis_band():
    with pytest.raises(ValueError):
        hb.grade_raw(_band("blood_pressure"), 120)


# ── (2) Roll-up ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("grades", "state"),
    [
        ([], None),
        (["ok"], "thriving"),
        (["ok", "ok", "ok", "ok"], "thriving"),
        (["ok", "watch"], "balanced"),
        (["watch", "watch"], "watch"),
        (["watch", "watch", "watch", "ok"], "watch"),
        (["ok", "concern"], "attention"),
        (["watch", "watch", "concern"], "attention"),
    ],
)
def test_rollup_worst_band_wins(grades, state):
    assert hb.rollup(grades, TABLE.rollup) == state


def test_rollup_vector_is_one_hot_or_zero():
    assert hb.rollup_vector("watch") == [0.0, 0.0, 1.0, 0.0]
    assert hb.rollup_vector(None) == [0.0, 0.0, 0.0, 0.0]


# ── (3) Slot allocation ───────────────────────────────────────────────────────


def test_slots_are_stable_and_reuse_the_lowest_free_index():
    s = hb.allocate_slots({}, ["a", "b", "c"], 32)
    assert s == {"a": 0, "b": 1, "c": 2}
    s = hb.allocate_slots(s, ["a", "c"], 32)  # b removed: c keeps its slot
    assert s == {"a": 0, "c": 2}
    s = hb.allocate_slots(s, ["a", "c", "d"], 32)  # d takes b's freed slot
    assert s == {"a": 0, "c": 2, "d": 1}


def test_slots_beyond_capacity_get_none():
    assert hb.allocate_slots({}, ["a", "b", "c"], 2) == {"a": 0, "b": 1}


def test_slot_table_lies_inside_the_localai_band():
    assert TABLE.slot_offset >= 7440
    assert TABLE.slot_offset + TABLE.slot_capacity <= 7440 + 512


# ── (4) T7: lane parity with the bridge ───────────────────────────────────────


@pytest.fixture(scope="module")
def lanes() -> dict:
    if not _LANE_SEMANTICS.exists():
        pytest.skip(f"needs a sibling localHealthkitBridge checkout: {_LANE_SEMANTICS}")
    return {lane["id"]: lane for lane in json.loads(_LANE_SEMANTICS.read_text())["lanes"]}


def test_band_lanes_match_lane_semantics(lanes):
    for band in TABLE.bands:
        if band.dormant:
            continue
        lane = lanes.get(band.lane)
        assert lane, f"band {band.id}: lane {band.lane} not in lane-semantics.json"
        region = (lane["region"]["offset"], lane["region"]["length"])
        assert band.lane_region == region, f"band {band.id}: region {band.lane_region} != {region}"
        axes = {a["index"]: a for a in lane["axes"]}
        for axis in band.axes:
            la = axes.get(axis.index)
            assert la, f"band {band.id}: axis {axis.index} not in lane {band.lane}"
            assert axis.name == la["name"], f"band {band.id}: axis {axis.index} name drift"
            assert axis.absent_value == la.get("absentValue"), (
                f"band {band.id}.{axis.name}: absentValue {axis.absent_value} "
                f"!= lane-semantics {la.get('absentValue')}"
            )
            assert list(axis.source_range) == la["sourceRange"], (
                f"band {band.id}.{axis.name}: sourceRange {list(axis.source_range)} "
                f"!= lane-semantics {la['sourceRange']}"
            )
        if band.confidence_axis is not None:
            assert band.confidence_axis in axes, f"band {band.id}: confidence axis missing"
