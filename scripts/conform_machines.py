#!/usr/bin/env python3
"""Bring data/machines/*.json under the canonical RealityEngine machine schema.

These eight definitions load into the RE alongside the corpus and write real
positions in the universal vector, but no schema gate had ever seen them
(jateeter/localAIStack#38). Against schemas/machine.schema.json in
RealityEngine_Machines they failed on four points, identically in all eight:

    /machine/arbiterRule            must be "PASSTHROUGH"
    /machine/metadata               missing "governance"
    /machine/metadata               missing "triggerConfig"
    /machine/metadata               missing "machineClass"

This script is the one-shot transform that fixed them, kept so the edit is
reproducible and reviewable rather than a hand-edit nobody can re-derive. It is
idempotent: running it against conformant files changes nothing.

Two of the four are pure additions. The other two deserve explanation.

arbiterRule OR -> PASSTHROUGH is behaviour-preserving, not a semantic change.
Verified in all three runtimes:

    C++    Or: withOutput > 0            Passthrough: !all.empty()
    LSP    or: (> sequences-with-output 0)  default: all-outputs non-empty
    Scala  OR: sequencesWithOutput > 0   PASSTHROUGH: outputList.nonEmpty

A concatenation of per-sequence outputs is non-empty exactly when some sequence
produced output, so the two predicates agree on every input. Only AND differs.
All 1,328 corpus machines already declare PASSTHROUGH.

triggerConfig is *not* inert metadata, which is why its values are conservative.
The runtimes join it into arbitration: C++ carries `merge.governance->ragStatusCode`
onto each contribution, and the SEVERITY rule resolves contended cells by that
code. Authoring a severity here would therefore change arbitration outcomes for
any cell these machines contend. Every rule is emitted GREEN/info — the
least-severe pair — so bringing these files under the schema does not silently
move a resolution. Per-sequence severities are a domain-owner decision and are
deliberately left for one; see the issue.

Each rule's outputMatches is read from the sequence's own declared output
vector, so the rules describe what the machine already does rather than
asserting something new.
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
MACHINE_DIR = REPO / "data" / "machines"

# machineClass is a closed enum in machine-class.schema.json. These assignments
# follow what each machine does: ai_load_bridge projects one region's signals
# into another and is the one true bridge; the rest observe a source and assert
# a classification, which is signal-monitor.
MACHINE_CLASS = {
    "agent_activity_classifier": "signal-monitor",
    "ai_load_bridge": "bridge",
    "medication_adherence": "signal-monitor",
    "personal_health_baseline": "signal-monitor",
    "rag_corrective_cycle": "signal-monitor",
    "session_agent_context": "signal-monitor",
    "session_health_context": "signal-monitor",
    "session_rag_context": "signal-monitor",
}

PROCESS_NAME = {
    "agent_activity_classifier": "LocalAI Agent Activity Classifier",
    "ai_load_bridge": "LocalAI Load Bridge",
    "medication_adherence": "LocalAI Medication Adherence",
    "personal_health_baseline": "LocalAI Personal Health Baseline",
    "rag_corrective_cycle": "LocalAI RAG Corrective Cycle",
    "session_agent_context": "LocalAI Session Agent Context",
    "session_health_context": "LocalAI Session Health Context",
    "session_rag_context": "LocalAI Session RAG Context",
}


def governance(stem: str) -> dict:
    return {
        "schemaVersion": "1.0.0",
        "ownerTeam": "localaistack",
        "runbook": f"https://runbooks.example.org/localai/{stem.replace('_', '-')}",
        "escalationPolicy": "slack:#localaistack",
        "contact": {
            "primary": "localaistack-primary@example.org",
            "secondary": "localaistack-secondary@example.org",
        },
        # Null across the board: these machines carry no agreed response time.
        # Stating one here would invent an obligation nobody signed up to.
        "sla": {"ok": None, "info": None, "warning": None, "error": None},
        "notes": (
            "localAIStack integration machine, registered into the RE at runtime "
            "by the reality bridge rather than loaded from the corpus."
        ),
    }


def observed_values(machine: dict) -> set[float]:
    """Every value the machine's own vectors carry — elements and outputs."""
    seen: set[float] = set()
    for sequence in machine.get("sequences") or []:
        for vector in sequence.get("vectors") or []:
            for element in vector.get("elements") or []:
                if isinstance(element.get("value"), (int, float)):
                    seen.add(float(element["value"]))
            for ov in vector.get("outputVectors") or []:
                for value in ov.get("vector") or []:
                    if isinstance(value, (int, float)):
                        seen.add(float(value))
    return seen


