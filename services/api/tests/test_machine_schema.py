"""The machine definitions this stack registers must satisfy the canonical schema.

`data/machines/*.json` and the topology machines built at runtime are loaded into
the Reality Engine alongside the corpus and write real positions in the universal
vector — but they live outside RealityEngine_Machines, so the corpus gates never
saw them (jateeter/localAIStack#38).

`scripts/validate-machines.sh` covers the eight on disk and runs in the local
regression lane. It cannot cover the topology machines: those are synthesized by
`core.topology_builder.build_machine_json` from the live LangGraph graphs and do
not exist as files, so they were 2 of the 10 `localai/*` machines the engine
loads with nothing checking their shape. This module is where they get checked,
because here the graph dependencies are importable.

Skips are deliberate and narrow — a missing sibling corpus checkout or missing
graph dependency is an environment fact, not a passing gate. Both are reported
as skips with a reason rather than silently succeeding.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
MACHINE_DIR = REPO / "data" / "machines"


def canonical_schema_dir() -> pathlib.Path | None:
    root = os.environ.get("MACHINES_DIR")
    candidates = [pathlib.Path(root)] if root else []
    candidates.append(REPO.parent / "RealityEngine_Machines")
    for candidate in candidates:
        if (candidate / "schemas" / "machine.schema.json").is_file():
            return candidate / "schemas"
    return None


@pytest.fixture(scope="module")
def machine_validator():
    jsonschema = pytest.importorskip(
        "jsonschema", reason="jsonschema not installed; see requirements-dev.txt"
    )
    schema_dir = canonical_schema_dir()
    if schema_dir is None:
        pytest.skip("canonical machine.schema.json not found; set MACHINES_DIR")
    assert schema_dir is not None  # narrowing; pytest.skip above does not return

    store = {}
    for path in schema_dir.glob("*.schema.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if "$id" in doc:
            store[doc["$id"]] = doc
    schema = json.loads((schema_dir / "machine.schema.json").read_text(encoding="utf-8"))
    resolver = jsonschema.RefResolver(base_uri=schema["$id"], referrer=schema, store=store)
    return jsonschema.Draft202012Validator(schema, resolver=resolver)


def machine_files() -> list[pathlib.Path]:
    return sorted(MACHINE_DIR.glob("*.json"))


@pytest.mark.parametrize("path", machine_files(), ids=lambda p: p.stem)
def test_static_machine_definition_matches_canonical_schema(path, machine_validator):
    doc = json.loads(path.read_text(encoding="utf-8"))
    errors = sorted(machine_validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(
        f"{'/'.join(str(p) for p in e.absolute_path) or '/'}: {e.message}" for e in errors
    )


def synthesized_machines() -> dict:
    """Build the runtime topology machines, or skip if the graphs cannot load."""
    try:
        from core.topology_builder import build_machine_json, compute_bindings

        bindings = compute_bindings()
    except Exception as exc:  # graph deps absent outside the API image
        pytest.skip(f"topology builder unavailable: {type(exc).__name__}: {exc}")
        raise  # unreachable; keeps the names below unambiguously bound
    return {name: build_machine_json(name, binding) for name, binding in bindings.items()}


def test_synthesized_topology_machines_match_canonical_schema(machine_validator):
    machines = synthesized_machines()
    assert machines, "topology builder produced no machines"
    failures = []
    for name, doc in machines.items():
        errors = sorted(machine_validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
        for error in errors:
            location = "/".join(str(p) for p in error.absolute_path) or "/"
            failures.append(f"{name}: {location}: {error.message}")
    assert not failures, "\n".join(failures)
