"""
Machine prose must name live regions — HEALTH_INTEGRATION_ROADMAP T11.

The eight localAI machines moved by +7388 into [7440:7952], and the numeric
perceptualMapping moved with them; the prose did not. Descriptions kept
pointing at [190:194], ps[112] and the like for months, which is what a
reader (and the RAG index) sees. This fails when a region reference in any
string falls outside the localAI band and is not one of the named exceptions.
"""

from __future__ import annotations

import json
import re

from core import reality_bridge

_BAND = (7440, 7440 + 512)
# References that are correctly outside the band: ai_load_bridge's corpus
# outputs and the corpus AI input window it narrowed from; the HealthKit lanes
# personal_health_baseline's roll-up is graded from; and session_agent_context's
# element-relative slices (indices within its own input, not cells).
_OUTSIDE_OK = {
    "[272:280]",
    "[272:276]",
    "[276:280]",
    "[256:280]",
    "[120:144]",
    "[4320:4344]",
    "[12:14]",
    "[0:4]",
}
_REF = re.compile(r"(?:ps)?\[(\d+)(?::(\d+))?\]")


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def test_machine_prose_names_regions_inside_the_localai_band():
    stale = []
    for path in sorted(reality_bridge._MACHINES_DIR.glob("*.json")):
        for text in _strings(json.loads(path.read_text())):
            for m in _REF.finditer(text):
                lo = int(m.group(1))
                hi = int(m.group(2)) if m.group(2) else lo + 1
                if m.group(0) in _OUTSIDE_OK or (_BAND[0] <= lo and hi <= _BAND[1]):
                    continue
                if not m.group(0).startswith("ps") and m.group(2) is None:
                    continue  # [3] and the like: element indices, not regions
                stale.append(f"{path.name}: {m.group(0)}")
    assert stale == [], "stale region references in machine prose: " + ", ".join(stale)
