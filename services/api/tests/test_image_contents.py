"""The API image carries every file the API reads by default.

The compose stack mounts config/ and data/ from the checkout, so a file the
Dockerfile forgets is invisible there and missing only in the image. That is how
the release image lost the generated per-domain machines: their index,
config/domain-machines.index.json, was never copied, and the bridge skipped all
eleven with a warning (RealityEngine_CI rc3 E1 vs E2).
"""

from __future__ import annotations

import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[3]
DOCKERFILE = REPO / "services" / "api" / "Dockerfile"


def _runtime_stage() -> str:
    text = DOCKERFILE.read_text()
    return text[text.rindex("\nFROM ") :]


def _copies() -> dict[str, str]:
    """destination -> source for every COPY in the runtime stage."""
    found = {}
    for src, dst in re.findall(r"^COPY\s+(?:--\S+\s+)*(\S+)\s+(\S+)\s*$", _runtime_stage(), re.M):
        found[dst] = src
    return found


def _env() -> dict[str, str]:
    stage = _runtime_stage().replace("\\\n", " ")
    env = {}
    for line in re.findall(r"^ENV\s+(.+)$", stage, re.M):
        for key, value in re.findall(r"(\w+)=(\S+)", line):
            env[key] = value
    return env


def test_domain_machine_index_is_in_the_image():
    copies = _copies()
    assert (
        copies.get("/app/config/domain-machines.index.json") == "config/domain-machines.index.json"
    )
    assert _env().get("LOCALAI_DOMAIN_MACHINES_INDEX") == "/app/config/domain-machines.index.json"


def test_every_indexed_domain_machine_is_in_the_image():
    # data/machines/ is copied whole; each file the index names must be under it.
    assert _copies().get("/app/data/machines/") == "data/machines/"
    index = json.loads((REPO / "config" / "domain-machines.index.json").read_text())
    for entry in index["generated"]:
        assert entry["file"].startswith("data/machines/"), entry
        assert (REPO / entry["file"]).is_file(), entry["file"]


def test_model_registry_is_in_the_image():
    assert _copies().get("/app/config/models.registry.json") == "config/models.registry.json"
    assert _env().get("MODELS_REGISTRY_PATH") == "/app/config/models.registry.json"
