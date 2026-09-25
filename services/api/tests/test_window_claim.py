"""
The live source claims its window from the bootstrap replay (owner's rule, 2026-09-24).

Ingesting a machine interns its inputSequences as a test source over the
machine's own input window. localAIStack's live sensor writes the same window,
and the replay was assembled after it, so an iPhone reporting *attention*
reached personal_health_baseline as the replay's *watch*. The bootstrap keeps
the replay until the live source wants the window; then it is removed, on that
engine only.
"""

from __future__ import annotations

import pytest

from core import health_scope, pe_sources
from tests.test_health_scope import PE, FakePE, _sleep


def _replay(pe: FakePE, sid: str, name: str, offset: int, length: int) -> None:
    pe.sources[sid] = {
        "id": sid,
        "type": "test",
        "name": name,
        "active": True,
        "region": {"offset": offset, "length": length},
    }


@pytest.fixture(autouse=True)
def _clean():
    health_scope.reset()
    pe_sources.clear_activation_memo()
    yield
    health_scope.reset()


def test_claim_removes_only_overlapping_localai_replays():
    pe = FakePE()
    _replay(pe, "t-health", "localai/personal_health_baseline / 5 sequences", 7574, 4)
    _replay(pe, "t-carekit", "localai/medication_adherence / 5 sequences", 7582, 4)
    _replay(pe, "t-corpus", "AGX051 / 3 sequences", 7574, 4)  # corpus: never ours
    removed = pe_sources.claim_window(pe, PE, {"offset": 7574, "length": 4})
    assert removed == 1
    assert "t-health" not in pe.sources
    assert "t-carekit" in pe.sources, "a non-overlapping localAI replay stays"
    assert "t-corpus" in pe.sources, "a corpus machine's replay is not localAI's to remove"


def test_partial_overlap_claims_the_whole_replay():
    """rag_retrieval [7440:7444] sits inside rag_corrective_cycle's [7440:7448]."""
    pe = FakePE()
    _replay(pe, "t-rag", "localai/rag_corrective_cycle / 4 sequences", 7440, 8)
    assert pe_sources.claim_window(pe, PE, {"offset": 7440, "length": 4}) == 1
    assert "t-rag" not in pe.sources


def test_no_live_value_no_claim():
    """Before the live source has anything to say, the replay stays."""
    pe = FakePE(scope=None)
    _replay(pe, "t-health", "localai/personal_health_baseline / 5 sequences", 7574, 4)
    health_scope.reconcile({"pe_url": PE}, client=pe)  # no HealthKit data
    assert "t-health" in pe.sources


def test_first_live_state_claims_the_rollup_window():
    pe = FakePE(scope=None)
    _replay(pe, "t-health", "localai/personal_health_baseline / 5 sequences", 7574, 4)
    pe.family(4340, _sleep(7.5))
    s = health_scope.reconcile({"pe_url": PE}, client=pe)
    assert s["state"] == "thriving"
    assert "t-health" not in pe.sources


def test_rebootstrapped_replay_is_claimed_again_on_the_next_pass():
    pe = FakePE(scope=None)
    pe.family(4340, _sleep(7.5))
    health_scope.reconcile({"pe_url": PE}, client=pe)
    _replay(pe, "t-again", "localai/personal_health_baseline / 5 sequences", 7574, 4)
    health_scope.reconcile({"pe_url": PE}, client=pe)
    assert "t-again" not in pe.sources


def test_activation_claims_the_sensor_window():
    """Any live localAI sensor (RAG, CareKit), not only the health roll-up."""
    pe = FakePE()
    _replay(pe, "t-carekit", "localai/medication_adherence / 5 sequences", 7582, 4)
    pe.sources["s-med"] = {
        "id": "s-med",
        "type": "sensor",
        "sensorId": "localai_carekit_med_adherence",
        "region": {"offset": 7582, "length": 1},
        "active": False,
        "lastValue": [1.0],
    }
    pe_sources.activate_sensor_source(pe, PE, "localai_carekit_med_adherence")
    assert "t-carekit" not in pe.sources
    assert pe.sources["s-med"]["active"] is True


def test_claim_never_raises():
    class _Down:
        def get(self, *_a, **_k):
            raise RuntimeError("PE down")

    assert pe_sources.claim_window(_Down(), PE, {"offset": 7574, "length": 4}) == 0