def bits_per_element(machine: dict) -> int:
    """Derive element width from evidence, per SEMANTIC_GUARDRAIL_CONTRACT.md.

        machine-native-binary   1   {0,1}
        machine-native-ordinal  4   {0..3}
        machine-native-scalar   8   0..1 continuous

    The contract is explicit that this comes from the values the machine's own
    sequence vectors actually contain, not from a label and not from a guess.
    Note the engines default to 8 when the field is absent, so declaring 1 for a
    genuinely binary machine changes what /api/storage-footprint reports for it.
    That metric is the point of the field; behaviour does not read it.
    """
    values = observed_values(machine)
    if not values:
        return 8
    if values <= {0.0, 1.0}:
        return 1
    if values <= {0.0, 1.0, 2.0, 3.0}:
        return 4
    return 8


def sequence_outputs(sequence: dict) -> list[list[float]]:
    out = []
    for vector in sequence.get("vectors") or []:
        for ov in vector.get("outputVectors") or []:
            if ov.get("vector") is not None:
                out.append(ov["vector"])
    return out


def trigger_config(stem: str, machine: dict) -> dict:
    rules = []
    for sequence in machine.get("sequences") or []:
        outputs = sequence_outputs(sequence)
        if not outputs:
            # A sequence that emits nothing cannot be matched on an output, and
            # a rule with no outputMatches would be a claim about a signal that
            # does not exist.
            continue
        seq_meta = sequence.get("metadata") or {}
        rules.append(
            {
                "sequenceId": sequence.get("id"),
                "outputMatches": outputs[0],
                # GREEN/info deliberately — see the module docstring. This value
                # reaches the arbiter through the 4.3.1 governance join.
                "ragStatusCode": "GREEN",
                "processStatus": "info",
                "description": seq_meta.get("description")
                or sequence.get("name")
                or sequence.get("id"),
            }
        )
    return {
        "processId": stem.upper().replace("_", ""),
        "processName": PROCESS_NAME[stem],
        "rules": rules,
    }


def conform(path: pathlib.Path) -> bool:
    doc = json.loads(path.read_text(encoding="utf-8"))
    machine = doc["machine"]
    stem = path.stem
    before = json.dumps(doc, sort_keys=True)

    machine["arbiterRule"] = "PASSTHROUGH"
    mapping = machine.get("perceptualMapping")
    if isinstance(mapping, dict) and "bitsPerElement" not in mapping:
        mapping["bitsPerElement"] = bits_per_element(machine)
    meta = machine.setdefault("metadata", {})
    meta.setdefault("machineClass", MACHINE_CLASS[stem])
    meta.setdefault("governance", governance(stem))
    if "triggerConfig" not in meta:
        meta["triggerConfig"] = trigger_config(stem, machine)

    after = json.dumps(doc, sort_keys=True)
    if before == after:
        return False
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def main() -> int:
    files = sorted(MACHINE_DIR.glob("*.json"))
    if not files:
        print(f"conform-machines: no machine definitions under {MACHINE_DIR}", file=sys.stderr)
        return 2
    unknown = [f.stem for f in files if f.stem not in MACHINE_CLASS]
    if unknown:
        # A new definition must be classified deliberately. Defaulting it would
        # put an unreviewed machineClass on a machine that writes the vector.
        print(f"conform-machines: no machineClass mapping for {unknown} — add one", file=sys.stderr)
        return 2
    changed = [f.name for f in files if conform(f)]
    print(
        f"conform-machines: {len(changed)} changed, {len(files) - len(changed)} already conformant"
    )
    for name in changed:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
