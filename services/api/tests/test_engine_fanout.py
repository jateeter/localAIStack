"""localAI addresses every registered engine, and holds engines in parity to agree.

RealityEngine_CI#363: an interaction no engine initiated used to reach one
engine, so engines in parity diverged on the inputs localAI fed them.
"""

from __future__ import annotations

import pytest

from core import bridge_binding, engine_fanout, reality_bridge

ENGINES = [
    {"re_url": f"http://{n}-re", "pe_url": f"http://{n}-pe", "instance": f"{n}-1", "healthy": True}
    for n in ("scala", "cpp", "lsp")
]


@pytest.fixture
def three_engines(monkeypatch):
    monkeypatch.setattr(
        engine_fanout, "resolve_all_bridge_targets", lambda: [dict(e) for e in ENGINES]
    )
    bridge_binding.set_initiating_instance(None)
    yield
    bridge_binding.set_initiating_instance(None)


class _FakePEs:
    """Records every POST; answers /api/push with a perceptual space per engine."""

    def __init__(self, space_for):
        self.posts: list[tuple[str, dict | None]] = []
        self.space_for = space_for

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kw):
        return _Resp({"sources": []})

    def patch(self, url, **kw):
        return _Resp({})

    def post(self, url, json=None, **kw):
        self.posts.append((url, json))
        if url.endswith("/api/push"):
            pe = url.split("/api/push")[0]
            return _Resp({"globalStep": 1, "step": {"perceptualSpace": self.space_for(pe)}})
        return _Resp({})


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _routing_space(generate: float) -> list[float]:
    ps = [0.0] * (reality_bridge._OUTPUT_ABORT + 1)
    ps[reality_bridge._OUTPUT_GENERATE] = generate
    return ps


def test_unaddressed_interaction_reaches_every_engine(three_engines):
    assert [t["instance"] for t in engine_fanout.interaction_targets()] == [
        "scala-1",
        "cpp-1",
        "lsp-1",
    ]


def test_an_engine_that_names_itself_is_answered_alone(three_engines, monkeypatch):
    monkeypatch.setattr(
        bridge_binding, "resolve_all_bridge_targets", lambda: [dict(e) for e in ENGINES]
    )
    bridge_binding.set_initiating_instance("cpp-1")
    assert [t["instance"] for t in engine_fanout.interaction_targets()] == ["cpp-1"]


def test_identical_values_are_written_to_every_engine(three_engines, monkeypatch):
    fake = _FakePEs(lambda pe: _routing_space(1.0))
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)
    monkeypatch.setattr(reality_bridge, "deactivate_lapsed", lambda *a, **kw: None)
    monkeypatch.setattr(reality_bridge, "activate_sensor_source", lambda *a, **kw: None)

    assert reality_bridge.push_grading_signal(4, 3, 1) == "generate"

    writes = [(u, j["values"]) for u, j in fake.posts if "/api/sensors/localai_rag_grading" in u]
    assert {u.split("/api/")[0] for u, _ in writes} == {e["pe_url"] for e in ENGINES}
    assert len({tuple(v) for _, v in writes}) == 1, "every engine must get the same values"
    assert engine_fanout.parity_report()["counts"]["agreed"] == 1


def test_engines_in_parity_answering_differently_is_recorded(three_engines, monkeypatch):
    fake = _FakePEs(lambda pe: _routing_space(0.0 if pe.startswith("http://lsp") else 1.0))
    monkeypatch.setattr(reality_bridge.httpx, "Client", lambda *a, **kw: fake)
    monkeypatch.setattr(reality_bridge, "deactivate_lapsed", lambda *a, **kw: None)
    monkeypatch.setattr(reality_bridge, "activate_sensor_source", lambda *a, **kw: None)

    # Proceeds on the first engine in instance registry order...
    assert reality_bridge.push_grading_signal(4, 3, 1) == "generate"
    report = engine_fanout.parity_report()
    assert report["counts"]["diverged"] == 1
    # ...and the divergence carries every engine's reading, none as reference.
    assert report["recentDivergences"][0]["readings"] == {
        "scala-1": "generate",
        "cpp-1": "generate",
        "lsp-1": "rewrite",
    }


def test_unhealthy_engines_are_skipped_but_one_is_always_addressed(monkeypatch):
    down = [dict(e, healthy=False) for e in ENGINES]
    monkeypatch.setattr(engine_fanout, "resolve_all_bridge_targets", lambda: down)
    assert [t["instance"] for t in engine_fanout.interaction_targets()] == ["scala-1"]

    mixed = [dict(ENGINES[0], healthy=False), *ENGINES[1:]]
    engine_fanout.reset_cache()
    monkeypatch.setattr(engine_fanout, "resolve_all_bridge_targets", lambda: mixed)
    assert [t["instance"] for t in engine_fanout.interaction_targets()] == ["cpp-1", "lsp-1"]


def test_difference_is_zero_for_parity_and_one_for_a_split_decision():
    assert engine_fanout.difference("generate", "generate") == 0.0
    assert engine_fanout.difference("generate", "rewrite") == 1.0
    assert engine_fanout.difference(0.25, 0.75) == 0.5
    assert engine_fanout.difference(0.0, 3.0) == 1.0
    assert engine_fanout.difference([0.0, 1.0], [0.0, 0.5]) == 0.25
    assert engine_fanout.difference([0.0], [0.0, 1.0]) == 0.5
    assert engine_fanout.difference({"a": 1, "b": "x"}, {"a": 1, "b": "y"}) == 0.5
    assert engine_fanout.difference(None, "watch") == 1.0


def test_spread_is_the_largest_pairwise_difference():
    assert engine_fanout.spread([0.1, 0.1, 0.1]) == 0.0
    assert engine_fanout.spread([0.1, 0.2, 0.6]) == 0.5


def test_parity_report_carries_the_difference_per_label():
    engine_fanout.agree("routing", [("a", "generate"), ("b", "generate")], "rewrite")
    engine_fanout.agree("routing", [("a", "generate"), ("b", "rewrite")], "rewrite")
    engine_fanout.agree("health", [("a", 0.2), ("b", 0.4)], 0.0)
    report = engine_fanout.parity_report()
    assert report["difference"]["routing"] == {"interactions": 2, "mean": 0.5, "max": 1.0}
    assert report["difference"]["health"]["max"] == pytest.approx(0.2)
    assert report["recentDivergences"][-1]["difference"] == pytest.approx(0.2)


def test_no_engines_returns_the_default():
    assert engine_fanout.agree("x", [], "rewrite") == "rewrite"
