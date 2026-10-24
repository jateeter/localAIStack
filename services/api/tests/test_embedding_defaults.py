"""
Each role's default model runs on MLX, and one default is written everywhere
(RealityEngine_CI#593).

Owner decision, 2026-10-24: each role's *default* model must run on MLX; any
other model may run on MLX or ggml. These tests need only the registry and the
settings, so they run in every job, including the lightweight E2E unit-test job
that installs no Qdrant or LangChain.
"""

from __future__ import annotations

import pathlib
import re

from config import Settings
from core import model_registry as mr

REPO = pathlib.Path(__file__).resolve().parents[3]


# ── the owner rule ───────────────────────────────────────────────────────────


def test_every_roles_default_declares_the_mlx_runner():
    reg = mr.load_registry(force_refresh=True)
    assert mr.default_runner_problems(reg) == []
    for role in ("llm", "embedding"):
        entry = reg.by_id(reg.default_for(role))
        assert entry is not None and entry.runner == "mlx", role


def test_a_non_default_model_may_run_on_ggml():
    reg = mr.load_registry(force_refresh=True)
    assert reg.by_id("nomic-embed-text").runner == "ggml"
    assert reg.by_id("llama3.1-8b").runner == "ggml"
    assert reg.default_for("embedding") != "nomic-embed-text"
    # ...and that is not a finding: the rule is about defaults only.
    assert mr.default_runner_problems(reg) == []


def test_the_rule_flags_a_ggml_default_and_an_unmeasured_one():
    reg = mr.load_registry(force_refresh=True)
    ggml = reg.model_copy(deep=True)
    ggml.roles["embedding"]["default"] = "nomic-embed-text"
    assert any("nomic-embed-text" in p and "ggml" in p for p in mr.default_runner_problems(ggml))

    unmeasured = reg.model_copy(deep=True)
    unmeasured.by_id("embeddinggemma-2-270m").runner = None
    assert any("None" in p for p in mr.default_runner_problems(unmeasured))

    gone = reg.model_copy(deep=True)
    gone.roles["llm"]["default"] = "no-such-model"
    assert any("not a registered model" in p for p in mr.default_runner_problems(gone))


# ── one default, everywhere it is written down ───────────────────────────────


def test_registry_config_env_compose_and_setup_name_the_same_embedder():
    """The previous default was split three ways (registry: nomic; config.py, setup.sh,
    and a live .env: ternary-bonsai, which the registry itself marks broken)."""
    reg = mr.load_registry(force_refresh=True)
    entry = reg.by_id(reg.default_for("embedding"))
    tag = entry.tag

    assert Settings.model_fields["embed_model"].default == tag
    assert Settings.model_fields["embed_dim"].default == entry.embed_dim

    env = (REPO / ".env.example").read_text()
    assert re.search(rf"^EMBED_MODEL={re.escape(tag)}$", env, re.M)
    assert re.search(rf"^EMBED_DIM={entry.embed_dim}$", env, re.M)
    assert f'EMBED_MODEL: "${{EMBED_MODEL:-{tag}}}"' in (REPO / "docker-compose.yml").read_text()
    assert f'EMBED_MODEL="${{EMBED_MODEL:-{tag}}}"' in (REPO / "scripts" / "setup.sh").read_text()
    assert f"${{EMBED_MODEL:-{tag}}}" in (REPO / "scripts" / "start.sh").read_text()


def test_the_ingest_script_does_not_hard_code_a_dimension():
    """embeddinggemma-2:270m reports 'embedding length 512' and returns 768-dim vectors,
    so a number written in a script is a claim; the dimension is probed."""
    src = (REPO / "scripts" / "ingest_health_docs.py").read_text()
    assert not re.search(r"embed_dim\s*=\s*\d+", src)
    assert "dimension probe" in src
