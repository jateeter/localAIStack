"""T10: re-rank retrieved documents by health state (core/health_rerank.py)."""

from __future__ import annotations

from types import SimpleNamespace

from core import health_rerank as hr


def _docs(*files):
    return [SimpleNamespace(metadata={"source": f"health/{f}"}, name=f) for f in files]


def _names(docs):
    return [d.name for d in docs]


GENERAL = ("wearable_metrics.md", "nutrition_and_recovery.md", "recovery_protocols.md")


def test_no_state_or_thriving_is_a_noop():
    docs = _docs(*GENERAL, "health_state_interventions.md")
    assert _names(hr.rerank(docs, None)) == _names(docs)
    assert _names(hr.rerank(docs, "thriving")) == _names(docs)


def test_attention_puts_interventions_then_recovery_first():
    docs = _docs(*GENERAL, "health_state_interventions.md")
    assert _names(hr.rerank(docs, "attention")) == [
        "health_state_interventions.md",
        "recovery_protocols.md",
        "wearable_metrics.md",
        "nutrition_and_recovery.md",
    ]


def test_flagged_band_outranks_the_state_and_concern_outranks_watch():
    docs = _docs("recovery_protocols.md", "sleep_quality.md", "heart_rate_guide.md", "x.md")
    out = hr.rerank(docs, "attention", [("sleep", "watch"), ("pulse", "concern")])
    assert _names(out)[:3] == ["heart_rate_guide.md", "sleep_quality.md", "recovery_protocols.md"]


def test_reorders_never_drops_or_adds_and_is_stable():
    docs = _docs("a.md", "sleep_quality.md", "b.md", "sleep_quality.md", "c.md")
    out = hr.rerank(docs, "balanced")
    assert sorted(map(id, out)) == sorted(map(id, docs))
    assert _names(out) == ["sleep_quality.md", "sleep_quality.md", "a.md", "b.md", "c.md"]
    assert out[0] is docs[1] and out[1] is docs[3]  # similarity order kept within a group


def test_documents_without_source_metadata_are_left_in_place():
    docs = [SimpleNamespace(metadata={}, name="m"), *_docs("sleep_quality.md")]
    assert _names(hr.rerank(docs, "balanced")) == ["sleep_quality.md", "m"]


def test_every_mapped_source_exists_in_the_health_corpus():
    import pathlib

    corpus = pathlib.Path(__file__).resolve().parents[3] / "data" / "documents" / "health"
    mapped = {f for v in (*hr.STATE_SOURCES.values(), *hr.BAND_SOURCES.values()) for f in v}
    missing = sorted(f for f in mapped if not (corpus / f).exists())
    assert missing == [], f"re-rank names documents that do not exist: {missing}"


def test_every_live_band_has_guidance():
    from core.health_bands import load_bands

    bands = {b.id for b in load_bands().bands}
    assert bands <= set(hr.BAND_SOURCES), sorted(bands - set(hr.BAND_SOURCES))


def test_health_focus_reads_the_followers_flagged_bands(monkeypatch):
    from core import health_scope, reality_bridge

    monkeypatch.setattr(reality_bridge, "current_health_state", lambda: "watch")
    monkeypatch.setattr("core.bridge_binding.bind", lambda: {"pe_url": "http://pe"})
    monkeypatch.setattr(
        health_scope,
        "last_summary",
        lambda url: {"slots": {"pulse": {"grade": "watch"}, "sleep": {"grade": "ok"}}},
    )
    assert hr.health_focus() == ("watch", [("pulse", "watch")])


def test_health_focus_never_raises(monkeypatch):
    from core import reality_bridge

    def boom():
        raise RuntimeError("RE down")

    monkeypatch.setattr(reality_bridge, "current_health_state", boom)
    assert hr.health_focus() == (None, [])
