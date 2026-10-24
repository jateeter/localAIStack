"""
Generated per-domain localAI machines (RealityEngine_Machines#219).

scripts/generate_domain_machines.py writes one localai/<domain>_baseline machine
per accepted domain; the reality bridge declares their roll-up sensors and
imports them. What must hold:

  * the output is reproducible, and a stale or hand-edited file is detected;
  * a domain's slot, once assigned, never moves, and the band cannot overflow;
  * the regions are inside the band, clear of the health slots and of each other;
  * each machine does what it says: one-hot in -> that state out, all-zero -> none;
  * the bridge's drift guard, sensors and import cover them.

The tests that need the real manifest read the sibling RealityEngine_Machines
checkout and skip without it (the "Machine contract" CI job has one); the rest
run on a stand-in manifest.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from core import reality_bridge

REPO = pathlib.Path(__file__).resolve().parents[3]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_domain_machines", REPO / "scripts" / "generate_domain_machines.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_generator()

DOMAINS = ["agriculture", "energy", "health-personal", "health-services", "legal-services"]


def _slots_for(domains):
    return gen.plan_slots(domains, {})


def _real_manifest_domains():
    try:
        return gen.manifest_domains()
    except SystemExit:
        pytest.skip("no sibling RealityEngine_Machines checkout (set MACHINES_DIR)")


# ── reproducible output ──────────────────────────────────────────────────────


def test_generation_is_reproducible():
    slots = _slots_for(DOMAINS)
    first = gen.expected_outputs(DOMAINS, slots)
    second = gen.expected_outputs(DOMAINS, slots)
    assert first == second
    for path, text in first.items():
        assert text.endswith("\n"), f"{path.name} has no trailing newline"
        json.loads(text)  # every output is valid JSON


def test_checked_in_files_match_a_regeneration():
    domains = _real_manifest_domains()
    problems = gen.check(domains, gen.load_slots())
    assert problems == [], " | ".join(problems)


def test_a_hand_edited_generated_file_is_detected(tmp_path, monkeypatch):
    domains = _real_manifest_domains()
    out = tmp_path / "generated"
    out.mkdir()
    slots = gen.load_slots()
    for path, text in gen.expected_outputs(domains, slots).items():
        if path.parent == gen.OUT_DIR:
            (out / path.name).write_text(text)
    monkeypatch.setattr(gen, "OUT_DIR", out)
    monkeypatch.setattr(gen, "SLOTS_PATH", tmp_path / "slots.json")
    monkeypatch.setattr(gen, "INDEX_PATH", tmp_path / "index.json")
    (tmp_path / "slots.json").write_text(gen.slots_text(slots))
    (tmp_path / "index.json").write_text(gen.index_text(slots, domains))
    assert gen.check(domains, slots) == []

    victim = next(out.glob("*.json"))
    victim.write_text(victim.read_text().replace('"GREEN"', '"RED"', 1))
    assert any(victim.name in p and "differs" in p for p in gen.check(domains, slots))

    victim.unlink()
    assert any(victim.name in p and "missing" in p for p in gen.check(domains, slots))


# ── slots: stable, bounded ───────────────────────────────────────────────────


def test_existing_slots_never_move_when_a_domain_is_added():
    before = _slots_for(["agriculture", "energy", "legal-services"])
    after = gen.plan_slots(["agriculture", "energy", "legal-services", "aviation"], before)
    for domain, slot in before.items():
        assert after[domain] == slot
    assert after["aviation"] == max(before.values()) + 1


def test_a_removed_domains_slot_is_reported_not_reused():
    slots = _slots_for(["agriculture", "energy"])
    problems = gen.check(["agriculture"], slots)
    assert any("energy" in p and "not an accepted domain" in p for p in problems)


def test_a_new_domain_without_a_slot_is_reported():
    problems = gen.check(["agriculture", "aviation"], {"agriculture": 0})
    assert any("aviation" in p and "no slot" in p for p in problems)


def test_the_band_cannot_overflow():
    too_many = [f"domain-{n:03d}" for n in range(gen.SLOT_CAPACITY + 1)]
    with pytest.raises(SystemExit) as exc:
        gen.plan_slots(too_many, {})
    assert "no free slot" in str(exc.value)
    assert len(gen.plan_slots(too_many[: gen.SLOT_CAPACITY], {})) == gen.SLOT_CAPACITY


def test_handwritten_domains_get_no_slot():
    slots = _slots_for(DOMAINS)
    assert "health-personal" not in slots
    assert set(slots) == set(DOMAINS) - set(gen.HANDWRITTEN)


# ── regions ──────────────────────────────────────────────────────────────────


def test_regions_are_inside_the_band_clear_of_health_slots_and_each_other():
    slots = _slots_for([f"domain-{n:02d}" for n in range(gen.SLOT_CAPACITY)])
    health_slots = gen.health_slot_window()  # from data/health/health_bands.json, the table's owner
    cells: set[int] = set()
    for slot in slots.values():
        inp, out = gen.regions(slot)
        assert out["offset"] == inp["offset"] + inp["length"]
        for region in (inp, out):
            lo, hi = region["offset"], region["offset"] + region["length"]
            assert gen.BAND[0] <= lo and hi <= gen.BAND[1], f"[{lo}:{hi}] outside the band"
            assert hi <= health_slots[0] or lo >= health_slots[1], (
                f"[{lo}:{hi}] on the health slots"
            )
            span = set(range(lo, hi))
            assert not (span & cells), f"[{lo}:{hi}] overlaps another machine"
            cells |= span


def test_the_slot_base_clears_every_hand_written_machine():
    top = 0
    for path in (REPO / "data" / "machines").glob("*.json"):
        pm = json.loads(path.read_text())["machine"]["perceptualMapping"]
        for side in ("input", "output"):
            r = pm[side]
            if gen.BAND[0] <= r["offset"] < gen.BAND[1]:
                top = max(top, r["offset"] + r["length"])
    assert top <= gen.SLOT_BASE


# ── what a generated machine does ────────────────────────────────────────────


@pytest.mark.parametrize("domain", ["agriculture", "legal-services"])
def test_a_generated_machine_maps_one_hot_input_to_the_matching_state(domain):
    machine = gen.build_machine(domain, 3)["machine"]
    s = gen.snake(domain)
    by_id = {seq["id"]: seq for seq in machine["sequences"]}
    assert set(by_id) == {f"{s}-{x}" for x in (*gen.STATES, "none")}

    for index, state in enumerate(gen.STATES):
        event = by_id[f"{s}-{state}"]["events"][0]
        # element[index] must be HIGH (value 1.0), the others wildcards.
        assert [e["value"] for e in event["elements"]] == [
            1.0 if i == index else 0.5 for i in range(4)
        ]
        assert event["outputEvents"][0]["vector"] == [1.0 if i == index else 0.0 for i in range(4)]
    none = by_id[f"{s}-none"]["events"][0]
    assert [e["value"] for e in none["elements"]] == [0.0] * 4
    assert none["outputEvents"][0]["vector"] == [0.0] * 4

    # Each declared input sequence exercises its own sequence and expects its output.
    for inp in machine["inputSequences"]:
        seq = by_id[inp["metadata"]["activeSequence"]]
        expected = json.loads(inp["metadata"]["expectedOutput"])
        assert seq["events"][0]["outputEvents"][0]["vector"] == [float(v) for v in expected]

    rules = machine["metadata"]["triggerConfig"]["rules"]
    assert [r["sequenceId"] for r in rules] == [f"{s}-{x}" for x in (*gen.STATES, "none")]
    assert machine["metadata"]["sequenceCount"] == 5 == len(machine["sequences"])


def test_a_generated_machine_names_its_own_cells():
    machine = gen.build_machine("energy", 6)["machine"]
    inp, out = gen.regions(6)
    assert machine["perceptualMapping"]["input"] == inp
    assert machine["perceptualMapping"]["output"] == out
    sensor = machine["metadata"]["sensorSources"]["localai_energy_rollup"]
    assert sensor["region"] == f"[{inp['offset']}:{inp['offset'] + inp['length']}]"


def test_every_accepted_domain_is_covered_by_a_generated_or_a_hand_written_machine():
    domains = _real_manifest_domains()
    slots = gen.load_slots()
    for domain in domains:
        if domain in gen.HANDWRITTEN:
            for name in gen.HANDWRITTEN[domain]:
                assert (REPO / "data" / "machines" / name).is_file(), f"{domain}: {name} missing"
        else:
            assert domain in slots, f"{domain} has no generated machine"
            assert (gen.OUT_DIR / f"{gen.snake(domain)}_baseline.json").is_file()


# ── the bridge ───────────────────────────────────────────────────────────────


def test_the_drift_guard_covers_the_generated_machines(tmp_path, monkeypatch):
    assert reality_bridge.verify_machine_offsets() == []

    entries = reality_bridge._domain_entries()
    assert entries, "the index describes no generated machines"
    bad = tmp_path / "machines" / "generated"
    bad.mkdir(parents=True)
    victim = entries[0]
    for e in entries:
        path = reality_bridge._domain_machine_path(e)
        data = json.loads(path.read_text())
        if e is victim:
            data["machine"]["perceptualMapping"]["input"]["offset"] += 1
        (bad / path.name).write_text(json.dumps(data))
    monkeypatch.setattr(reality_bridge, "_MACHINES_DIR", tmp_path / "machines")
    mismatches = reality_bridge.verify_machine_offsets()
    assert any(f"{victim['domain'].replace('-', '_')}_baseline.json.input" in m for m in mismatches)


def test_domain_sensors_sit_in_their_machines_input_window():
    sensors = reality_bridge.domain_sensors()
    entries = reality_bridge._domain_entries()
    assert [s["sensorId"] for s in sensors] == [e["sensorId"] for e in entries]
    for sensor, entry in zip(sensors, entries, strict=True):
        machine = json.loads(reality_bridge._domain_machine_path(entry).read_text())["machine"]
        assert sensor["region"] == machine["perceptualMapping"]["input"]
        assert sensor["sensorId"] in machine["metadata"]["sensorSources"]
    ids = [s["sensorId"] for s in sensors]
    assert len(ids) == len(set(ids))


def test_import_domain_machines_hands_every_machine_to_every_engine(monkeypatch):
    seen = {}

    def fake(machines, label):
        seen["machines"], seen["label"] = machines, label
        return True

    monkeypatch.setattr(reality_bridge, "import_machines_everywhere", fake)
    assert reality_bridge.import_domain_machines() is True
    assert seen["label"] == "domain"
    entries = reality_bridge._domain_entries()
    assert [n for n, _ in seen["machines"]] == [e["name"] for e in entries]
    for name, doc in seen["machines"]:
        assert doc["machine"]["name"] == name


def test_import_domain_machines_is_false_without_an_index(tmp_path, monkeypatch):
    monkeypatch.setattr(reality_bridge, "_DOMAIN_INDEX_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(
        reality_bridge, "import_machines_everywhere", lambda *a, **k: pytest.fail("must not import")
    )
    assert reality_bridge.import_domain_machines() is False
    assert reality_bridge.domain_sensors() == []
